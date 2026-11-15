#!/usr/bin/env python3
"""Read-only local scanner, baseline snapshot differ, pre-flight concurrency guard,
and quarantine stager.

Uses only the Python 3.10+ standard library. Never deletes files, uninstalls apps,
or modifies system services. The only mutating subcommand is `quarantine`, which
moves explicitly selected (`--ids`), preflight-safe paths into a quarantine batch
with a restore script; Git data, governance docs, launch items, update/firmware
advisories and worktrees are always refused.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import plistlib
import re
import shlex
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

SYSTEM_LIBRARY_IGNORE_PREFIXES = (
    "com.apple.",
    "apple",
    "clouddocs",
    "mobilemesync",
    "crashreporter",
    "caches",
    "familycircle",
    "knowledge",
    "metadata",
    "passkit",
    "routined",
    "spotlight",
    " Group Containers",
    "group.com.apple.",
)

COMMON_SHARED_TOOL_TOKENS = {
    "google",
    "microsoft",
    "homebrew",
    "pip",
    "npm",
    "pnpm",
    "yarn",
    "cargo",
    "rustup",
    "uv",
    "playwright",
    "ms-playwright",
    "claude",
    "codex",
    "antigravity",
    "cursor",
    "orca",
    "git",
    "gh",
    "node",
    "python",
    "xcode",
    "swift",
    "llvm",
    "typescript",
}

# High-frequency generic words excluded from installed-app vendor_tokens so orphan leftovers are not masked.
GENERIC_APP_VENDOR_STOPWORDS = {
    "com",
    "org",
    "net",
    "io",
    "co",
    "me",
    "tv",
    "us",
    "uk",
    "de",
    "fr",
    "jp",
    "cn",
    "app",
    "apps",
    "application",
    "mac",
    "macos",
    "osx",
    "darwin",
    "apple",
    "group",
    "pro",
    "plus",
    "lite",
    "mini",
    "max",
    "ultra",
    "free",
    "beta",
    "alpha",
    "lts",
    "studio",
    "helper",
    "agent",
    "daemon",
    "service",
    "services",
    "client",
    "server",
    "desktop",
    "mobile",
    "web",
    "native",
    "player",
    "viewer",
    "reader",
    "editor",
    "writer",
    "maker",
    "builder",
    "cloud",
    "drive",
    "sync",
    "box",
    "backup",
    "storage",
    "pdf",
    "doc",
    "docs",
    "note",
    "notes",
    "mail",
    "chat",
    "video",
    "audio",
    "music",
    "photo",
    "media",
    "dev",
    "devel",
    "tools",
    "tool",
    "toolkit",
    "util",
    "utils",
    "utility",
    "utilities",
    "manager",
    "launcher",
    "updater",
    "installer",
    "uninstaller",
    "cleaner",
    "monitor",
    "suite",
    "hub",
    "center",
    "central",
    "core",
    "base",
    "main",
    "host",
    "runner",
    "worker",
    "extension",
    "plugin",
    "widget",
    "menubar",
    "status",
    "tray",
    "panel",
    "bar",
    "browser",
    "terminal",
    "code",
    "work",
    "office",
    "home",
    "team",
    "one",
}

BUILD_DIR_PATTERNS = (
    "target",
    "build",
    ".build",
    ".build-*",
    "DerivedData",
    "DerivedData-*",
    "*-builds",
    "*-preservation",
    "node_modules",
    "dist",
    ".next",
)

# Signed release archives and verification preservation dirs require manual confirmation (tier C, unchecked).
MANUAL_CONFIRM_BUILD_PATTERNS = (
    "*-builds",
    "*-preservation",
)

VALID_UNINSTALL_MODES = {"keep_data", "purge_all"}

# Candidates that are advisories or in-place fixes: never moved into quarantine.
NON_MOVABLE_CATEGORIES = {
    "git_pack_bloat": "Git pack 是仓库对象库，移走会损坏仓库；需要瘦身时在无并发写入时运行普通 `git gc`。",
    "governance_doc": "治理文档（AGENTS.md / CLAUDE.md 一类）只能就地修改，不能搬进隔离区。",
    "launch_item": "启动项需用户确认后用 launchctl 卸载，不在隔离区批量搬移。",
    "app_update": "应用更新检查只给出升级建议，没有可搬移的文件。",
    "env_update": "开发环境更新检查只给出升级建议，没有可搬移的文件。",
    "peripheral_firmware": "外设与固件健康项只给出处理建议，没有可搬移的文件。",
    "backup_exclusion_drift": "备份排除规则漂移要改备份脚本的排除规则，不是搬移目录。",
}
NON_MOVABLE_ACTION_TYPES = {"optimize", "update", "upgrade"}
GOVERNANCE_DOC_NAMES = {"agents.md", "claude.md", "gemini.md", "review.md", "contributing.md", "readme.md"}
WORKTREE_REFUSAL = "worktree 类候选交给 repo-cleanup 技能按退役流程处理，不进隔离区。"
NON_FS_REFUSAL = "不是本机文件系统路径（远端或领域工具动作），不能搬进隔离区。"
LOCAL_QUARANTINE_FALLBACK = "~/.Cleanup_Quarantine"

# Same pre-merge declaration format that git-ship parses (scripts/git_ship.py CHECK_DECL_RE).
PREMERGE_DECL_FILES = ("AGENTS.md", "CLAUDE.md")
PREMERGE_DECL_RE = re.compile(
    r"^\s*(?:(?:[-*+]|\d+[.)])\s+|>\s*)?(?:\*\*)?合入前检查(?:\*\*)?\s*[:：]\s*(?:\*\*\s*)?(?P<value>.+?)\s*$"
)
PREMERGE_NONE_RE = re.compile(r"^(无(需(检查)?)?|不需要(检查)?|none)(?!\w)", re.I)
PREMERGE_NOTE_START = "（(。；;."


class ConfigError(Exception):
    """Raised when topology configuration is missing, malformed, or invalid."""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def expand_path(raw: str) -> Path:
    return Path(os.path.expanduser(raw)).resolve(strict=False)


def load_topology(config_path: str | Path) -> dict[str, Any]:
    """Load and validate topology configuration in fail-closed mode."""
    path = Path(config_path)
    if not path.exists() or not path.is_file():
        raise ConfigError(f"Configuration file not found: {path}")
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception as exc:
        raise ConfigError(f"Failed to parse configuration JSON at {path}: {exc}") from exc

    if (
        not isinstance(data, dict)
        or isinstance(data.get("version"), bool)
        or data.get("version") != SCHEMA_VERSION
    ):
        raise ConfigError(
            f"Unsupported or missing configuration version in {path} (expected {SCHEMA_VERSION})"
        )

    whitelist = data.get("whitelist")
    if not isinstance(whitelist, dict):
        raise ConfigError(f"Missing or invalid 'whitelist' object in {path}")

    for list_key in ("paths", "repos", "app_names"):
        val = whitelist.get(list_key, [])
        if not isinstance(val, list) or not all(isinstance(x, str) and x.strip() for x in val):
            raise ConfigError(f"Invalid 'whitelist.{list_key}' in {path}: must be a list of non-empty strings")

    guest_val = whitelist.get("guest_ids", [])
    if not isinstance(guest_val, list) or not all(
        (isinstance(x, str) and x.strip()) or (isinstance(x, int) and not isinstance(x, bool) and x >= 0)
        for x in guest_val
    ):
        raise ConfigError(
            f"Invalid 'whitelist.guest_ids' in {path}: must be a list of non-empty strings or non-negative integers"
        )

    notes = whitelist.get("notes", {})
    if not isinstance(notes, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in notes.items()
    ):
        raise ConfigError(f"Invalid 'whitelist.notes' in {path}: must be a string-to-string map")

    _quarantine_root_entries(data)

    return data


def _normalize_repo_slug(raw: str) -> str:
    """Normalize a repo reference ('Owner/Repo', HTTPS/SSH git URL, or plain repo name) to lowercase."""
    s = raw.strip()
    if not s:
        return ""
    if s.lower().endswith(".git"):
        s = s[:-4]
    s = s.rstrip("/")
    # SSH SCP-like syntax: git@github.com:owner/repo
    m_scp = re.match(r"^[^/@\s]+@[^:/\s]+:([^/\s]+/[^/\s]+)$", s)
    if m_scp:
        return m_scp.group(1).lower()
    # URL syntax: https://github.com/owner/repo or ssh://git@host/owner/repo
    m_url = re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://[^/]+/(.+)$", s)
    if m_url:
        tail = [seg for seg in m_url.group(1).split("/") if seg]
        if len(tail) >= 2:
            return f"{tail[-2]}/{tail[-1]}".lower()
    if s.lower().startswith("github:"):
        s = s[7:].strip("/")
    parts = [seg.strip() for seg in s.split("/") if seg.strip()]
    if len(parts) >= 2:
        return f"{parts[-2]}/{parts[-1]}".lower()
    return parts[0].lower() if parts else ""


def _normalize_guest_token(raw: Any) -> set[str]:
    """Normalize a numeric or string guest/container identifier into canonical comparison tokens."""
    s = str(raw if raw is not None else "").strip().lower()
    if not s:
        return set()
    tokens = {s}
    m = re.match(r"^(?:pve:)?(?:vm|ct|lxc|qemu)[-_/:]?(\d+)$", s)
    if m:
        num = m.group(1)
        tokens.update({num, f"vm-{num}", f"ct-{num}", f"lxc/{num}", f"qemu/{num}"})
    elif s.isdigit():
        tokens.update({f"vm-{s}", f"ct-{s}", f"lxc/{s}", f"qemu/{s}"})
    return tokens


def build_whitelist_index(config: dict[str, Any]) -> dict[str, Any]:
    wl = config.get("whitelist", {})
    raw_paths = wl.get("paths", [])
    resolved_paths: list[Path] = []
    inode_keys: set[tuple[int, int]] = set()
    notes_by_path: dict[str, str] = {}
    raw_notes = wl.get("notes", {})

    for raw in raw_paths:
        p = expand_path(raw)
        resolved_paths.append(p)
        note = raw_notes.get(raw) or raw_notes.get(str(p)) or ""
        if note:
            notes_by_path[str(p)] = note
        try:
            st = os.stat(p, follow_symlinks=False)
            inode_keys.add((st.st_dev, st.st_ino))
        except OSError:
            pass

    app_tokens = {normalize_token(name) for name in wl.get("app_names", []) if normalize_token(name)}

    repos: set[str] = set()
    notes_by_repo: dict[str, str] = {}
    for raw_r in wl.get("repos", []):
        slug = _normalize_repo_slug(str(raw_r))
        if slug:
            repos.add(slug)
            note = raw_notes.get(str(raw_r)) or raw_notes.get(slug) or ""
            if note:
                notes_by_repo[slug] = note

    guest_ids: set[str] = set()
    guest_tokens: set[str] = set()
    notes_by_guest: dict[str, str] = {}
    for raw_g in wl.get("guest_ids", []):
        g_str = str(raw_g).strip()
        if g_str:
            guest_ids.add(g_str.lower())
            toks = _normalize_guest_token(g_str)
            guest_tokens.update(toks)
            note = raw_notes.get(g_str) or raw_notes.get(g_str.lower()) or ""
            if note:
                for t in toks:
                    notes_by_guest[t] = note

    return {
        "paths": resolved_paths,
        "inode_keys": inode_keys,
        "notes_by_path": notes_by_path,
        "notes_by_repo": notes_by_repo,
        "notes_by_guest": notes_by_guest,
        "raw_notes": raw_notes,
        "app_tokens": app_tokens,
        "repos": repos,
        "guest_ids": guest_ids,
        "guest_tokens": guest_tokens,
    }


parse_whitelist_index = build_whitelist_index


def _extract_git_remote_repos(repo_dir: Path) -> list[str]:
    """Read .git/config (or linked worktree commondir config) without spawning git to find <owner>/<repo> slugs."""
    git_marker = repo_dir / ".git"
    config_files: list[Path] = []
    try:
        if git_marker.is_dir():
            config_files.append(git_marker / "config")
        elif git_marker.is_file():
            raw = git_marker.read_text(encoding="utf-8", errors="replace").strip()
            if raw.startswith("gitdir:"):
                gitdir = (repo_dir / raw.split(":", 1)[1].strip()).resolve(strict=False)
                config_files.append(gitdir / "config")
                commondir_file = gitdir / "commondir"
                if commondir_file.is_file():
                    common_rel = commondir_file.read_text(encoding="utf-8", errors="replace").strip()
                    if common_rel:
                        common_dir = (gitdir / common_rel).resolve(strict=False)
                        config_files.append(common_dir / "config")
    except OSError:
        return []

    slugs: list[str] = []
    for cfg in config_files:
        if not cfg.is_file():
            continue
        try:
            text = cfg.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            m = re.match(r"^\s*url\s*=\s*(.+?)\s*$", line)
            if m:
                slug = _normalize_repo_slug(m.group(1))
                if "/" in slug and slug not in slugs:
                    slugs.append(slug)
    return slugs


def check_repo_whitelisted(
    repo_or_spec: str | Path | None,
    wl_index: dict[str, Any],
    item: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """Check whether a local workspace repo or remote GitHub repo candidate matches whitelist.repos."""
    wl_repos: set[str] = wl_index.get("repos", set())
    if not wl_repos:
        return False, ""

    notes_by_repo: dict[str, str] = wl_index.get("notes_by_repo", {})
    qualified_slugs: set[str] = set()
    plain_names: set[str] = set()
    parent_qualified: set[str] = set()

    if item:
        for key in ("repo", "full_name", "repo_name", "repository"):
            val = str(item.get(key) or "").strip()
            if val:
                slug = _normalize_repo_slug(val)
                if "/" in slug:
                    qualified_slugs.add(slug)
                    plain_names.add(slug.split("/", 1)[1])
                elif slug:
                    plain_names.add(slug)
        if str(item.get("domain") or "").lower() == "github":
            for key in ("path_or_spec", "path", "spec"):
                val = str(item.get(key) or "").strip()
                if val and not val.startswith(("/", "~", ".")):
                    slug = _normalize_repo_slug(val)
                    if "/" in slug:
                        qualified_slugs.add(slug)
                        plain_names.add(slug.split("/", 1)[1])
                    elif slug:
                        plain_names.add(slug)

    if repo_or_spec is not None:
        raw_str = str(repo_or_spec).strip()
        if raw_str:
            is_local_like = isinstance(repo_or_spec, Path) or raw_str.startswith(("/", "~", "."))
            if is_local_like:
                p = expand_path(raw_str)
                # Walk up to enclosing git repo if p is a nested build/pack/doc path inside a repo
                curr = p
                repo_root: Path | None = None
                for cand_dir in (curr, *curr.parents):
                    if cand_dir in (Path("/"), Path("/Users"), Path("/Volumes"), Path("/tmp"), Path("/private/tmp")):
                        break
                    try:
                        if (cand_dir / ".git").exists():
                            repo_root = cand_dir
                            break
                    except OSError:
                        continue
                if repo_root is not None:
                    rname = repo_root.name.strip().lower()
                    if rname:
                        plain_names.add(rname)
                    if repo_root.parent.name:
                        parent_qualified.add(f"{repo_root.parent.name.strip().lower()}/{rname}")
                    for rslug in _extract_git_remote_repos(repo_root):
                        qualified_slugs.add(rslug)
                        plain_names.add(rslug.split("/", 1)[1])
            else:
                # Only treat non-filesystem strings as repo specs when repo/github context applies
                domain = str((item or {}).get("domain") or "").lower()
                cat = str((item or {}).get("category") or "").lower()
                if domain in ("github", "projects") or "repo" in cat or "git" in cat or "/" in raw_str:
                    if ":" not in raw_str or raw_str.lower().startswith(("github:", "https://", "http://", "ssh://", "git@")):
                        slug = _normalize_repo_slug(raw_str)
                        if "/" in slug:
                            qualified_slugs.add(slug)
                            plain_names.add(slug.split("/", 1)[1])
                        elif slug:
                            plain_names.add(slug)

    if not qualified_slugs and not plain_names:
        return False, ""

    for wl_entry in sorted(wl_repos):
        if "/" in wl_entry:
            if wl_entry in qualified_slugs or wl_entry in parent_qualified:
                note = notes_by_repo.get(wl_entry, f"Protected by whitelist.repos ({wl_entry})")
                return True, note
            # Pure local repo without any configured git remote URL: allow matching the <repo> part
            if not qualified_slugs:
                _, wl_repo_only = wl_entry.split("/", 1)
                if wl_repo_only in plain_names:
                    note = notes_by_repo.get(wl_entry, f"Protected by whitelist.repos ({wl_entry})")
                    return True, note
        else:
            if wl_entry in plain_names:
                note = notes_by_repo.get(wl_entry, f"Protected by whitelist.repos ({wl_entry})")
                return True, note

    return False, ""


def _is_guest_candidate_context(item: dict[str, Any] | None, raw_path: str) -> bool:
    if item:
        for k in ("guest_id", "vmid", "ctid", "container_id", "guest_name", "container_name"):
            if item.get(k) is not None and str(item.get(k)).strip():
                return True
        domain = str(item.get("domain") or "").strip().lower()
        if domain in ("nas", "homelab", "pve", "docker"):
            return True
        cat = str(item.get("category") or "").strip().lower()
        if any(tok in cat for tok in ("guest", "vm", "lxc", "container", "docker", "pve", "nas")):
            return True
    s = raw_path.strip().lower()
    if s and not s.startswith(("/", "~", ".")):
        if s.startswith(("pve:", "docker:", "lxc/", "qemu/", "vm-", "ct-", "nas:")):
            return True
    return False


def check_guest_whitelisted(
    item: dict[str, Any] | None,
    raw_path: str,
    wl_index: dict[str, Any],
) -> tuple[bool, str]:
    """Check whether a remote VM/LXC/Docker/NAS candidate matches whitelist.guest_ids."""
    wl_guest_ids: set[str] = wl_index.get("guest_ids", set())
    wl_guest_tokens: set[str] = wl_index.get("guest_tokens", set())
    if not wl_guest_ids or not _is_guest_candidate_context(item, raw_path):
        return False, ""

    notes_by_guest: dict[str, str] = wl_index.get("notes_by_guest", {})
    cand_tokens: set[str] = set()

    if item:
        for id_key in ("guest_id", "vmid", "ctid", "container_id"):
            val = item.get(id_key)
            if val is not None and str(val).strip():
                cand_tokens.update(_normalize_guest_token(val))
        for name_key in ("name", "guest_name", "container_name"):
            val = str(item.get(name_key) or "").strip()
            if val:
                cand_tokens.update(_normalize_guest_token(val))
        title = str(item.get("title") or "").strip()
        if title:
            for m in re.finditer(r"(?<![a-z0-9])(?:vm|ct|lxc|qemu|#)\s*[-_/:]?(\d+)(?![a-z0-9])", title.lower()):
                cand_tokens.update(_normalize_guest_token(m.group(1)))

    specs_to_inspect = [raw_path]
    if item:
        for spec_key in ("path_or_spec", "path", "spec"):
            v = str(item.get(spec_key) or "").strip()
            if v and v not in specs_to_inspect:
                specs_to_inspect.append(v)

    for spec in specs_to_inspect:
        s = spec.strip()
        if not s:
            continue
        sl = s.lower()
        cand_tokens.update(_normalize_guest_token(sl))
        if ":" in sl and not sl.startswith(("/", "~")):
            lhs, rhs_raw = sl.split(":", 1)
            rhs = rhs_raw.strip("/")
            if lhs in ("pve", "docker", "lxc", "qemu", "vm", "ct", "nas", "guest", "homelab") and rhs:
                cand_tokens.update(_normalize_guest_token(rhs))
                last_seg = rhs.rsplit("/", 1)[-1]
                if last_seg:
                    cand_tokens.update(_normalize_guest_token(last_seg))
        elif "/" in sl and not sl.startswith(("/", "~")):
            last_seg = sl.rsplit("/", 1)[-1]
            if last_seg:
                cand_tokens.update(_normalize_guest_token(last_seg))
        for m in re.finditer(r"(?<![a-z0-9])(?:vm|ct|lxc|qemu)[-_/:]?(\d+)(?![a-z0-9])", sl):
            cand_tokens.update(_normalize_guest_token(m.group(0)))
            cand_tokens.update(_normalize_guest_token(m.group(1)))

    matched = cand_tokens & wl_guest_tokens
    if matched:
        hit = sorted(matched)[0]
        note = notes_by_guest.get(hit, f"Protected by whitelist.guest_ids ({hit})")
        return True, note

    return False, ""


def apply_candidate_whitelist(cand: dict[str, Any], wl_index: dict[str, Any]) -> dict[str, Any]:
    """Ensure repo and guest whitelist rules mark matching candidates whitelisted=True and default_checked=False."""
    raw_spec = str(cand.get("path_or_spec") or cand.get("path") or "")
    if not cand.get("whitelisted"):
        repo_wl, repo_note = check_repo_whitelisted(raw_spec or None, wl_index, item=cand)
        if repo_wl:
            cand["whitelisted"] = True
            cand["whitelist_note"] = repo_note
    if not cand.get("whitelisted"):
        guest_wl, guest_note = check_guest_whitelisted(cand, raw_spec, wl_index)
        if guest_wl:
            cand["whitelisted"] = True
            cand["whitelist_note"] = guest_note
    if cand.get("whitelisted"):
        cand["default_checked"] = False
    return cand


def check_path_whitelisted(target: Path, wl_index: dict[str, Any]) -> tuple[bool, str]:
    """Return (is_whitelisted, reason_or_note). Checks both path ancestry and device/inode."""
    resolved = target.resolve(strict=False)
    try:
        st = os.stat(target, follow_symlinks=False)
        if (st.st_dev, st.st_ino) in wl_index["inode_keys"]:
            note = wl_index["notes_by_path"].get(str(resolved), "Matched whitelist device/inode")
            return True, note
    except OSError:
        pass

    for wl_path in wl_index["paths"]:
        if resolved == wl_path or wl_path in resolved.parents or resolved in wl_path.parents:
            note = wl_index["notes_by_path"].get(str(wl_path), f"Protected by whitelist path {wl_path}")
            return True, note

    return False, ""


def bounded_dir_size(
    root: Path,
    max_entries: int = 25000,
    max_seconds: float = 5.0,
) -> tuple[int, bool, int]:
    """Compute logical byte size without following symlinks. Returns (bytes, truncated, recent_mtime)."""
    total = 0
    seen = 0
    truncated = False
    latest_mtime = 0
    deadline = time.monotonic() + max_seconds
    visited_inodes: set[tuple[int, int]] = set()

    try:
        root_st = os.lstat(root)
        latest_mtime = int(root_st.st_mtime)
        if not stat.S_ISDIR(root_st.st_mode):
            return root_st.st_size, False, latest_mtime
    except OSError:
        return 0, True, 0

    stack = [root]
    while stack:
        if time.monotonic() > deadline or seen >= max_entries:
            truncated = True
            break
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    seen += 1
                    if seen > max_entries or time.monotonic() > deadline:
                        truncated = True
                        break
                    try:
                        st = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    mtime = int(st.st_mtime)
                    if mtime > latest_mtime:
                        latest_mtime = mtime
                    if stat.S_ISDIR(st.st_mode):
                        stack.append(Path(entry.path))
                    elif stat.S_ISREG(st.st_mode):
                        key = (st.st_dev, st.st_ino)
                        if key not in visited_inodes:
                            visited_inodes.add(key)
                            total += st.st_size
        except OSError:
            truncated = True

    return total, truncated, latest_mtime


def _read_plist_safe(plist_path: Path) -> dict[str, Any] | None:
    if not plist_path.is_file():
        return None
    try:
        with plist_path.open("rb") as fp:
            info = plistlib.load(fp)
        return info if isinstance(info, dict) else None
    except Exception:
        try:
            proc = subprocess.run(
                ["/usr/bin/plutil", "-convert", "json", "-o", "-", "--", str(plist_path)],
                capture_output=True,
                text=True,
                timeout=4,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                data = json.loads(proc.stdout)
                return data if isinstance(data, dict) else None
        except Exception:
            pass
    return None


def is_host_scan_config(config: dict[str, Any]) -> bool:
    """Return True when config points at real macOS host roots rather than isolated unit test fixtures."""
    for key in ("app_roots", "library_roots"):
        for raw in config.get(key, []):
            s = str(raw).strip()
            if s.startswith(("~/", "/Applications", "/Library", "/System/Volumes/Data")):
                return True
    return False


def _clean_app_name_words(raw_name: str) -> tuple[str, ...]:
    s = raw_name.strip()
    if s.lower().endswith(".app"):
        s = s[:-4]
    words = [w.lower() for w in re.split(r"[^a-zA-Z0-9]+", s) if w]
    return tuple(words)


def _is_meaningful_vendor_token(raw_word: str) -> bool:
    nt = normalize_token(raw_word)
    if len(nt) < 3 or nt.isdigit():
        return False
    if nt in GENERIC_APP_VENDOR_STOPWORDS or raw_word.strip().lower() in GENERIC_APP_VENDOR_STOPWORDS:
        return False
    return True


def collect_installed_apps(app_roots: list[str]) -> dict[str, Any]:
    """Collect bundle IDs, normalized names, vendor tokens, and detailed metadata from installed .app bundles."""
    bundle_ids: set[str] = set()
    norm_names: set[str] = set()
    vendor_tokens: set[str] = set(COMMON_SHARED_TOOL_TOKENS)
    apps_detail: list[dict[str, Any]] = []
    seen_app_paths: set[str] = set()

    for raw_root in app_roots:
        root = expand_path(raw_root)
        if not root.is_dir():
            continue
        # Scan depth 1 and 2 for *.app
        candidates: list[Path] = []
        try:
            for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
                if child.suffix.lower() == ".app":
                    candidates.append(child)
                elif child.is_dir() and not child.is_symlink():
                    try:
                        for sub in sorted(child.iterdir(), key=lambda p: p.name.lower()):
                            if sub.suffix.lower() == ".app":
                                candidates.append(sub)
                    except OSError:
                        pass
        except OSError:
            continue

        for app_dir in candidates:
            if app_dir.is_symlink():
                continue
            app_key = str(app_dir.resolve(strict=False))
            if app_key in seen_app_paths:
                continue
            seen_app_paths.add(app_key)

            stem_norm = normalize_token(app_dir.stem)
            if stem_norm:
                norm_names.add(stem_norm)

            plist_path = app_dir / "Contents" / "Info.plist"
            if not plist_path.is_file():
                plist_path = app_dir / "Info.plist"
            info = _read_plist_safe(plist_path) or {}

            bid_orig = str(info.get("CFBundleIdentifier") or "").strip()
            bid = bid_orig.lower()
            app_norm_tokens: set[str] = {stem_norm} if stem_norm else set()
            clean_name_tuples: set[tuple[str, ...]] = set()
            stem_words = _clean_app_name_words(app_dir.stem)
            if stem_words:
                clean_name_tuples.add(stem_words)

            if bid:
                bundle_ids.add(bid)
                for part in bid.split(".")[:-1]:
                    if _is_meaningful_vendor_token(part):
                        vendor_tokens.add(normalize_token(part))

            display_name = str(info.get("CFBundleDisplayName") or info.get("CFBundleName") or app_dir.stem).strip()
            for key in ("CFBundleName", "CFBundleDisplayName"):
                val = str(info.get(key) or "").strip()
                if val:
                    nt = normalize_token(val)
                    if nt:
                        norm_names.add(nt)
                        app_norm_tokens.add(nt)
                    w_tuple = _clean_app_name_words(val)
                    if w_tuple:
                        clean_name_tuples.add(w_tuple)

            exec_val = str(info.get("CFBundleExecutable") or "").strip()
            if exec_val:
                nt_exec = normalize_token(exec_val)
                if nt_exec and nt_exec not in GENERIC_APP_VENDOR_STOPWORDS and len(nt_exec) >= 4:
                    norm_names.add(nt_exec)
                    app_norm_tokens.add(nt_exec)

            sparkle_feed = str(info.get("SUFeedURL") or "").strip()
            has_mas_receipt = (app_dir / "Contents" / "_MASReceipt" / "receipt").is_file()
            if has_mas_receipt:
                install_source = "app_store"
            elif sparkle_feed:
                install_source = "sparkle"
            else:
                install_source = "standalone"

            apps_detail.append(
                {
                    "name": display_name or app_dir.stem,
                    "stem": app_dir.stem,
                    "path": str(app_dir),
                    "bundle_id": bid_orig,
                    "bundle_id_lower": bid,
                    "version": str(info.get("CFBundleShortVersionString") or "").strip(),
                    "build": str(info.get("CFBundleVersion") or "").strip(),
                    "executable": exec_val,
                    "sparkle_feed": sparkle_feed,
                    "install_source": install_source,
                    "norm_tokens": sorted(app_norm_tokens),
                    "clean_name_tuples": sorted(clean_name_tuples),
                }
            )

    return {
        "bundle_ids": bundle_ids,
        "norm_names": norm_names,
        "vendor_tokens": vendor_tokens,
        "apps": apps_detail,
    }


def _strip_library_entry_suffix(entry_name: str) -> str:
    lower = entry_name.strip().lower()
    for suffix in (".savedstate", ".binarycookies", ".plist"):
        if lower.endswith(suffix):
            lower = lower[: -len(suffix)]
            break
    return lower


APP_RUNTIME_SUFFIX_WORDS = {
    "helper",
    "agent",
    "daemon",
    "updater",
    "crashreporter",
    "renderer",
    "gpu",
    "plugin",
    "shipit",
    "cache",
    "caches",
    "data",
    "support",
    "log",
    "logs",
    "savedstate",
}


def _are_runtime_qualifier_words(words: list[str]) -> bool:
    for w in words:
        if w in APP_RUNTIME_SUFFIX_WORDS or re.match(r"^v?\d+$", w):
            continue
        return False
    return True


def match_library_entry_to_app(entry_name: str, app: dict[str, Any]) -> bool:
    """Return True only when strong evidence ties a Library entry to the given app:
    1. Exact or dot-segmented CFBundleIdentifier match (<bid>, <bid>.*, *.<bid>, group.<bid>, group.<bid>.*);
    2. Full canonical app name matched on standalone word boundaries (for non-dotted folder names, never
       claiming another vendor's dotted bundle/container ID or a sibling app like 'Notion Calendar').
    """
    lower = _strip_library_entry_suffix(entry_name)
    if not lower:
        return False

    bid = str(app.get("bundle_id_lower") or app.get("bundle_id") or "").strip().lower()
    if bid:
        if (
            lower == bid
            or lower.startswith(f"{bid}.")
            or lower.endswith(f".{bid}")
            or f".{bid}." in lower
        ):
            return True

    # Never let a third-party app claim Apple system containers/preferences by word coincidence
    if lower.startswith(("com.apple.", "group.com.apple.")) and not bid.startswith("com.apple."):
        return False

    # Dotted directory names (reverse-DNS bundle IDs, Group Containers, TeamID.<bid>) are bundle identifiers:
    # if the app has a CFBundleIdentifier and did not match step 1 above, never match by plain app name.
    if "." in lower and bid:
        return False

    entry_words = [w for w in re.split(r"[^a-z0-9]+", lower) if w]
    if not entry_words:
        return False

    raw_tuples = app.get("clean_name_tuples")
    name_tuples: list[tuple[str, ...]] = []
    if isinstance(raw_tuples, list) and raw_tuples:
        for t in raw_tuples:
            if isinstance(t, (list, tuple)) and t:
                name_tuples.append(tuple(str(x).lower() for x in t if str(x)))
    else:
        for key in ("name", "stem", "display_name"):
            val = str(app.get(key) or "").strip()
            if val:
                wt = _clean_app_name_words(val)
                if wt and wt not in name_tuples:
                    name_tuples.append(wt)

    for name_words in name_tuples:
        joined = "".join(name_words)
        if not joined:
            continue
        # Single generic stopword or very short name (<4 chars) only matches if it is the entire entry name
        if len(joined) < 4 or (len(name_words) == 1 and joined in GENERIC_APP_VENDOR_STOPWORDS):
            if len(joined) >= 2 and entry_words == list(name_words):
                return True
            continue

        n_len = len(name_words)
        if entry_words == list(name_words):
            return True
        if len(entry_words) > n_len and entry_words[:n_len] == list(name_words):
            if _are_runtime_qualifier_words(entry_words[n_len:]):
                return True

        # Multi-word app name (e.g. "Super Studio") matching a single joined word ("SuperStudio"),
        # still anchored on word boundaries so "MySuperStudioExtra" does not match.
        if n_len >= 2:
            if entry_words == [joined]:
                return True
            if len(entry_words) > 1 and entry_words[0] == joined and _are_runtime_qualifier_words(entry_words[1:]):
                return True

    return False


def is_entry_matched_to_installed_app(entry_name: str, installed: dict[str, Any]) -> bool:
    lower = _strip_library_entry_suffix(entry_name)
    if any(lower.startswith(prefix.lower()) for prefix in SYSTEM_LIBRARY_IGNORE_PREFIXES):
        return True

    norm = normalize_token(lower)
    if not norm:
        return True

    bundle_ids: set[str] = installed.get("bundle_ids", set())
    norm_names: set[str] = installed.get("norm_names", set())

    if lower in bundle_ids or norm in norm_names:
        return True

    for bid in bundle_ids:
        if (
            lower.startswith(f"{bid}.")
            or lower.endswith(f".{bid}")
            or lower == f"group.{bid}"
            or lower.startswith(f"group.{bid}.")
            or bid.startswith(f"{lower}.")
        ):
            return True

    apps_list = installed.get("apps", [])
    for app in apps_list:
        if match_library_entry_to_app(entry_name, app):
            return True

    # Check non-generic bundle ID / vendor segments (e.g. com.google.Chrome -> google, Adobe -> adobe)
    parts = [normalize_token(p) for p in re.split(r"[.\-_ ]+", lower) if p]
    vendor_tokens: set[str] = installed.get("vendor_tokens", set())
    for p in parts:
        if p and p in vendor_tokens:
            return True

    return False


def _orphan_group_key(entry_name: str) -> str:
    lower = entry_name.strip().lower()
    for suffix in (".savedstate", ".binarycookies", ".plist"):
        if lower.endswith(suffix):
            lower = lower[: -len(suffix)]
            break
    parts = [p for p in lower.split(".") if p not in ("com", "org", "net", "io", "app", "mac", "macos", "savedstate")]
    if len(parts) >= 2:
        return normalize_token(parts[-1]) or normalize_token(lower)
    return normalize_token(lower)


def _effective_library_roots(config: dict[str, Any]) -> list[str]:
    library_roots = list(
        config.get(
            "library_roots",
            ["~/Library/Application Support", "~/Library/Caches", "~/Library/Containers"],
        )
    )
    if is_host_scan_config(config) and "orphan_extra_roots" not in config:
        for extra in (
            "~/Library/Group Containers",
            "~/Library/Saved Application State",
            "~/Library/Logs",
            "~/Library/HTTPStorages",
            "~/Library/WebKit",
        ):
            if extra not in library_roots:
                library_roots.append(extra)
    else:
        for extra in config.get("orphan_extra_roots", []):
            if extra not in library_roots:
                library_roots.append(extra)
    return library_roots


def scan_app_orphans(
    config: dict[str, Any],
    wl_index: dict[str, Any],
    installed: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    app_roots = config.get("app_roots", ["/Applications", "~/Applications"])
    library_roots = _effective_library_roots(config)
    min_mb = float(config.get("thresholds", {}).get("min_orphan_mb", 50))
    min_bytes = int(min_mb * 1024 * 1024)

    if installed is None:
        installed = collect_installed_apps(app_roots)
    candidates: list[dict[str, Any]] = []
    dir_sizes: dict[str, int] = {}

    raw_orphans: list[dict[str, Any]] = []
    for raw_lib in library_roots:
        lib_dir = expand_path(raw_lib)
        if not lib_dir.is_dir():
            continue
        try:
            entries = sorted(lib_dir.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            continue

        for entry in entries:
            if entry.is_symlink() or not entry.is_dir():
                continue
            if is_entry_matched_to_installed_app(entry.name, installed):
                continue

            size_bytes, truncated, latest_mtime = bounded_dir_size(entry)
            dir_sizes[str(entry)] = size_bytes
            raw_orphans.append(
                {
                    "entry": entry,
                    "lib_name": lib_dir.name,
                    "size_bytes": size_bytes,
                    "truncated": truncated,
                    "latest_mtime": latest_mtime,
                    "group_key": _orphan_group_key(entry.name),
                }
            )

    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in raw_orphans:
        gk = item["group_key"] or normalize_token(item["entry"].name)
        grouped.setdefault(gk, []).append(item)

    for gk, items in grouped.items():
        total_bytes = sum(x["size_bytes"] for x in items)
        if total_bytes < min_bytes:
            continue
        primary = max(items, key=lambda x: x["size_bytes"])
        entry = primary["entry"]
        truncated = any(x["truncated"] for x in items)
        latest_mtime = max(x["latest_mtime"] for x in items)

        whitelisted = False
        wl_note = ""
        for x in items:
            w, note = check_path_whitelisted(x["entry"], wl_index)
            if not w and normalize_token(x["entry"].name) in wl_index["app_tokens"]:
                w = True
                note = f"Protected by whitelist.app_names ({x['entry'].name})"
            if w:
                whitelisted = True
                wl_note = note
                break

        slug = re.sub(r"[^a-z0-9]+", "-", entry.name.lower()).strip("-")[:32]
        all_paths = [str(x["entry"]) for x in items]
        locations = "、".join(sorted({x["lib_name"] for x in items}))
        cand: dict[str, Any] = {
            "id": f"orphan-{slug}",
            "domain": "mac",
            "category": "app_orphan",
            "action_type": "cleanup",
            "tier": "C",
            "title": f"疑似已卸载应用残留：{entry.name}" + (f"（共 {len(all_paths)} 处残留）" if len(all_paths) > 1 else ""),
            "path": str(entry),
            "size_bytes": total_bytes,
            "size_truncated": truncated,
            "latest_mtime": latest_mtime,
            "gauge_id": "mac-data",
            "whitelisted": whitelisted,
            "whitelist_note": wl_note,
            "evidence": (
                f"位于 {locations}，未在 {app_roots} 中匹配到同 Bundle ID 或应用名称；"
                "删除前需确认该软件确已停用且不含需保留的个人配置或工程数据。"
            ),
        }
        if len(all_paths) > 1:
            cand["paths"] = all_paths
        candidates.append(cand)

    return candidates, dir_sizes


CACHE_ROOT_NAMES = {"caches", "saved application state", "logs", "httpstorages", "webkit"}
MDLS_BIN = "/usr/bin/mdls"


def _parse_mdls_date(raw: str) -> int:
    """Parse an mdls date such as '2026-10-13 04:38:32 +0000'; '(null)' or junk returns 0."""
    s = (raw or "").strip()
    if not s or s == "(null)":
        return 0
    try:
        return int(datetime.strptime(s, "%Y-%m-%d %H:%M:%S %z").timestamp())
    except ValueError:
        return 0


def collect_app_last_opened(
    app_paths: list[str],
    config: dict[str, Any],
    run_probes: bool,
) -> dict[str, int]:
    """Return {app_path: unix_ts} from Spotlight kMDItemLastUsedDate; apps without a value are omitted.

    `probe_fixtures.app_last_used` ({app_path: "YYYY-MM-DD HH:MM:SS +0000" | unix_ts}) replaces
    the live mdls probe for deterministic tests. Live probing only runs on host scans.
    """
    fixtures = config.get("probe_fixtures", {})
    if isinstance(fixtures, dict) and isinstance(fixtures.get("app_last_used"), dict):
        out_fix: dict[str, int] = {}
        for raw_path, value in fixtures["app_last_used"].items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                ts = int(value)
            else:
                ts = _parse_mdls_date(str(value))
            if ts > 0:
                out_fix[str(expand_path(str(raw_path)))] = ts
        return out_fix
    if not run_probes or not app_paths or not os.path.exists(MDLS_BIN):
        return {}

    out: dict[str, int] = {}
    try:
        proc = subprocess.run(
            [MDLS_BIN, "-raw", "-name", "kMDItemLastUsedDate", *app_paths],
            capture_output=True,
            timeout=30,
            check=False,
        )
        values = proc.stdout.decode("utf-8", errors="replace").split("\0") if proc.returncode == 0 else []
    except Exception:
        values = []
    if len(values) == len(app_paths):
        for p, v in zip(app_paths, values):
            ts = _parse_mdls_date(v)
            if ts:
                out[p] = ts
        return out

    # Batch output did not line up one value per app: query one by one within a time budget.
    deadline = time.monotonic() + 30.0
    for p in app_paths:
        if time.monotonic() > deadline:
            break
        try:
            single = subprocess.run(
                [MDLS_BIN, "-raw", "-name", "kMDItemLastUsedDate", p],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
        except Exception:
            continue
        ts = _parse_mdls_date(single.stdout) if single.returncode == 0 else 0
        if ts:
            out[p] = ts
    return out


def scan_apps_inventory_and_uninstall(
    config: dict[str, Any],
    wl_index: dict[str, Any],
    installed: dict[str, Any] | None = None,
    run_probes: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    """Inventory installed apps (size, data, cache, usage) and emit app_uninstall candidates for unused apps.

    "Last used" is the later of Spotlight's kMDItemLastUsedDate (when available) and the newest
    file change in the app bundle and its Library data/cache dirs. Without a Spotlight value the
    basis is file changes only, and titles say "无文件改动" instead of "未打开".
    """
    app_roots = config.get("app_roots", ["/Applications", "~/Applications"])
    library_roots = _effective_library_roots(config)
    thresholds = config.get("thresholds", {})
    unused_days_threshold = float(thresholds.get("unused_app_days", 90))
    min_app_mb = float(thresholds.get("min_app_size_mb", 50))
    min_app_bytes = int(min_app_mb * 1024 * 1024)

    if installed is None:
        installed = collect_installed_apps(app_roots)

    lib_entries: list[dict[str, Any]] = []
    for raw_lib in library_roots:
        lib_dir = expand_path(raw_lib)
        if not lib_dir.is_dir():
            continue
        is_cache_kind = lib_dir.name.strip().lower() in CACHE_ROOT_NAMES
        try:
            for child in sorted(lib_dir.iterdir(), key=lambda p: p.name.lower()):
                if child.is_symlink() or not child.is_dir():
                    continue
                lib_entries.append({"path": child, "name": child.name, "is_cache": is_cache_kind})
        except OSError:
            continue

    now_ts = time.time()
    inventory_out: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    dir_sizes: dict[str, int] = {}
    existing_app_paths = [str(Path(a["path"])) for a in installed.get("apps", []) if Path(a["path"]).exists()]
    last_opened_by_path = collect_app_last_opened(existing_app_paths, config, run_probes)

    for app in installed.get("apps", []):
        app_path = Path(app["path"])
        if not app_path.exists():
            continue
        app_bytes, app_trunc, app_mtime = bounded_dir_size(app_path)
        dir_sizes[str(app_path)] = app_bytes
        try:
            st = os.stat(app_path, follow_symlinks=False)
            last_modified_ts = max(app_mtime, int(st.st_mtime))
        except OSError:
            last_modified_ts = app_mtime

        data_bytes = 0
        cache_bytes = 0
        data_paths: list[str] = []
        cache_paths: list[str] = []

        for le in lib_entries:
            if match_library_entry_to_app(le["name"], app):
                sz, _, mtime = bounded_dir_size(le["path"])
                dir_sizes[str(le["path"])] = sz
                if mtime > last_modified_ts:
                    last_modified_ts = mtime
                if le["is_cache"]:
                    cache_bytes += sz
                    cache_paths.append(str(le["path"]))
                else:
                    data_bytes += sz
                    data_paths.append(str(le["path"]))

        # Conservative: an app counts as used if it was either opened (Spotlight) or changed recently.
        last_opened_ts = last_opened_by_path.get(str(app_path)) or last_opened_by_path.get(
            str(app_path.resolve(strict=False)), 0
        )
        last_used_basis = "spotlight" if last_opened_ts else "file_mtime"
        last_used_ts = max(last_opened_ts, last_modified_ts)
        total_bytes = app_bytes + data_bytes + cache_bytes
        days_since_used = max(0.0, (now_ts - last_used_ts) / 86400.0) if last_used_ts > 0 else 0.0
        usage_status = "infrequent" if days_since_used >= unused_days_threshold else "frequent"
        days_int = int(days_since_used)
        if last_used_basis == "spotlight":
            idle_label = f"约 {days_int} 天未打开、无文件改动"
            badge = f"{days_int} 天未打开"
            basis_note = (
                f"Spotlight kMDItemLastUsedDate 记录最近打开于 {datetime.fromtimestamp(last_opened_ts, timezone.utc):%Y-%m-%d}，"
                f"应用与数据目录最近改动于 {datetime.fromtimestamp(last_modified_ts, timezone.utc):%Y-%m-%d}"
                if last_modified_ts > 0
                else f"Spotlight kMDItemLastUsedDate 记录最近打开于 {datetime.fromtimestamp(last_opened_ts, timezone.utc):%Y-%m-%d}"
            )
        else:
            idle_label = f"约 {days_int} 天无文件改动"
            badge = f"{days_int} 天无改动"
            basis_note = "未取到 Spotlight 最近打开时间，按应用本体与数据、缓存目录的文件修改时间估算"

        whitelisted, wl_note = check_path_whitelisted(app_path, wl_index)
        if not whitelisted and normalize_token(app["name"]) in wl_index["app_tokens"]:
            whitelisted = True
            wl_note = f"Protected by whitelist.app_names ({app['name']})"

        keep_data_paths = [str(app_path), *cache_paths]
        purge_all_paths = [str(app_path), *data_paths, *cache_paths]
        keep_data_bytes = app_bytes + cache_bytes

        inv_item: dict[str, Any] = {
            "name": app["name"],
            "bundle_id": app["bundle_id"],
            "path": str(app_path),
            "version": app["version"],
            "build": app["build"],
            "install_source": app["install_source"],
            "sparkle_feed": app["sparkle_feed"],
            "app_bytes": app_bytes,
            "data_bytes": data_bytes,
            "cache_bytes": cache_bytes,
            "total_bytes": total_bytes,
            "data_paths": data_paths,
            "cache_paths": cache_paths,
            "all_paths": purge_all_paths,
            "last_used_ts": last_used_ts,
            "last_opened_ts": last_opened_ts,
            "last_modified_ts": last_modified_ts,
            "last_used_basis": last_used_basis,
            "days_since_used": round(days_since_used, 1),
            "usage_status": usage_status,
            "whitelisted": whitelisted,
        }
        inventory_out.append(inv_item)

        if usage_status == "infrequent" and total_bytes >= min_app_bytes:
            slug = re.sub(r"[^a-z0-9]+", "-", (app["bundle_id_lower"] or app["stem"]).lower()).strip("-")[:36]
            candidates.append(
                {
                    "id": f"app-uninstall-{slug}",
                    "domain": "mac",
                    "category": "app_uninstall",
                    "action_type": "cleanup",
                    "tier": "C",
                    "title": f"不常用应用卸载：{app['name']}（{idle_label}）",
                    "path": str(app_path),
                    # Default mode is keep_data: only the .app bundle and caches, user data stays.
                    "selected_uninstall_mode": "keep_data",
                    "paths": keep_data_paths,
                    "app_only_paths": keep_data_paths,
                    "all_uninstall_paths": purge_all_paths,
                    "size_bytes": total_bytes,
                    "app_bytes": app_bytes,
                    "data_bytes": data_bytes,
                    "cache_bytes": cache_bytes,
                    "size_truncated": app_trunc,
                    "latest_mtime": last_used_ts,
                    "last_used_basis": last_used_basis,
                    "metric_badge": badge,
                    "gauge_id": "mac-data",
                    "whitelisted": whitelisted,
                    "whitelist_note": wl_note,
                    "uninstall_modes": {
                        "purge_all": {
                            "label": "一键全部删除（含残留与数据）",
                            "size_bytes": total_bytes,
                            "paths": purge_all_paths,
                        },
                        "keep_data": {
                            "label": "保留用户数据（仅删应用与缓存）",
                            "size_bytes": keep_data_bytes,
                            "paths": keep_data_paths,
                        },
                    },
                    "evidence": (
                        f"应用本体 {app_bytes / (1024**2):.1f} MiB + 用户数据 {data_bytes / (1024**2):.1f} MiB + "
                        f"缓存与状态 {cache_bytes / (1024**2):.1f} MiB（共 {total_bytes / (1024**2):.1f} MiB）；"
                        f"{basis_note}，按较晚者计{idle_label}。默认「保留用户数据」只移走应用本体与缓存；"
                        "「一键全部删除（含全部残留）」需用户明确选择。"
                    ),
                }
            )

    inventory_out.sort(key=lambda x: x["total_bytes"], reverse=True)
    return inventory_out, candidates, dir_sizes


DEFAULT_GLOBAL_CACHE_ROOTS = (
    "~/Library/Developer/Xcode/DerivedData",
    "~/Library/Caches/Homebrew",
    "~/.npm/_cacache",
    "~/.cache/uv",
    "~/.cache/pip",
    "~/Library/Caches/ms-playwright",
)


def scan_global_caches(
    config: dict[str, Any],
    wl_index: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Scan pure reproducible developer/package global caches without touching daily hot app caches."""
    if "global_cache_roots" in config:
        raw_roots = config.get("global_cache_roots", [])
    elif is_host_scan_config(config):
        raw_roots = list(DEFAULT_GLOBAL_CACHE_ROOTS)
    else:
        raw_roots = []

    min_cache_mb = float(config.get("thresholds", {}).get("min_cache_mb", 200))
    min_cache_bytes = int(min_cache_mb * 1024 * 1024)

    candidates: list[dict[str, Any]] = []
    dir_sizes: dict[str, int] = {}

    for raw in raw_roots:
        raw_p = Path(os.path.abspath(os.path.expanduser(raw)))
        if raw_p.is_symlink() or any(
            anc.is_symlink() for anc in raw_p.parents if str(anc) not in ("/", "/tmp", "/var", "/etc")
        ):
            continue
        if not raw_p.is_dir():
            continue
        p = raw_p.resolve(strict=False)
        sz, trunc, latest_mtime = bounded_dir_size(p)
        dir_sizes[str(p)] = sz
        if sz < min_cache_bytes:
            continue
        whitelisted, wl_note = check_path_whitelisted(p, wl_index)
        slug = re.sub(r"[^a-z0-9]+", "-", p.name.lower()).strip("-")[:32]
        candidates.append(
            {
                "id": f"cache-{slug}",
                "domain": "mac",
                "category": "system_cache",
                "action_type": "cleanup",
                "tier": "A",
                "title": f"可安全清理的全局工具缓存：{p.name}",
                "path": str(p),
                "size_bytes": sz,
                "size_truncated": trunc,
                "latest_mtime": latest_mtime,
                "gauge_id": "mac-data",
                "whitelisted": whitelisted,
                "whitelist_note": wl_note,
                "evidence": (
                    f"全局构建/包管理器纯缓存目录 {p}（{sz / (1024**2):.1f} MiB），"
                    "清理后不影响日常软件配置与登录状态，后续按需自动重建。"
                ),
            }
        )
    return candidates, dir_sizes


DEFAULT_LAUNCH_AGENT_ROOTS = (
    "~/Library/LaunchAgents",
    "/Library/LaunchAgents",
    "/Library/LaunchDaemons",
)

SYSTEM_BINARY_PREFIXES = (
    "/System/",
    "/usr/libexec/",
    "/usr/sbin/",
    "/usr/bin/",
    "/bin/",
    "/sbin/",
)


def _extract_launch_executable(plist_data: dict[str, Any]) -> str:
    prog = plist_data.get("Program")
    if isinstance(prog, str) and prog.strip():
        return prog.strip()
    args = plist_data.get("ProgramArguments")
    if isinstance(args, list) and args and isinstance(args[0], str) and args[0].strip():
        return args[0].strip()
    return ""


def _query_launchctl_exit_codes(config: dict[str, Any]) -> dict[str, int]:
    fixtures = config.get("probe_fixtures", {})
    if isinstance(fixtures, dict) and isinstance(fixtures.get("launchctl_status"), dict):
        out_fix: dict[str, int] = {}
        for k, v in fixtures["launchctl_status"].items():
            if isinstance(v, int):
                out_fix[str(k)] = int(v)
            elif isinstance(v, dict) and isinstance(v.get("exit_code"), int):
                out_fix[str(k)] = int(v["exit_code"])
        return out_fix
    if not is_host_scan_config(config):
        return {}
    try:
        proc = subprocess.run(
            ["/bin/launchctl", "list"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if proc.returncode != 0:
            return {}
        out: dict[str, int] = {}
        for line in proc.stdout.splitlines()[1:]:
            parts = line.split("\t")
            if len(parts) == 3:
                _, status_str, label = parts
                try:
                    code = int(status_str.strip())
                    out[label.strip()] = code
                except ValueError:
                    pass
        return out
    except Exception:
        return {}


def scan_launch_items(
    config: dict[str, Any],
    wl_index: dict[str, Any],
    installed: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Scan macOS LaunchAgents/LaunchDaemons for broken executables, uninstalled orphan apps, or crash loops."""
    if "launch_agent_roots" in config:
        raw_roots = config.get("launch_agent_roots", [])
    elif is_host_scan_config(config):
        raw_roots = list(DEFAULT_LAUNCH_AGENT_ROOTS)
    else:
        raw_roots = []

    if not raw_roots:
        return []

    if installed is None:
        installed = collect_installed_apps(config.get("app_roots", ["/Applications", "~/Applications"]))
    exit_codes = _query_launchctl_exit_codes(config)
    candidates: list[dict[str, Any]] = []

    for raw_root in raw_roots:
        root = expand_path(raw_root)
        if not root.is_dir():
            continue
        try:
            plists = sorted(root.glob("*.plist"), key=lambda p: p.name.lower())
        except OSError:
            continue

        for plist_path in plists:
            if plist_path.is_symlink() or not plist_path.is_file():
                continue
            data = _read_plist_safe(plist_path)
            if not data:
                continue
            label = str(data.get("Label") or plist_path.stem).strip()
            if label.lower().startswith("com.apple."):
                continue

            exe_raw = _extract_launch_executable(data)
            exe_path = expand_path(exe_raw) if exe_raw.startswith(("~", "/")) else None
            whitelisted, wl_note = check_path_whitelisted(plist_path, wl_index)
            if not whitelisted and normalize_token(label) in wl_index["app_tokens"]:
                whitelisted = True
                wl_note = f"Protected by whitelist.app_names ({label})"

            slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:36]
            try:
                mtime = int(plist_path.stat().st_mtime)
            except OSError:
                mtime = 0

            # 1. Broken executable: points to a non-existent binary on disk
            if exe_path is not None and not any(str(exe_path).startswith(p) for p in SYSTEM_BINARY_PREFIXES):
                if not exe_path.exists():
                    candidates.append(
                        {
                            "id": f"launch-broken-{slug}",
                            "domain": "mac",
                            "category": "launch_item",
                            "action_type": "cleanup",
                            "tier": "B",
                            "title": f"无用启动项（目标程序已不存在）：{label}",
                            "path": str(plist_path),
                            "size_bytes": 0,
                            "size_truncated": False,
                            "latest_mtime": mtime,
                            "metric_badge": "失效启动项",
                            "gauge_id": "mac-data",
                            "whitelisted": whitelisted,
                            "whitelist_note": wl_note,
                            "evidence": (
                                f"启动项配置 {plist_path} 指向的可执行文件 `{exe_raw}` 已不存在，"
                                "通常是软件卸载后遗留的无效开机自启项，可安全卸载并移入隔离区。"
                            ),
                        }
                    )
                    continue

            # 2. Orphan launch item: points inside an uninstalled .app or unmatched third-party bundle
            is_in_shared_prefix = exe_raw.startswith(
                ("/opt/homebrew/", "/usr/local/", "/System/", "/usr/", "/bin/", "/sbin/")
            )
            matched_app = is_entry_matched_to_installed_app(label, installed)
            if not matched_app and exe_path is not None and ".app/" in str(exe_path):
                app_part = str(exe_path).split(".app/", 1)[0] + ".app"
                if Path(app_part).exists():
                    matched_app = True

            if not matched_app and not is_in_shared_prefix:
                candidates.append(
                    {
                        "id": f"launch-orphan-{slug}",
                        "domain": "mac",
                        "category": "launch_item",
                        "action_type": "cleanup",
                        "tier": "C",
                        "title": f"疑似孤儿启动项（未匹配已装应用）：{label}",
                        "path": str(plist_path),
                        "size_bytes": 0,
                        "size_truncated": False,
                        "latest_mtime": mtime,
                        "metric_badge": "孤儿启动项",
                        "gauge_id": "mac-data",
                        "whitelisted": whitelisted,
                        "whitelist_note": wl_note,
                        "evidence": (
                            f"启动项 {plist_path}（可执行路径 `{exe_raw or '未声明'}`）未匹配到任何已安装应用，"
                            "请确认对应软件是否已停用；若已不用可取消加载并移入隔离区。"
                        ),
                    }
                )
                continue

            # 3. Crash loop / non-zero exit code in launchctl list
            exit_code = exit_codes.get(label)
            if exit_code is not None and exit_code != 0:
                candidates.append(
                    {
                        "id": f"launch-crash-{slug}",
                        "domain": "mac",
                        "category": "launch_item",
                        "action_type": "optimize",
                        "tier": "B",
                        "title": f"后台启动项异常退出（退出码 {exit_code}）：{label}",
                        "path": str(plist_path),
                        "size_bytes": 0,
                        "size_truncated": False,
                        "latest_mtime": mtime,
                        "metric_badge": f"退出码 {exit_code}",
                        "gauge_id": "mac-data",
                        "whitelisted": whitelisted,
                        "whitelist_note": wl_note,
                        "evidence": (
                            f"`launchctl list` 显示启动项 {label} 最近一次退出码为 {exit_code}（可执行路径 `{exe_raw}`），"
                            "可能在后台反复重启报错，建议检查日志或禁用该自启服务。"
                        ),
                    }
                )

    return candidates


def _should_run_system_probes(config: dict[str, Any], cli_override: bool | None = None) -> bool:
    if cli_override is not None:
        return cli_override
    if "system_probes" in config:
        val = config["system_probes"]
        return bool(val) if not isinstance(val, dict) else any(bool(v) for v in val.values())
    return is_host_scan_config(config)


def scan_app_updates(
    config: dict[str, Any],
    wl_index: dict[str, Any],
    app_inventory: list[dict[str, Any]],
    run_probes: bool = False,
) -> list[dict[str, Any]]:
    """Check installed macOS apps for available updates via Homebrew Cask, App Store (mas), or fixtures."""
    fixtures = config.get("probe_fixtures", {})
    outdated_records: list[dict[str, Any]] = []

    if isinstance(fixtures, dict) and isinstance(fixtures.get("app_updates"), list):
        outdated_records.extend(fixtures["app_updates"])
    elif run_probes:
        brew = shutil.which("brew")
        if brew:
            try:
                env = {**os.environ, "HOMEBREW_NO_AUTO_UPDATE": "1"}
                proc = subprocess.run(
                    [brew, "outdated", "--cask", "--json=v2"],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    env=env,
                    check=False,
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    data = json.loads(proc.stdout)
                    for cask in data.get("casks", []):
                        inst = cask.get("installed_versions")
                        cur_ver = inst[0] if isinstance(inst, list) and inst else str(inst or "")
                        latest_ver = str(cask.get("current_version") or "")
                        token = str(cask.get("name") or "")
                        if token and latest_ver and cur_ver != latest_ver:
                            outdated_records.append(
                                {
                                    "name": token,
                                    "current_version": cur_ver,
                                    "latest_version": latest_ver,
                                    "source": "Homebrew Cask",
                                    "command": f"brew upgrade --cask {token}",
                                }
                            )
            except Exception:
                pass

        mas = shutil.which("mas")
        if mas:
            try:
                proc = subprocess.run(
                    [mas, "outdated"],
                    capture_output=True,
                    text=True,
                    timeout=12,
                    check=False,
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    for line in proc.stdout.splitlines():
                        m = re.match(r"^\s*(\d+)\s+(.+?)\s+\(([^()]+?)\s*->\s*([^()]+?)\)\s*$", line)
                        if m:
                            app_id, name, cur_v, new_v = m.groups()
                            outdated_records.append(
                                {
                                    "name": name.strip(),
                                    "current_version": cur_v.strip(),
                                    "latest_version": new_v.strip(),
                                    "source": "Mac App Store",
                                    "command": f"mas upgrade {app_id}",
                                }
                            )
            except Exception:
                pass

    inv_by_norm: dict[str, dict[str, Any]] = {}
    for app in app_inventory:
        for tok in (normalize_token(app["name"]), normalize_token(Path(app["path"]).stem), normalize_token(app["bundle_id"])):
            if tok:
                inv_by_norm[tok] = app

    candidates: list[dict[str, Any]] = []
    for rec in outdated_records:
        raw_name = str(rec.get("name") or "").strip()
        cur_ver = str(rec.get("current_version") or "未知").strip()
        latest_ver = str(rec.get("latest_version") or "").strip()
        source = str(rec.get("source") or "官方更新源").strip()
        cmd_hint = str(rec.get("command") or "官方更新器").strip()
        if not raw_name or not latest_ver:
            continue

        norm_key = normalize_token(raw_name)
        matched_app = inv_by_norm.get(norm_key)
        app_path = str(rec.get("path") or (matched_app["path"] if matched_app else f"/Applications/{raw_name}.app"))
        display_name = matched_app["name"] if matched_app else raw_name
        if matched_app:
            matched_app["has_update"] = True
            matched_app["latest_version"] = latest_ver
            matched_app["update_source"] = source

        whitelisted = normalize_token(display_name) in wl_index["app_tokens"]
        wl_note = f"Protected by whitelist.app_names ({display_name})" if whitelisted else ""
        slug = re.sub(r"[^a-z0-9]+", "-", norm_key).strip("-")[:32]

        candidates.append(
            {
                "id": f"app-update-{slug}",
                "domain": "mac",
                "category": "app_update",
                "action_type": "update",
                "tier": "B",
                "title": f"应用版本更新：{display_name} ({cur_ver} → {latest_ver})",
                "path": app_path,
                "size_bytes": 0,
                "size_truncated": False,
                "latest_mtime": 0,
                "metric_badge": f"{cur_ver} → {latest_ver}",
                "gauge_id": "mac-data",
                "whitelisted": whitelisted,
                "whitelist_note": wl_note,
                "evidence": (
                    f"检测到应用 {display_name} 存在可用更新：当前版本 {cur_ver} → 最新版本 {latest_ver}（来源：{source}）。"
                    f"建议在确认应用未处于关键任务时执行 `{cmd_hint}` 升级。"
                ),
            }
        )

    return candidates


def scan_env_and_dependencies(
    config: dict[str, Any],
    wl_index: dict[str, Any],
    run_probes: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Scan macOS developer environments, CLI formulae, and global language dependencies for outdated versions."""
    fixtures = config.get("probe_fixtures", {})
    env_groups: list[dict[str, Any]] = []

    if isinstance(fixtures, dict) and isinstance(fixtures.get("env_updates"), list):
        env_groups.extend(fixtures["env_updates"])
    elif run_probes:
        brew = shutil.which("brew")
        if brew:
            try:
                env = {**os.environ, "HOMEBREW_NO_AUTO_UPDATE": "1"}
                proc = subprocess.run(
                    [brew, "outdated", "--formula", "--json=v2"],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    env=env,
                    check=False,
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    data = json.loads(proc.stdout)
                    pkgs = []
                    for f in data.get("formulae", []):
                        if f.get("pinned"):
                            continue
                        inst = f.get("installed_versions")
                        cur_v = inst[0] if isinstance(inst, list) and inst else str(inst or "")
                        new_v = str(f.get("current_version") or "")
                        if f.get("name") and new_v and cur_v != new_v:
                            pkgs.append({"name": f["name"], "current": cur_v, "latest": new_v})
                    if pkgs:
                        env_groups.append(
                            {
                                "id": "brew-formula",
                                "manager": "Homebrew Formulae",
                                "command": "brew upgrade --formula",
                                "packages": pkgs,
                            }
                        )
            except Exception:
                pass

        npm = shutil.which("npm")
        if npm:
            try:
                proc = subprocess.run(
                    [npm, "outdated", "-g", "--json"],
                    capture_output=True,
                    text=True,
                    timeout=12,
                    check=False,
                )
                if proc.stdout.strip():
                    data = json.loads(proc.stdout)
                    if isinstance(data, dict) and data:
                        pkgs = [
                            {
                                "name": k,
                                "current": str(v.get("current") or "未知"),
                                "latest": str(v.get("latest") or v.get("wanted") or ""),
                            }
                            for k, v in data.items()
                            if isinstance(v, dict) and v.get("current") != v.get("latest")
                        ]
                        if pkgs:
                            env_groups.append(
                                {
                                    "id": "npm-global",
                                    "manager": "Node.js 全局依赖 (npm -g)",
                                    "command": "npm update -g",
                                    "packages": pkgs,
                                }
                            )
            except Exception:
                pass

        rustup = shutil.which("rustup")
        if rustup:
            try:
                proc = subprocess.run(
                    [rustup, "check"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                if proc.returncode == 0 and "Update available" in proc.stdout:
                    pkgs = []
                    for line in proc.stdout.splitlines():
                        if "Update available" in line:
                            name = line.split("-", 1)[0].strip()
                            ver_part = line.split("Update available", 1)[-1].lstrip(" :").strip()
                            pkgs.append({"name": name, "current": "已安装", "latest": ver_part})
                    if pkgs:
                        env_groups.append(
                            {
                                "id": "rustup",
                                "manager": "Rust 工具链 (rustup)",
                                "command": "rustup update",
                                "packages": pkgs,
                            }
                        )
            except Exception:
                pass

    candidates: list[dict[str, Any]] = []
    for grp in env_groups:
        gid = re.sub(
            r"[^a-z0-9]+",
            "-",
            str(grp.get("id") or grp.get("manager") or grp.get("ecosystem") or "env").lower(),
        ).strip("-")
        manager = str(grp.get("manager") or grp.get("ecosystem") or gid)
        cmd = str(grp.get("command") or "")
        pkgs = grp.get("packages", [])
        if not isinstance(pkgs, list) or not pkgs:
            continue
        preview_list = [
            f"{p.get('name', '?')} ({p.get('current', '?')} → {p.get('latest', '?')})"
            if isinstance(p, dict)
            else str(p)
            for p in pkgs[:8]
        ]
        more_suffix = f" 等共 {len(pkgs)} 项" if len(pkgs) > 8 else ""
        candidates.append(
            {
                "id": f"env-update-{gid}",
                "domain": "mac",
                "category": "env_update",
                "action_type": "update",
                "tier": "B",
                "title": f"环境与依赖更新：{manager}（{len(pkgs)} 项可升级）",
                "path": cmd or manager,
                "size_bytes": 0,
                "size_truncated": False,
                "latest_mtime": 0,
                "metric_badge": f"{len(pkgs)} 项可更新",
                "gauge_id": "mac-data",
                "whitelisted": False,
                "whitelist_note": "",
                "evidence": (
                    f"{manager} 检测到可更新组件：{', '.join(preview_list)}{more_suffix}。"
                    f"建议核对大版本兼容性后执行 `{cmd}` 升级。"
                ),
            }
        )

    return env_groups, candidates


def _walk_usb_nodes(
    nodes: list[dict[str, Any]],
    out_drives: list[dict[str, Any]],
    out_docks: list[dict[str, Any]],
    out_inputs: list[dict[str, Any]],
) -> None:
    for node in nodes:
        if not isinstance(node, dict):
            continue
        name = str(node.get("_name") or "").strip()
        vendor = str(node.get("manufacturer") or node.get("vendor_id") or "").strip()
        fw = str(node.get("bcd_device") or node.get("firmware_version") or node.get("device_revision") or "").strip()
        speed = str(node.get("link_speed") or node.get("speed") or "").strip()
        lname = name.lower()

        if node.get("Media") or any(k in lname for k in ("ssd", "nvme", "portable", "flash", "storage", "card reader")):
            out_drives.append(
                {
                    "name": name or "USB Storage",
                    "kind": "sd_card" if "card" in lname else "external_drive",
                    "vendor": vendor,
                    "firmware_version": fw or "未报告",
                    "smart_status": "Verified",
                    "link_speed": speed,
                    "transport": "USB",
                }
            )
        elif any(k in lname for k in ("hub", "dock", "thunderbolt", "caldigit", "anker", "belkin", "ugreen", "benq", "ivanky")):
            out_docks.append(
                {
                    "name": name,
                    "vendor": vendor,
                    "firmware_version": fw or "未报告",
                    "link_speed": speed,
                    "transport": "USB/Hub",
                }
            )
        elif any(k in lname for k in ("mouse", "keyboard", "trackpad", "receiver", "keychron", "logi", "razer", "nuphy", "hhkb")):
            out_inputs.append(
                {
                    "name": name,
                    "kind": "mouse" if "mouse" in lname else ("keyboard" if "key" in lname else "peripheral"),
                    "vendor": vendor,
                    "firmware_version": fw or "未报告",
                    "battery_pct": None,
                    "transport": "USB",
                }
            )

        for child_key in ("_items", "items"):
            children = node.get(child_key)
            if isinstance(children, list):
                _walk_usb_nodes(children, out_drives, out_docks, out_inputs)


def scan_peripherals_and_firmware(
    config: dict[str, Any],
    wl_index: dict[str, Any],
    run_probes: bool = False,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Inspect connected disks, SD cards, docks/hubs, mice, and keyboards for firmware, link speed, SMART, and battery health."""
    fixtures = config.get("probe_fixtures", {})
    peripherals: dict[str, Any] = {
        "storage_devices": [],
        "docks_and_hubs": [],
        "input_devices": [],
    }

    if isinstance(fixtures, dict) and isinstance(fixtures.get("peripherals"), dict):
        fp = fixtures["peripherals"]
        peripherals["storage_devices"] = list(fp.get("storage_devices", []))
        peripherals["docks_and_hubs"] = list(fp.get("docks_and_hubs", []))
        peripherals["input_devices"] = list(fp.get("input_devices", []))
    elif run_probes and shutil.which("system_profiler"):
        try:
            proc = subprocess.run(
                [
                    "/usr/sbin/system_profiler",
                    "SPNVMeDataType",
                    "SPUSBHostDataType",
                    "SPThunderboltDataType",
                    "SPBluetoothDataType",
                    "SPCardReaderDataType",
                    "-detailLevel",
                    "mini",
                    "-json",
                ],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                sp = json.loads(proc.stdout)
                for ctrl in sp.get("SPNVMeDataType", []):
                    for item in ctrl.get("_items", []):
                        peripherals["storage_devices"].append(
                            {
                                "name": str(item.get("_name") or item.get("device_model") or "NVMe SSD"),
                                "kind": "nvme_ssd",
                                "vendor": "Apple/NVMe",
                                "model": str(item.get("device_model") or ""),
                                "firmware_version": str(item.get("device_revision") or "未知"),
                                "smart_status": str(item.get("smart_status") or "Verified"),
                                "link_speed": str(item.get("spnvme_linkspeed") or ""),
                                "transport": "NVMe",
                            }
                        )
                for cr in sp.get("SPCardReaderDataType", []):
                    for item in cr.get("_items", []):
                        peripherals["storage_devices"].append(
                            {
                                "name": str(item.get("_name") or "SD Card"),
                                "kind": "sd_card",
                                "vendor": str(item.get("spcardreader_vendor_id") or ""),
                                "firmware_version": str(item.get("spcardreader_revision_id") or "未知"),
                                "smart_status": "Verified",
                                "link_speed": "",
                                "transport": "SD Card Reader",
                            }
                        )
                for tb in sp.get("SPThunderboltDataType", []):
                    for dev in tb.get("_items", []):
                        name = str(dev.get("_name") or "").strip()
                        if name and "bus" not in name.lower():
                            peripherals["docks_and_hubs"].append(
                                {
                                    "name": name,
                                    "vendor": str(dev.get("vendor_name_key") or ""),
                                    "firmware_version": str(dev.get("firmware_version") or dev.get("switch_version_key") or "未知"),
                                    "link_speed": str(dev.get("receptacle_1_tag", {}).get("current_speed_key") or ""),
                                    "transport": "Thunderbolt",
                                }
                            )
                _walk_usb_nodes(
                    sp.get("SPUSBHostDataType", []),
                    peripherals["storage_devices"],
                    peripherals["docks_and_hubs"],
                    peripherals["input_devices"],
                )
                for bt in sp.get("SPBluetoothDataType", []):
                    connected = bt.get("device_connected", [])
                    if isinstance(connected, list):
                        for entry in connected:
                            if not isinstance(entry, dict):
                                continue
                            for dev_name, dev_info in entry.items():
                                if not isinstance(dev_info, dict):
                                    continue
                                minor = str(dev_info.get("device_minorType") or "").lower()
                                bat_raw = str(dev_info.get("device_batteryLevelMain") or dev_info.get("device_batteryPercent") or "").rstrip("%")
                                bat_pct = int(bat_raw) if bat_raw.isdigit() else None
                                peripherals["input_devices"].append(
                                    {
                                        "name": dev_name,
                                        "kind": minor or "bluetooth_device",
                                        "vendor": str(dev_info.get("device_vendorID") or ""),
                                        "firmware_version": str(dev_info.get("device_firmwareVersion") or "未知"),
                                        "battery_pct": bat_pct,
                                        "transport": "Bluetooth",
                                    }
                                )
        except Exception:
            pass

    candidates: list[dict[str, Any]] = []
    fw_summary_parts: list[str] = []

    for drv in peripherals["storage_devices"]:
        name = str(drv.get("name") or "存储设备")
        fw = str(drv.get("firmware_version") or "未知")
        smart = str(drv.get("smart_status") or "Verified")
        speed = str(drv.get("link_speed") or "")
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:32]
        fw_summary_parts.append(f"{name} (固件 {fw})")

        if smart and smart.lower() not in ("verified", "ok", "not supported"):
            candidates.append(
                {
                    "id": f"hw-smart-{slug}",
                    "domain": "mac",
                    "category": "peripheral_firmware",
                    "action_type": "optimize",
                    "tier": "C",
                    "title": f"磁盘/存储 SMART 健康异常预警：{name} ({smart})",
                    "path": name,
                    "size_bytes": 0,
                    "size_truncated": False,
                    "latest_mtime": 0,
                    "metric_badge": f"SMART: {smart}",
                    "gauge_id": "mac-data",
                    "whitelisted": False,
                    "whitelist_note": "",
                    "evidence": f"存储设备 {name}（固件版本 {fw}）报告 SMART 状态为 `{smart}`，请立即核实备份并检查硬件健康度。",
                }
            )
        if "480 mb" in speed.lower() and drv.get("kind") == "external_drive":
            candidates.append(
                {
                    "id": f"hw-speed-{slug}",
                    "domain": "mac",
                    "category": "peripheral_firmware",
                    "action_type": "optimize",
                    "tier": "B",
                    "title": f"外置存储握手速率降级提醒：{name} ({speed})",
                    "path": name,
                    "size_bytes": 0,
                    "size_truncated": False,
                    "latest_mtime": 0,
                    "metric_badge": speed,
                    "gauge_id": "mac-data",
                    "whitelisted": False,
                    "whitelist_note": "",
                    "evidence": (
                        f"外置存储 {name}（固件 {fw}）当前协商速率仅为 `{speed}`（疑似回落至 USB 2.0），"
                        "建议检查连接线材、扩展坞端口或设备固件。"
                    ),
                }
            )
        if drv.get("update_available") or drv.get("latest_firmware"):
            latest_fw = str(drv.get("latest_firmware") or "新版可升")
            candidates.append(
                {
                    "id": f"hw-fw-{slug}",
                    "domain": "mac",
                    "category": "peripheral_firmware",
                    "action_type": "update",
                    "tier": "B",
                    "title": f"存储固件更新可用：{name} ({fw} → {latest_fw})",
                    "path": name,
                    "size_bytes": 0,
                    "size_truncated": False,
                    "latest_mtime": 0,
                    "metric_badge": f"{fw} → {latest_fw}",
                    "gauge_id": "mac-data",
                    "whitelisted": False,
                    "whitelist_note": "",
                    "evidence": f"存储设备 {name} 存在可匹配的官方固件更新（当前 {fw} → 目标 {latest_fw}）。",
                }
            )

    for dock in peripherals["docks_and_hubs"]:
        name = str(dock.get("name") or "扩展坞")
        fw = str(dock.get("firmware_version") or "未知")
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:32]
        fw_summary_parts.append(f"{name} (固件 {fw})")
        if dock.get("update_available") or dock.get("latest_firmware"):
            latest_fw = str(dock.get("latest_firmware") or "新版可升")
            candidates.append(
                {
                    "id": f"hw-fw-{slug}",
                    "domain": "mac",
                    "category": "peripheral_firmware",
                    "action_type": "update",
                    "tier": "B",
                    "title": f"扩展坞固件更新可用：{name} ({fw} → {latest_fw})",
                    "path": name,
                    "size_bytes": 0,
                    "size_truncated": False,
                    "latest_mtime": 0,
                    "metric_badge": f"{fw} → {latest_fw}",
                    "gauge_id": "mac-data",
                    "whitelisted": False,
                    "whitelist_note": "",
                    "evidence": f"扩展坞/集线器 {name} 检测到可用固件更新（当前 {fw} → 目标 {latest_fw}）。",
                }
            )

    for inp in peripherals["input_devices"]:
        name = str(inp.get("name") or "外设")
        fw = str(inp.get("firmware_version") or "未知")
        bat = inp.get("battery_pct")
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:32]
        fw_summary_parts.append(f"{name} (固件 {fw}" + (f", 电量 {bat}%" if bat is not None else "") + ")")

        if isinstance(bat, int) and bat <= 20:
            candidates.append(
                {
                    "id": f"hw-battery-{slug}",
                    "domain": "mac",
                    "category": "peripheral_firmware",
                    "action_type": "optimize",
                    "tier": "B",
                    "title": f"无线键鼠/外设低电量提醒：{name}（剩余 {bat}%）",
                    "path": name,
                    "size_bytes": 0,
                    "size_truncated": False,
                    "latest_mtime": 0,
                    "metric_badge": f"电量 {bat}%",
                    "gauge_id": "mac-data",
                    "whitelisted": False,
                    "whitelist_note": "",
                    "evidence": f"外设 {name}（连接方式：{inp.get('transport', 'Bluetooth')}，固件 {fw}）当前电量为 {bat}%，建议及时充电。",
                }
            )
        if inp.get("update_available") or inp.get("latest_firmware"):
            latest_fw = str(inp.get("latest_firmware") or "新版可升")
            candidates.append(
                {
                    "id": f"hw-fw-{slug}",
                    "domain": "mac",
                    "category": "peripheral_firmware",
                    "action_type": "update",
                    "tier": "B",
                    "title": f"键鼠外设固件更新可用：{name} ({fw} → {latest_fw})",
                    "path": name,
                    "size_bytes": 0,
                    "size_truncated": False,
                    "latest_mtime": 0,
                    "metric_badge": f"{fw} → {latest_fw}",
                    "gauge_id": "mac-data",
                    "whitelisted": False,
                    "whitelist_note": "",
                    "evidence": f"外设 {name} 检测到官方固件更新（当前 {fw} → 目标 {latest_fw}），可通过厂商官方驱动工具升级。",
                }
            )

    if fw_summary_parts and not candidates:
        candidates.append(
            {
                "id": "hw-firmware-inventory",
                "domain": "mac",
                "category": "peripheral_firmware",
                "action_type": "update",
                "tier": "B",
                "default_checked": False,
                "title": f"已连接硬盘、扩展坞与键鼠固件核对（共 {len(fw_summary_parts)} 台设备）",
                "path": "system_profiler",
                "size_bytes": 0,
                "size_truncated": False,
                "latest_mtime": 0,
                "metric_badge": f"{len(fw_summary_parts)} 台设备",
                "gauge_id": "mac-data",
                "whitelisted": False,
                "whitelist_note": "",
                "evidence": "当前在线存储与外设固件清单：" + "；".join(fw_summary_parts[:10]) + "。勾选后由代理核对各型号官方最新固件发布说明。",
            }
        )

    return peripherals, candidates


def iter_git_repos(workspace_roots: list[str], max_depth: int = 3) -> list[Path]:
    repos: list[Path] = []
    seen: set[Path] = set()

    def walk(current: Path, depth: int) -> None:
        if depth > max_depth or not current.is_dir() or current.is_symlink():
            return
        git_marker = current / ".git"
        if git_marker.exists():
            resolved = current.resolve(strict=False)
            if resolved not in seen:
                seen.add(resolved)
                repos.append(resolved)
            return
        try:
            for child in sorted(current.iterdir(), key=lambda p: p.name.lower()):
                if child.name.startswith(".") or child.name in ("node_modules", "target", "build", "DerivedData"):
                    continue
                if child.is_dir() and not child.is_symlink():
                    walk(child, depth + 1)
        except OSError:
            pass

    for raw in workspace_roots:
        root = expand_path(raw)
        if root.is_dir():
            walk(root, 0)
    return repos


def parse_premerge_decl(text: str) -> tuple[str | None, str | None]:
    """Mirror git-ship: ("command", cmd), ("none", None), ("invalid", line) or (None, None) when absent."""
    for line in text.splitlines():
        m = PREMERGE_DECL_RE.match(line)
        if not m:
            continue
        value = m.group("value")
        lead = re.match(r"`([^`]+)`\s*(?P<rest>.*)$", value)
        if lead and lead.group(1).strip() and (not lead.group("rest") or lead.group("rest")[0] in PREMERGE_NOTE_START):
            return "command", lead.group(1).strip()
        if "`" not in value and PREMERGE_NONE_RE.match(value):
            return "none", None
        return "invalid", line.strip()
    return None, None


def premerge_decl_for_repo(repo: Path) -> tuple[str | None, str | None, str | None]:
    """Read AGENTS.md first, then CLAUDE.md. Returns (kind, value, file_name); file_name is None if neither exists."""
    first_existing: str | None = None
    for name in PREMERGE_DECL_FILES:
        doc = repo / name
        if not doc.is_file():
            continue
        if first_existing is None:
            first_existing = name
        try:
            text = doc.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        kind, value = parse_premerge_decl(text)
        if kind:
            return kind, value, name
    return None, None, first_existing


def matches_any_glob(name: str, globs: list[str]) -> bool:
    for pattern in globs:
        if fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(name.lower(), pattern.lower()):
            return True
    return False


def resolve_gauge_id_for_path(
    target_path: Path | str,
    gauges: list[dict[str, Any]] | None,
    fallback: str = "mac-data",
) -> str:
    """Resolve gauge_id by longest mount_path component-prefix match against storage_gauges."""
    if not gauges:
        return fallback
    raw_str = str(target_path or "").strip()
    if not raw_str or not raw_str.startswith(("/", "~", ".")):
        return fallback
    resolved = expand_path(raw_str)
    best_id = ""
    best_key = (-1, -1)
    for g in gauges:
        gid = str(g.get("id") or "").strip()
        mount_raw = str(g.get("mount_path") or "").strip()
        if not gid or not mount_raw:
            continue
        mount_p = expand_path(mount_raw)
        if resolved == mount_p or mount_p in resolved.parents:
            cand_key = (len(mount_p.parts), len(str(mount_p)))
            if cand_key > best_key:
                best_key = cand_key
                best_id = gid
    return best_id or fallback


def deduplicate_candidate_ids(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ensure candidate IDs are globally unique within a snapshot.

    The first occurrence keeps its human-readable ID; collisions append the first 6 hex chars
    of SHA-1 over the canonical path/spec.
    """
    seen: set[str] = set()
    for cand in candidates:
        base_id = str(cand.get("id") or "candidate").strip() or "candidate"
        if base_id not in seen:
            cand["id"] = base_id
            seen.add(base_id)
            continue
        raw_ref = str(cand.get("path_or_spec") or cand.get("path") or "")
        if raw_ref.startswith(("/", "~", ".")):
            canon = str(expand_path(raw_ref))
        else:
            canon = f"{cand.get('domain', '')}:{cand.get('category', '')}:{raw_ref}:{cand.get('title', '')}"
        short_hash = hashlib.sha1(canon.encode("utf-8")).hexdigest()[:6]
        new_id = f"{base_id}-{short_hash}"
        seq = 1
        while new_id in seen:
            seq += 1
            new_id = f"{base_id}-{short_hash}-{seq}"
        cand["id"] = new_id
        seen.add(new_id)
    return candidates


def scan_remote_repositories_and_guests(
    config: dict[str, Any],
    wl_index: dict[str, Any],
    gauges: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Generate GitHub repository and remote PVE/Docker/NAS candidates from config/fixtures, honoring whitelists."""
    fixtures = config.get("probe_fixtures", {})
    if not isinstance(fixtures, dict):
        fixtures = {}

    candidates: list[dict[str, Any]] = []

    raw_gh_repos: list[Any] = []
    if isinstance(fixtures.get("github_repos"), list):
        raw_gh_repos.extend(fixtures["github_repos"])
    if isinstance(config.get("github_candidates"), list):
        raw_gh_repos.extend(config["github_candidates"])

    for entry in raw_gh_repos:
        if isinstance(entry, str) and entry.strip():
            rec: dict[str, Any] = {"repo": entry.strip()}
        elif isinstance(entry, dict):
            rec = dict(entry)
        else:
            continue

        spec = str(
            rec.get("path_or_spec")
            or rec.get("repo")
            or rec.get("full_name")
            or rec.get("path")
            or rec.get("name")
            or ""
        ).strip()
        if not spec:
            continue
        slug = re.sub(r"[^a-z0-9]+", "-", spec.lower()).strip("-")[:40] or "repo"
        cand: dict[str, Any] = {
            "id": str(rec.get("id") or f"gh-repo-{slug}"),
            "domain": str(rec.get("domain") or "github"),
            "category": str(rec.get("category") or "github_repo"),
            "action_type": str(rec.get("action_type") or "optimize"),
            "tier": str(rec.get("tier") or "C"),
            "title": str(rec.get("title") or f"GitHub 仓库治理候选：{spec}"),
            "repo": spec,
            "path": str(rec.get("path") or spec),
            "path_or_spec": spec,
            "size_bytes": int(rec.get("size_bytes") or 0),
            "size_truncated": False,
            "latest_mtime": int(rec.get("latest_mtime") or 0),
            "gauge_id": str(rec.get("gauge_id") or ""),
            "whitelisted": bool(rec.get("whitelisted", False)),
            "whitelist_note": str(rec.get("whitelist_note") or ""),
            "evidence": str(rec.get("evidence") or f"远端仓库 {spec} 待复核归档、可见性或分支清理。"),
        }
        if "default_checked" in rec:
            cand["default_checked"] = bool(rec["default_checked"])
        else:
            cand["default_checked"] = cand["tier"] == "A" and not cand["whitelisted"]
        apply_candidate_whitelist(cand, wl_index)
        candidates.append(cand)

    raw_guests: list[Any] = []
    for key in ("homelab_guests", "pve_guests", "docker_containers", "nas_candidates", "remote_candidates"):
        if isinstance(fixtures.get(key), list):
            raw_guests.extend(fixtures[key])
    if isinstance(config.get("homelab_candidates"), list):
        raw_guests.extend(config["homelab_candidates"])

    for entry in raw_guests:
        if isinstance(entry, str) and entry.strip():
            rec = {"path_or_spec": entry.strip()}
        elif isinstance(entry, dict):
            rec = dict(entry)
        else:
            continue

        guest_id = rec.get("guest_id") if rec.get("guest_id") is not None else rec.get("vmid", rec.get("ctid"))
        name = str(rec.get("name") or rec.get("guest_name") or rec.get("container_name") or "").strip()
        spec = str(
            rec.get("path_or_spec")
            or rec.get("path")
            or (f"pve:{guest_id}" if guest_id is not None else name)
        ).strip()
        if not spec and guest_id is None and not name:
            continue

        ident_for_slug = str(guest_id if guest_id is not None else (name or spec))
        slug = re.sub(r"[^a-z0-9]+", "-", ident_for_slug.lower()).strip("-")[:40] or "guest"
        default_gauge = (
            resolve_gauge_id_for_path(spec, gauges, fallback="mac-data")
            if spec.startswith(("/", "~"))
            else str(rec.get("gauge_id") or "")
        )
        cand = {
            "id": str(rec.get("id") or f"homelab-guest-{slug}"),
            "domain": str(rec.get("domain") or "nas"),
            "category": str(rec.get("category") or "homelab_guest"),
            "action_type": str(rec.get("action_type") or "cleanup"),
            "tier": str(rec.get("tier") or "C"),
            "title": str(rec.get("title") or f"远端容器/虚拟机候选：{name or spec}"),
            "path": str(rec.get("path") or spec),
            "path_or_spec": spec,
            "size_bytes": int(rec.get("size_bytes") or 0),
            "size_truncated": False,
            "latest_mtime": int(rec.get("latest_mtime") or 0),
            "gauge_id": str(rec.get("gauge_id") if rec.get("gauge_id") is not None else default_gauge),
            "whitelisted": bool(rec.get("whitelisted", False)),
            "whitelist_note": str(rec.get("whitelist_note") or ""),
            "evidence": str(rec.get("evidence") or f"远端实例 {name or spec} 待确认状态与空间占用。"),
        }
        if guest_id is not None:
            cand["guest_id"] = guest_id
        if name:
            cand["name"] = name
        if spec.startswith(("/", "~")):
            p_wl, p_note = check_path_whitelisted(expand_path(spec), wl_index)
            if p_wl:
                cand["whitelisted"] = True
                cand["whitelist_note"] = p_note
        if "default_checked" in rec:
            cand["default_checked"] = bool(rec["default_checked"])
        else:
            cand["default_checked"] = cand["tier"] == "A" and not cand["whitelisted"]
        apply_candidate_whitelist(cand, wl_index)
        candidates.append(cand)

    return candidates


def scan_workspaces_and_backups(
    config: dict[str, Any],
    wl_index: dict[str, Any],
    gauges: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    workspace_roots = config.get("workspace_roots", [])
    thresholds = config.get("thresholds", {})
    min_git_pack_bytes = int(float(thresholds.get("min_git_pack_mb", 100)) * 1024 * 1024)
    min_build_bytes = int(float(thresholds.get("min_build_artifact_mb", 100)) * 1024 * 1024)
    backup_pipelines = config.get("backup_pipelines", [])
    active_gauges = gauges if gauges is not None else collect_storage_gauges(config)

    candidates: list[dict[str, Any]] = []
    dir_sizes: dict[str, int] = {}

    repos = iter_git_repos(workspace_roots)
    for repo in repos:
        repo_slug = re.sub(r"[^a-z0-9]+", "-", repo.name.lower()).strip("-")
        whitelisted_repo, wl_repo_note = check_path_whitelisted(repo, wl_index)
        if not whitelisted_repo:
            whitelisted_repo, wl_repo_note = check_repo_whitelisted(repo, wl_index)

        # 1. Check .git/objects/pack size
        git_dir = repo / ".git"
        if git_dir.is_dir():
            pack_dir = git_dir / "objects" / "pack"
            if pack_dir.is_dir():
                pack_bytes, truncated, latest_mtime = bounded_dir_size(pack_dir)
                dir_sizes[str(pack_dir)] = pack_bytes
                if pack_bytes >= min_git_pack_bytes:
                    pack_cand: dict[str, Any] = {
                        "id": f"git-pack-{repo_slug}",
                        "domain": "projects",
                        "category": "git_pack_bloat",
                        "action_type": "optimize",
                        "tier": "B",
                        "default_checked": False,
                        "title": f"仓库 Git Pack 体积较大：{repo.name} (.git/objects/pack)",
                        "path": str(pack_dir),
                        "size_bytes": pack_bytes,
                        "reclaimable_bytes": 0,
                        "size_truncated": truncated,
                        "latest_mtime": latest_mtime,
                        "gauge_id": resolve_gauge_id_for_path(pack_dir, active_gauges),
                        "whitelisted": whitelisted_repo,
                        "whitelist_note": wl_repo_note,
                        "evidence": (
                            f"{repo.name} 的 .git/objects/pack 达 {pack_bytes / (1024**2):.1f} MiB；"
                            "可在无并发操作时执行普通 `git gc`（不加 --prune=now，保留别的会话可能还在用的松散对象），"
                            "或检查是否误提交过大二进制文件。"
                        ),
                    }
                    candidates.append(pack_cand)

        # 2. Check AGENTS.md (then CLAUDE.md) pre-merge check declaration, same rule as git-ship
        decl_kind, decl_value, decl_file = premerge_decl_for_repo(repo)
        if decl_file is not None and decl_kind not in ("command", "none"):
            doc_path = repo / decl_file
            invalid = decl_kind == "invalid"
            try:
                doc_mtime = int(doc_path.stat().st_mtime)
            except OSError:
                doc_mtime = 0
            candidates.append(
                {
                    "id": f"doc-premerge-{repo_slug}",
                    "domain": "projects",
                    "category": "governance_doc",
                    "action_type": "optimize",
                    "tier": "B",
                    "default_checked": False,
                    "title": (
                        f"修正合入前检查写法：{repo.name}/{decl_file}"
                        if invalid
                        else f"补充合入前检查声明：{repo.name}/{decl_file}"
                    ),
                    "path": str(doc_path),
                    "size_bytes": 0,
                    "size_truncated": False,
                    "latest_mtime": doc_mtime,
                    "gauge_id": "",
                    "whitelisted": whitelisted_repo,
                    "whitelist_note": wl_repo_note,
                    "evidence": (
                        f"{decl_file} 里的合入前检查写法 git-ship 认不出：{decl_value}；"
                        "写成行首「合入前检查：`命令`」（多条命令用 && 连成一条）或「合入前检查：无」。"
                        if invalid
                        else "AGENTS.md 和 CLAUDE.md 都没有行首的「合入前检查：`命令`」或「合入前检查：无」约定行。"
                    ),
                }
            )

    # 3. Scan top-level and repo-level build/preservation directories & backup exclusion drift
    for raw_root in workspace_roots:
        ws_root = expand_path(raw_root)
        if not ws_root.is_dir():
            continue
        # Check directories up to depth 3 matching BUILD_DIR_PATTERNS
        stack: list[tuple[Path, int]] = [(ws_root, 0)]
        while stack:
            curr, depth = stack.pop()
            if depth > 3:
                continue
            try:
                children = sorted(curr.iterdir(), key=lambda p: p.name.lower())
            except OSError:
                continue
            for child in children:
                if child.is_symlink() or not child.is_dir():
                    continue
                if child.name == ".git":
                    continue
                if matches_any_glob(child.name, list(BUILD_DIR_PATTERNS)):
                    size_bytes, truncated, latest_mtime = bounded_dir_size(child)
                    dir_sizes[str(child)] = size_bytes
                    if size_bytes >= min_build_bytes:
                        whitelisted, wl_note = check_path_whitelisted(child, wl_index)
                        if not whitelisted:
                            whitelisted, wl_note = check_repo_whitelisted(child, wl_index)
                        is_manual_archive = matches_any_glob(child.name, list(MANUAL_CONFIRM_BUILD_PATTERNS))
                        tier = "C" if is_manual_archive else "A"
                        default_checked = False if (is_manual_archive or whitelisted) else True
                        gauge_id = resolve_gauge_id_for_path(child, active_gauges)
                        slug = re.sub(r"[^a-z0-9]+", "-", f"{curr.name}-{child.name}".lower()).strip("-")[:36]
                        candidates.append(
                            {
                                "id": f"build-{slug}",
                                "domain": "projects",
                                "category": "build_artifact",
                                "action_type": "cleanup",
                                "tier": tier,
                                "default_checked": default_checked,
                                "title": (
                                    f"归档/留档构建目录（需人工确认）：{curr.name}/{child.name}"
                                    if is_manual_archive
                                    else f"可重建构建产物：{curr.name}/{child.name}"
                                ),
                                "path": str(child),
                                "size_bytes": size_bytes,
                                "size_truncated": truncated,
                                "latest_mtime": latest_mtime,
                                "gauge_id": gauge_id,
                                "whitelisted": whitelisted,
                                "whitelist_note": wl_note,
                                "evidence": (
                                    f"匹配归档/验收留档目录模式 ({child.name})，可能含签名发布包或留档证据，默认不勾选，请人工确认。"
                                    if is_manual_archive
                                    else f"匹配构建/产物目录模式 ({child.name})，可通过工程构建命令重新生成。"
                                ),
                            }
                        )
                        # Check backup exclusion drift against configured pipelines
                        for pipe in backup_pipelines:
                            raw_watched = pipe.get("watched_roots")
                            if isinstance(raw_watched, list) and raw_watched:
                                child_resolved = child.resolve(strict=False)
                                watched_paths = [expand_path(str(wr)) for wr in raw_watched if str(wr).strip()]
                                if watched_paths and not any(
                                    child_resolved == wp or wp in child_resolved.parents for wp in watched_paths
                                ):
                                    continue
                            globs = pipe.get("expected_exclude_globs", [])
                            script_raw = pipe.get("script_path", "")
                            script_text = ""
                            if script_raw:
                                sp = expand_path(script_raw)
                                if sp.is_file():
                                    try:
                                        script_text = sp.read_text(encoding="utf-8", errors="replace")
                                    except OSError:
                                        pass
                            covered_by_glob = matches_any_glob(child.name, globs)
                            covered_in_script = (not script_text) or (child.name in script_text) or any(
                                g.strip("*") in script_text for g in globs if g.strip("*") and fnmatch.fnmatch(child.name, g)
                            )
                            if not (covered_by_glob and covered_in_script):
                                pipe_id = pipe.get("id", "backup")
                                candidates.append(
                                    {
                                        "id": f"drift-{pipe_id}-{slug}",
                                        "domain": "projects",
                                        "category": "backup_exclusion_drift",
                                        "action_type": "optimize",
                                        "tier": "B",
                                        "default_checked": False,
                                        "title": f"备份排除规则疑似漏排：{child.name} → {pipe.get('label', pipe_id)}",
                                        "path": str(child),
                                        "size_bytes": size_bytes,
                                        "reclaimable_bytes": 0,
                                        "size_truncated": truncated,
                                        "latest_mtime": latest_mtime,
                                        "gauge_id": gauge_id,
                                        "whitelisted": whitelisted,
                                        "whitelist_note": wl_note,
                                        "evidence": (
                                            f"目录 {child} ({size_bytes / (1024**2):.1f} MiB) 未被备份管线 "
                                            f"{pipe.get('label', pipe_id)} 的排除规则完整覆盖，可能穿透备份撑爆远端空间。"
                                        ),
                                    }
                                )
                else:
                    stack.append((child, depth + 1))

    deduplicate_candidate_ids(candidates)
    return candidates, dir_sizes


def collect_storage_gauges(config: dict[str, Any]) -> list[dict[str, Any]]:
    gauges_out: list[dict[str, Any]] = []
    for g in config.get("storage_gauges", []):
        mount_raw = g.get("mount_path", "")
        mount_path = expand_path(mount_raw) if mount_raw else Path("/")
        entry: dict[str, Any] = {
            "id": g.get("id", "storage"),
            "label": g.get("label", mount_raw or "Storage"),
            "mount_path": str(mount_path),
            "warn_used_pct": float(g.get("warn_used_pct", 85.0)),
            "min_free_gib": g.get("min_free_gib"),
        }
        try:
            st = os.statvfs(mount_path)
            total_bytes = st.f_blocks * st.f_frsize
            free_bytes = st.f_bavail * st.f_frsize
            used_bytes = max(0, total_bytes - (st.f_bfree * st.f_frsize))
            used_pct = round((used_bytes / total_bytes) * 100.0, 2) if total_bytes > 0 else 0.0
            entry.update(
                {
                    "status": "ok",
                    "total_bytes": total_bytes,
                    "used_bytes": used_bytes,
                    "free_bytes": free_bytes,
                    "used_pct": used_pct,
                }
            )
        except OSError as exc:
            entry.update(
                {
                    "status": "unavailable",
                    "error": str(exc),
                    "total_bytes": 0,
                    "used_bytes": 0,
                    "free_bytes": 0,
                    "used_pct": 0.0,
                }
            )
        gauges_out.append(entry)
    return gauges_out


def write_secure_json(path: Path, payload: dict[str, Any], overwrite: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_TRUNC if overwrite else os.O_EXCL)
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise ConfigError(f"Output file already exists (pass --overwrite to replace): {path}") from exc
    with os.fdopen(fd, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)
        fp.write("\n")


def cmd_scan_local(args: argparse.Namespace) -> int:
    config = load_topology(args.config)
    wl_index = build_whitelist_index(config)
    app_roots = config.get("app_roots", ["/Applications", "~/Applications"])
    installed = collect_installed_apps(app_roots)
    cli_probe_override = False if getattr(args, "skip_probes", False) else None
    run_probes = _should_run_system_probes(config, cli_override=cli_probe_override)

    gauges = collect_storage_gauges(config)
    orphan_candidates, orphan_sizes = scan_app_orphans(config, wl_index, installed=installed)
    app_inventory, uninstall_candidates, app_sizes = scan_apps_inventory_and_uninstall(
        config, wl_index, installed=installed, run_probes=run_probes
    )
    cache_candidates, cache_sizes = scan_global_caches(config, wl_index)
    launch_candidates = scan_launch_items(config, wl_index, installed=installed)
    app_update_candidates = scan_app_updates(config, wl_index, app_inventory, run_probes=run_probes)
    env_groups, env_update_candidates = scan_env_and_dependencies(config, wl_index, run_probes=run_probes)
    peripherals, hw_candidates = scan_peripherals_and_firmware(config, wl_index, run_probes=run_probes)
    ws_candidates, ws_sizes = scan_workspaces_and_backups(config, wl_index, gauges=gauges)
    remote_candidates = scan_remote_repositories_and_guests(config, wl_index, gauges=gauges)

    all_candidates = (
        orphan_candidates
        + uninstall_candidates
        + cache_candidates
        + launch_candidates
        + app_update_candidates
        + env_update_candidates
        + hw_candidates
        + ws_candidates
        + remote_candidates
    )
    for cand in all_candidates:
        raw_p = str(cand.get("path") or "").strip()
        if raw_p.startswith(("/", "~")) and cand.get("gauge_id") == "mac-data" and gauges:
            cand["gauge_id"] = resolve_gauge_id_for_path(raw_p, gauges)
        apply_candidate_whitelist(cand, wl_index)
    deduplicate_candidate_ids(all_candidates)
    all_sizes = {**orphan_sizes, **app_sizes, **cache_sizes, **ws_sizes}

    freq_apps = [a for a in app_inventory if a.get("usage_status") == "frequent"]
    infreq_apps = [a for a in app_inventory if a.get("usage_status") == "infrequent"]
    app_inventory_summary = {
        "total_apps": len(app_inventory),
        "frequent_count": len(freq_apps),
        "infrequent_count": len(infreq_apps),
        "total_bytes": sum(int(a.get("total_bytes", 0)) for a in app_inventory),
        "apps": app_inventory,
    }

    snapshot = {
        "schema": SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "config_path": str(Path(args.config).resolve()),
        "storage_gauges": gauges,
        "candidates": all_candidates,
        "app_inventory": app_inventory_summary,
        "environments": env_groups,
        "peripherals": peripherals,
        "directory_sizes": all_sizes,
    }

    if args.output:
        write_secure_json(Path(args.output), snapshot, overwrite=args.overwrite)
    else:
        json.dump(snapshot, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    before_path = Path(args.before)
    after_path = Path(args.after)
    try:
        before = json.loads(before_path.read_text(encoding="utf-8"))
        after = json.loads(after_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ConfigError(f"Failed to read snapshot files for diff: {exc}") from exc

    before_gauges = {g["id"]: g for g in before.get("storage_gauges", []) if "id" in g}
    gauge_deltas: list[dict[str, Any]] = []
    for g_after in after.get("storage_gauges", []):
        gid = g_after.get("id")
        g_before = before_gauges.get(gid)
        if not g_before or g_before.get("status") != "ok" or g_after.get("status") != "ok":
            continue
        used_delta = int(g_after.get("used_bytes", 0)) - int(g_before.get("used_bytes", 0))
        free_delta = int(g_after.get("free_bytes", 0)) - int(g_before.get("free_bytes", 0))
        pct_delta = round(float(g_after.get("used_pct", 0.0)) - float(g_before.get("used_pct", 0.0)), 2)
        gauge_deltas.append(
            {
                "id": gid,
                "label": g_after.get("label", gid),
                "before_used_bytes": g_before.get("used_bytes", 0),
                "after_used_bytes": g_after.get("used_bytes", 0),
                "used_delta_bytes": used_delta,
                "free_delta_bytes": free_delta,
                "used_pct_delta": pct_delta,
            }
        )

    before_sizes: dict[str, int] = before.get("directory_sizes", {})
    after_sizes: dict[str, int] = after.get("directory_sizes", {})
    growth_rows: list[dict[str, Any]] = []
    for path_str, after_sz in after_sizes.items():
        before_sz = before_sizes.get(path_str, 0)
        delta = int(after_sz) - int(before_sz)
        if delta != 0:
            growth_rows.append(
                {
                    "path": path_str,
                    "before_bytes": int(before_sz),
                    "after_bytes": int(after_sz),
                    "delta_bytes": delta,
                    "is_new": path_str not in before_sizes,
                }
            )
    growth_rows.sort(key=lambda r: r["delta_bytes"], reverse=True)

    before_ids = {c["id"] for c in before.get("candidates", []) if "id" in c}
    after_ids = {c["id"] for c in after.get("candidates", []) if "id" in c}

    diff_payload = {
        "schema": SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "before_generated_at": before.get("generated_at", ""),
        "after_generated_at": after.get("generated_at", ""),
        "gauge_deltas": gauge_deltas,
        "top_growth_paths": [r for r in growth_rows if r["delta_bytes"] > 0][:10],
        "top_shrunk_paths": [r for r in reversed(growth_rows) if r["delta_bytes"] < 0][:10],
        "new_candidate_ids": sorted(after_ids - before_ids),
        "resolved_candidate_ids": sorted(before_ids - after_ids),
    }

    if args.output:
        write_secure_json(Path(args.output), diff_payload, overwrite=args.overwrite)
    else:
        json.dump(diff_payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    return 0


def check_open_handles(target: Path) -> list[str]:
    """Return process names holding open handles inside target via lsof when available."""
    try:
        proc = subprocess.run(
            ["lsof", "-F", "c", "+D", str(target)],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
    except Exception:
        return []
    if proc.returncode != 0 and not proc.stdout:
        return []
    comms: set[str] = set()
    for line in proc.stdout.splitlines():
        if line.startswith("c") and len(line) > 1:
            comms.add(line[1:])
    return sorted(comms)


def check_git_dirty(target: Path) -> bool:
    try:
        proc = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(target), "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
        if proc.returncode != 0:
            return True
        return bool(proc.stdout.strip())
    except Exception:
        return True


def is_git_worktree_or_repo(target: Path, item: dict[str, Any]) -> bool:
    if target.is_dir() and (target / ".git").exists():
        return True
    cat = str(item.get("category") or "").lower()
    kind = str(item.get("kind") or "").lower()
    return "worktree" in cat or kind == "worktree"


def find_enclosing_git_root(target: Path) -> Path | None:
    """Walk up from target to find an enclosing Git repository or worktree root (.git dir or file)."""
    curr = target.resolve(strict=False)
    for candidate in (curr, *curr.parents):
        if candidate in (Path("/"), Path("/Users"), Path("/Volumes"), Path("/tmp"), Path("/private/tmp")):
            break
        try:
            if (candidate / ".git").exists():
                return candidate
        except OSError:
            continue
    return None


def _bytes_mentions_exact_path(blob: bytes, path_bytes: bytes) -> bool:
    """Return True if path_bytes appears in blob at a path boundary (not as a prefix of path-v2)."""
    if not path_bytes:
        return False
    pos = 0
    n = len(path_bytes)
    total = len(blob)
    while True:
        idx = blob.find(path_bytes, pos)
        if idx < 0:
            return False
        end = idx + n
        if end >= total:
            return True
        nxt = blob[end]
        # Reject if followed by filename continuation chars (alphanumeric, UTF-8 multibyte, '+', '-', '.', '_', '~')
        if (
            (48 <= nxt <= 57)
            or (65 <= nxt <= 90)
            or (97 <= nxt <= 122)
            or nxt >= 128
            or nxt in (43, 45, 46, 95, 126)
        ):
            pos = idx + 1
            continue
        return True


def _file_mentions_any_path(file_path: Path, needles: list[bytes], chunk_bytes: int = 131072) -> bool:
    if not needles:
        return False
    try:
        st = file_path.stat()
        with file_path.open("rb") as fp:
            if st.st_size <= chunk_bytes * 2:
                blob = fp.read()
                return any(_bytes_mentions_exact_path(blob, nb) for nb in needles)
            head = fp.read(chunk_bytes)
            if any(_bytes_mentions_exact_path(head, nb) for nb in needles):
                return True
            fp.seek(max(0, st.st_size - chunk_bytes))
            tail = fp.read(chunk_bytes)
            return any(_bytes_mentions_exact_path(tail, nb) for nb in needles)
    except OSError:
        return False


def _claude_project_slug(path_obj: Path) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", str(path_obj)).strip("-").lower()


def _find_orca_workspace_root(target: Path) -> Path | None:
    """If target sits inside .../orca/workspaces/<repo>/<worktree>, return that worktree root."""
    for candidate in (Path(os.path.abspath(target)), target.resolve(strict=False)):
        parts = candidate.parts
        for idx in range(len(parts) - 1):
            if parts[idx].lower() == "orca" and parts[idx + 1].lower() == "workspaces":
                rem = len(parts) - (idx + 2)
                if rem >= 2:
                    return Path(*parts[: idx + 4])
                if rem == 1:
                    return Path(*parts[: idx + 3])
    return None


def _is_linked_git_worktree(repo_root: Path | None) -> bool:
    if repo_root is None:
        return False
    try:
        git_marker = repo_root / ".git"
        if git_marker.is_file():
            return True
    except OSError:
        pass
    return ".worktrees" in repo_root.parts


def _is_temp_fixture_without_injected_home(target: Path, home_dir: Path) -> bool:
    tmp_prefixes = (
        Path("/tmp"),
        Path("/private/tmp"),
        Path("/var/folders"),
        Path("/private/var/folders"),
        Path(tempfile.gettempdir()).resolve(strict=False),
    )
    resolved_target = target.resolve(strict=False)
    resolved_home = home_dir.resolve(strict=False)
    target_in_tmp = any(resolved_target == p or p in resolved_target.parents for p in tmp_prefixes)
    home_in_tmp = any(resolved_home == p or p in resolved_home.parents for p in tmp_prefixes)
    return target_in_tmp and not home_in_tmp


def _resolve_ai_session_roots(
    target: Path,
    config: dict[str, Any] | None = None,
    session_roots: dict[str, list[Path]] | None = None,
) -> dict[str, list[Path]]:
    """Resolve session directories for Codex, Claude Code, and Antigravity without touching real host home in isolated fixture tests."""
    if session_roots is not None:
        return {
            host: [expand_path(str(p)) for p in paths if str(p).strip()]
            for host, paths in session_roots.items()
        }

    cfg_roots = (config or {}).get("ai_session_roots")
    if isinstance(cfg_roots, dict):
        out_cfg: dict[str, list[Path]] = {}
        for host in ("codex", "claude", "antigravity"):
            raw_val = cfg_roots.get(host, [])
            if isinstance(raw_val, str) and raw_val.strip():
                out_cfg[host] = [expand_path(raw_val)]
            elif isinstance(raw_val, list):
                out_cfg[host] = [expand_path(str(x)) for x in raw_val if str(x).strip()]
        return out_cfg

    env_home = os.environ.get("FSM_SESSION_HOME")
    env_codex = os.environ.get("FSM_CODEX_SESSIONS_DIR")
    env_claude = os.environ.get("FSM_CLAUDE_PROJECTS_DIR")
    env_agy = os.environ.get("FSM_ANTIGRAVITY_BRAIN_DIRS")

    if any(v is not None for v in (env_home, env_codex, env_claude, env_agy)):
        if env_home is not None and env_home.strip().lower() in ("", "none", "off", "0", "false"):
            base_roots: dict[str, list[Path]] = {"codex": [], "claude": [], "antigravity": []}
        elif env_home:
            h = expand_path(env_home)
            base_roots = {
                "codex": [h / ".codex" / "sessions"],
                "claude": [h / ".claude" / "projects"],
                "antigravity": [
                    h / ".gemini" / "antigravity-cli" / "brain",
                    h / ".gemini" / "antigravity" / "brain",
                ],
            }
        else:
            base_roots = {"codex": [], "claude": [], "antigravity": []}

        if env_codex is not None:
            base_roots["codex"] = [expand_path(p) for p in env_codex.split(os.pathsep) if p.strip()]
        if env_claude is not None:
            base_roots["claude"] = [expand_path(p) for p in env_claude.split(os.pathsep) if p.strip()]
        if env_agy is not None:
            base_roots["antigravity"] = [expand_path(p) for p in env_agy.split(os.pathsep) if p.strip()]
        return base_roots

    home_dir = Path(os.path.expanduser("~"))
    if _is_temp_fixture_without_injected_home(target, home_dir):
        return {"codex": [], "claude": [], "antigravity": []}

    return {
        "codex": [home_dir / ".codex" / "sessions"],
        "claude": [home_dir / ".claude" / "projects"],
        "antigravity": [
            home_dir / ".gemini" / "antigravity-cli" / "brain",
            home_dir / ".gemini" / "antigravity" / "brain",
        ],
    }


def check_codex_session_activity(
    repo_root: Path,
    cutoff_ts: float,
    sessions_dir: Path | None = None,
) -> tuple[bool, str]:
    """Check if recent Codex session rollouts (~/.codex/sessions/**/*.jsonl) reference repo_root after cutoff_ts."""
    if sessions_dir is not None:
        roots = [sessions_dir]
    else:
        roots = _resolve_ai_session_roots(repo_root).get("codex", [])
    needles: list[bytes] = []
    for p in (Path(os.path.abspath(repo_root)), repo_root.resolve(strict=False)):
        b = str(p).rstrip("/").encode("utf-8", errors="ignore")
        if b and b not in needles:
            needles.append(b)

    for sess_root in roots:
        if not sess_root.is_dir():
            continue
        try:
            for jsonl in sess_root.rglob("*.jsonl"):
                try:
                    st = jsonl.stat()
                except OSError:
                    continue
                if st.st_mtime < cutoff_ts:
                    continue
                if _file_mentions_any_path(jsonl, needles):
                    age_min = max(0.0, (time.time() - st.st_mtime) / 60.0)
                    return (
                        True,
                        f"Active Codex session ({jsonl.name}) referenced {repo_root} {age_min:.1f}m ago; whole worktree is protected.",
                    )
        except OSError:
            continue
    return False, ""


def check_ai_session_activity(
    target: Path,
    enclosing_repo: Path | None,
    cutoff_ts: float,
    *,
    config: dict[str, Any] | None = None,
    session_roots: dict[str, list[Path]] | None = None,
) -> tuple[bool, str]:
    """Read-only detection of active AI client sessions (Codex, Claude Code, Antigravity) and active Orca/Git worktrees."""
    orca_root = _find_orca_workspace_root(target)
    anchor_paths: list[Path] = []
    for candidate in (enclosing_repo, orca_root, target):
        if candidate is None:
            continue
        for variant in (Path(os.path.abspath(candidate)), candidate.resolve(strict=False)):
            if variant not in anchor_paths:
                anchor_paths.append(variant)

    needles: list[bytes] = []
    claude_slugs: set[str] = set()
    for p in anchor_paths:
        raw_b = str(p).rstrip("/").encode("utf-8", errors="ignore")
        if raw_b and raw_b not in needles:
            needles.append(raw_b)
        c_slug = _claude_project_slug(p)
        if c_slug:
            claude_slugs.add(c_slug)

    roots = _resolve_ai_session_roots(target, config=config, session_roots=session_roots)
    display_anchor = enclosing_repo or orca_root or target

    # 1. Codex sessions (~/.codex/sessions/**/*.jsonl)
    for codex_root in roots.get("codex", []):
        if not codex_root.is_dir():
            continue
        try:
            for jsonl in codex_root.rglob("*.jsonl"):
                try:
                    st = jsonl.stat()
                except OSError:
                    continue
                if st.st_mtime < cutoff_ts:
                    continue
                if _file_mentions_any_path(jsonl, needles):
                    age_min = max(0.0, (time.time() - st.st_mtime) / 60.0)
                    return (
                        True,
                        f"Active Codex session ({jsonl.name}) referenced {display_anchor} {age_min:.1f}m ago.",
                    )
        except OSError:
            continue

    # 2. Claude Code sessions (~/.claude/projects/**/*.jsonl)
    for claude_root in roots.get("claude", []):
        if not claude_root.is_dir():
            continue
        try:
            for jsonl in claude_root.rglob("*.jsonl"):
                try:
                    st = jsonl.stat()
                except OSError:
                    continue
                if st.st_mtime < cutoff_ts:
                    continue
                parent_slug = _claude_project_slug(jsonl.parent.name)
                matched_by_dir = bool(parent_slug and parent_slug in claude_slugs)
                if matched_by_dir or _file_mentions_any_path(jsonl, needles):
                    age_min = max(0.0, (time.time() - st.st_mtime) / 60.0)
                    return (
                        True,
                        f"Active Claude Code session ({jsonl.name}) referenced {display_anchor} {age_min:.1f}m ago.",
                    )
        except OSError:
            continue

    # 3. Antigravity sessions (~/.gemini/antigravity-cli/brain/ and ~/.gemini/antigravity/brain/)
    agy_exts = {".jsonl", ".log", ".json", ".txt"}
    for agy_root in roots.get("antigravity", []):
        if not agy_root.is_dir():
            continue
        try:
            for log_file in agy_root.rglob("*"):
                if log_file.suffix.lower() not in agy_exts:
                    continue
                try:
                    st = log_file.stat()
                except OSError:
                    continue
                if not stat.S_ISREG(st.st_mode) or st.st_mtime < cutoff_ts:
                    continue
                if _file_mentions_any_path(log_file, needles):
                    age_min = max(0.0, (time.time() - st.st_mtime) / 60.0)
                    return (
                        True,
                        f"Active Antigravity session ({log_file.name}) referenced {display_anchor} {age_min:.1f}m ago.",
                    )
        except OSError:
            continue

    # 4. Active Orca workspace (orca/workspaces) or linked Git worktree with recent file/git activity
    wt_root = orca_root if orca_root is not None else (enclosing_repo if _is_linked_git_worktree(enclosing_repo) else None)
    if wt_root is not None and wt_root.exists():
        wt_mtime = git_metadata_latest_mtime(wt_root)
        _, _, tree_mtime = bounded_dir_size(wt_root, max_entries=4000, max_seconds=1.5)
        wt_mtime = max(wt_mtime, tree_mtime)
        if wt_mtime > cutoff_ts:
            age_min = max(0.0, (time.time() - wt_mtime) / 60.0)
            host_label = "Orca workspace (orca/workspaces)" if orca_root is not None else "Git worktree"
            return (
                True,
                f"Active {host_label} session: worktree {wt_root} modified {age_min:.1f}m ago; whole worktree is protected.",
            )

    return False, ""


def git_metadata_latest_mtime(target: Path) -> int:
    """Inspect .git metadata files (index, HEAD, worktree gitdir) so large repos >10k files still catch recent git activity."""
    git_entry = target / ".git"
    if not git_entry.exists():
        return 0
    latest = 0
    candidates: list[Path] = [git_entry]
    try:
        if git_entry.is_file():
            raw = git_entry.read_text(encoding="utf-8", errors="replace").strip()
            if raw.startswith("gitdir:"):
                gitdir_raw = raw.split(":", 1)[1].strip()
                gitdir = (target / gitdir_raw).resolve(strict=False)
                candidates.extend([gitdir, gitdir / "index", gitdir / "HEAD", gitdir / "ORIG_HEAD"])
        elif git_entry.is_dir():
            candidates.extend(
                [
                    git_entry / "index",
                    git_entry / "HEAD",
                    git_entry / "FETCH_HEAD",
                    git_entry / "ORIG_HEAD",
                ]
            )
    except OSError:
        pass

    for p in candidates:
        try:
            st = os.stat(p, follow_symlinks=False)
            mtime = int(st.st_mtime)
            if mtime > latest:
                latest = mtime
        except OSError:
            pass
    return latest


def is_remote_or_non_fs_spec(raw_path: str, item: dict[str, Any]) -> bool:
    s = raw_path.strip()
    if not s:
        return True
    if item.get("domain") == "github" and not s.startswith(("/", "~", ".")):
        return True
    if not s.startswith(("/", "~", ".")) and ":" in s:
        return True
    if not s.startswith(("/", "~", ".")):
        return True
    return False


def normalize_uninstall_mode(raw_mode: str, *, context: str = "--uninstall-mode") -> str:
    norm = str(raw_mode or "").strip().lower().replace("-", "_")
    if norm not in VALID_UNINSTALL_MODES:
        raise ConfigError(
            f"Invalid uninstall mode '{raw_mode}' in {context} (expected keep-data or purge-all)"
        )
    return norm


def parse_ids_spec(
    raw_ids: str | None,
    *,
    require_non_empty: bool = False,
) -> tuple[list[str], dict[str, str]]:
    """Parse `--ids` supporting optional per-item uninstall mode suffixes (`<id>:keep-data` / `<id>:purge-all`).

    Raises ConfigError (exit code 2) on malformed entries, invalid mode suffixes, or conflicting duplicate modes.
    """
    ordered_ids: list[str] = []
    seen: set[str] = set()
    bare_ids: set[str] = set()
    per_id_modes: dict[str, str] = {}

    if raw_ids is not None:
        for raw_part in str(raw_ids).split(","):
            token = raw_part.strip()
            if not token:
                continue
            if ":" in token:
                item_id_raw, mode_raw = token.split(":", 1)
                item_id = item_id_raw.strip()
                if not item_id:
                    raise ConfigError(f"Invalid --ids token '{token}': missing candidate ID before ':'")
                if not mode_raw.strip():
                    raise ConfigError(f"Invalid --ids token '{token}': empty uninstall mode suffix after ':'")
                norm_mode = normalize_uninstall_mode(mode_raw, context=f"--ids ({token})")
                if item_id in bare_ids:
                    raise ConfigError(
                        f"Ambiguous duplicate ID '{item_id}' in --ids: cannot mix bare ID with '{token}'"
                    )
                if item_id in per_id_modes and per_id_modes[item_id] != norm_mode:
                    raise ConfigError(
                        f"Conflicting uninstall modes for '{item_id}' in --ids: "
                        f"{per_id_modes[item_id]} vs {norm_mode}"
                    )
                per_id_modes[item_id] = norm_mode
            else:
                item_id = token
                if item_id in per_id_modes:
                    raise ConfigError(
                        f"Ambiguous duplicate ID '{item_id}' in --ids: cannot mix bare ID with '{item_id}:{per_id_modes[item_id]}'"
                    )
                bare_ids.add(item_id)

            if item_id not in seen:
                seen.add(item_id)
                ordered_ids.append(item_id)

    if require_non_empty and not ordered_ids:
        raise ConfigError("--ids must list at least one candidate ID; quarantine never defaults to all candidates")

    return ordered_ids, per_id_modes


def resolve_item_paths(
    item: dict[str, Any],
    cli_uninstall_mode: str | None = None,
    item_mode_override: str | None = None,
) -> tuple[list[str], str | None]:
    """Resolve target paths for a candidate item, honoring keep_data / purge_all uninstall modes when present.

    Precedence: per-item `<id>:<mode>` override > global `--uninstall-mode` > candidate `selected_uninstall_mode` > `keep_data`.
    """
    if item_mode_override is not None:
        validated_override = normalize_uninstall_mode(item_mode_override, context="--ids")
    else:
        validated_override = None

    if cli_uninstall_mode is not None:
        validated_cli = normalize_uninstall_mode(cli_uninstall_mode, context="--uninstall-mode")
    else:
        validated_cli = None

    modes = item.get("uninstall_modes")
    if isinstance(modes, dict) and modes:
        raw_mode = validated_override or validated_cli or item.get("selected_uninstall_mode") or "keep_data"
        norm_mode = str(raw_mode).strip().lower().replace("-", "_")
        if norm_mode not in modes:
            norm_mode = "keep_data" if "keep_data" in modes else next(iter(modes.keys()))
        mode_obj = modes.get(norm_mode, {})
        if isinstance(mode_obj, dict) and isinstance(mode_obj.get("paths"), list) and mode_obj["paths"]:
            return [str(p) for p in mode_obj["paths"]], norm_mode
    elif validated_override is not None:
        raise ConfigError(
            f"Candidate '{item.get('id', 'unknown')}' does not support per-item uninstall mode '{validated_override}'"
        )
    if isinstance(item.get("paths"), list) and item.get("paths"):
        return [str(p) for p in item["paths"]], None
    p = str(item.get("path") or item.get("path_or_spec") or "").strip()
    return ([p] if p else []), None


def evaluate_single_target(
    raw_path: str,
    item: dict[str, Any],
    wl_index: dict[str, Any],
    cutoff_ts: float,
    recent_minutes: float,
    skip_lsof: bool,
    worktree_cutoff_ts: float | None = None,
    worktree_protect_minutes: float | None = None,
    *,
    config: dict[str, Any] | None = None,
    session_roots: dict[str, list[Path]] | None = None,
) -> dict[str, Any]:
    effective_raw = raw_path.strip() or str(item.get("path_or_spec") or item.get("path") or "").strip()
    display_spec = os.path.expanduser(effective_raw) if effective_raw.startswith(("~", "/")) else effective_raw

    # 1. Priority whitelist guard (runs BEFORE remote/non-fs short-circuit so whitelisted repos/guests never return SAFE_TO_EXECUTE)
    if item.get("whitelisted"):
        return {
            "path": display_spec,
            "status": "SKIPPED_WHITELIST",
            "reason": str(item.get("whitelist_note") or "Candidate is marked whitelisted"),
        }

    repo_wl, repo_note = check_repo_whitelisted(effective_raw or None, wl_index, item=item)
    if repo_wl:
        return {
            "path": display_spec,
            "status": "SKIPPED_WHITELIST",
            "reason": repo_note,
        }

    guest_wl, guest_note = check_guest_whitelisted(item, effective_raw, wl_index)
    if guest_wl:
        return {
            "path": display_spec,
            "status": "SKIPPED_WHITELIST",
            "reason": guest_note,
        }

    if is_remote_or_non_fs_spec(effective_raw, item):
        return {
            "path": effective_raw,
            "status": "SAFE_TO_EXECUTE",
            "reason": "Non-filesystem or remote action; verify via domain tool.",
        }

    raw_expanded = os.path.expanduser(effective_raw)
    target = Path(raw_expanded)
    whitelisted, wl_note = check_path_whitelisted(target, wl_index)
    if whitelisted:
        return {
            "path": str(target),
            "status": "BLOCKED_WHITELISTED",
            "reason": wl_note or "Target is in whitelist",
        }

    if not target.exists() and not target.is_symlink():
        return {
            "path": str(target),
            "status": "MISSING_ALREADY_DONE",
            "reason": "Target path no longer exists.",
        }

    if target.is_symlink():
        return {
            "path": str(target),
            "status": "BLOCKED_SYMLINK_ROOT",
            "reason": "Target root is a symbolic link; inspect link target manually.",
        }

    enclosing_repo = find_enclosing_git_root(target)
    wt_cutoff = worktree_cutoff_ts if worktree_cutoff_ts is not None else cutoff_ts
    wt_minutes = worktree_protect_minutes if worktree_protect_minutes is not None else recent_minutes

    session_cutoff = wt_cutoff if (enclosing_repo is not None or _find_orca_workspace_root(target) is not None) else cutoff_ts
    sess_active, sess_reason = check_ai_session_activity(
        target,
        enclosing_repo,
        session_cutoff,
        config=config,
        session_roots=session_roots,
    )
    if sess_active:
        return {
            "path": str(target),
            "status": "BLOCKED_ACTIVE_SESSION",
            "reason": sess_reason,
        }

    _, _, latest_mtime = bounded_dir_size(target, max_entries=10000, max_seconds=3.0)
    if target.is_dir():
        latest_mtime = max(latest_mtime, git_metadata_latest_mtime(target))

    if latest_mtime > cutoff_ts:
        age_min = max(0.0, (time.time() - latest_mtime) / 60.0)
        return {
            "path": str(target),
            "status": "BLOCKED_RECENT_ACTIVITY",
            "reason": f"Modified {age_min:.1f} minutes ago (threshold: {recent_minutes}m); another session may be active.",
        }

    # Whole-worktree cascading protection: if target is inside a Git worktree/repo
    # (e.g. target/, node_modules/, distribution/artifacts/), protect it whenever
    # the enclosing worktree has recent activity, active AI sessions, or dirty status.
    if enclosing_repo is not None:
        repo_mtime = git_metadata_latest_mtime(enclosing_repo)
        if enclosing_repo != target:
            _, _, enclosing_tree_mtime = bounded_dir_size(enclosing_repo, max_entries=4000, max_seconds=1.5)
            repo_mtime = max(repo_mtime, enclosing_tree_mtime)
        if repo_mtime > wt_cutoff:
            age_min = max(0.0, (time.time() - repo_mtime) / 60.0)
            return {
                "path": str(target),
                "status": "BLOCKED_RECENT_ACTIVITY",
                "reason": (
                    f"Enclosing worktree {enclosing_repo} modified {age_min:.1f} minutes ago "
                    f"(threshold: {wt_minutes}m); whole worktree (including build/artifact dirs) is protected."
                ),
            }

        if check_git_dirty(enclosing_repo):
            return {
                "path": str(target),
                "status": "BLOCKED_DIRTY_WORKTREE",
                "reason": f"Enclosing worktree {enclosing_repo} has uncommitted or untracked changes; whole worktree is protected.",
            }
    elif is_git_worktree_or_repo(target, item):
        if check_git_dirty(target):
            return {
                "path": str(target),
                "status": "BLOCKED_DIRTY_WORKTREE",
                "reason": "Worktree has uncommitted or untracked changes.",
            }

    if not skip_lsof:
        holders = check_open_handles(target)
        if holders:
            return {
                "path": str(target),
                "status": "BLOCKED_OPEN_HANDLES",
                "reason": f"Open file handles held by: {', '.join(holders)}",
            }

    return {
        "path": str(target),
        "status": "SAFE_TO_EXECUTE",
        "reason": "Passed whitelist, symlink, whole-worktree, recent-mtime, and handle checks.",
    }


def quarantine_item_refusal(item: dict[str, Any]) -> str:
    """Return why a candidate may never be moved into quarantine (category / action level), or ""."""
    cat = str(item.get("category") or "").strip().lower()
    kind = str(item.get("kind") or "").strip().lower()
    if "worktree" in cat or kind == "worktree":
        return WORKTREE_REFUSAL
    if cat in NON_MOVABLE_CATEGORIES:
        return NON_MOVABLE_CATEGORIES[cat]
    action = str(item.get("action_type") or "").strip().lower()
    if action in NON_MOVABLE_ACTION_TYPES:
        return f"动作类型为 {action}（优化或更新建议），没有可搬移的文件。"
    return ""


def quarantine_path_refusal(target: Path) -> str:
    """Return why a concrete path may never be moved into quarantine, or ""."""
    abs_target = Path(os.path.abspath(target))
    if ".git" in abs_target.parts or ".git" in target.resolve(strict=False).parts:
        return "路径在 Git 内部目录（.git）里，移走会损坏仓库。"
    name_lower = abs_target.name.lower()
    if name_lower in GOVERNANCE_DOC_NAMES or name_lower == "readme" or name_lower.startswith("readme."):
        return NON_MOVABLE_CATEGORIES["governance_doc"]
    if abs_target.suffix.lower() == ".plist" and abs_target.parent.name in ("LaunchAgents", "LaunchDaemons"):
        return NON_MOVABLE_CATEGORIES["launch_item"]
    try:
        if abs_target.is_dir() and (abs_target / ".git").exists():
            return WORKTREE_REFUSAL
    except OSError:
        pass
    return ""


def quarantine_refusal_for(item: dict[str, Any], raw_paths: list[str]) -> str:
    """Item-level refusal shared by preflight (quarantine_allowed) and quarantine: any refused path refuses the item."""
    reason = quarantine_item_refusal(item)
    if reason:
        return reason
    if not raw_paths:
        return "候选项没有可搬移的路径。"
    for raw in raw_paths:
        if is_remote_or_non_fs_spec(str(raw), item):
            return NON_FS_REFUSAL
        path_reason = quarantine_path_refusal(Path(os.path.expanduser(str(raw))))
        if path_reason:
            return f"{raw}：{path_reason}"
    return ""


def external_mount_root(path: Path) -> Path | None:
    """Return /Volumes/<name> for paths under /Volumes, else None."""
    parts = path.parts
    if len(parts) >= 3 and parts[0] == "/" and parts[1] == "Volumes":
        return Path("/", "Volumes", parts[2])
    return None


def read_volume_uuid(mount_root: Path) -> str:
    """Read VolumeUUID via `diskutil info -plist <mount point>`; "" when unavailable."""
    try:
        proc = subprocess.run(
            ["/usr/sbin/diskutil", "info", "-plist", str(mount_root)],
            capture_output=True,
            timeout=15,
            check=False,
        )
    except Exception:
        return ""
    if proc.returncode != 0 or not proc.stdout:
        return ""
    try:
        info = plistlib.loads(proc.stdout)
    except Exception:
        return ""
    if not isinstance(info, dict):
        return ""
    return str(info.get("VolumeUUID") or "").strip()


def _quarantine_root_entries(config: dict[str, Any]) -> list[dict[str, str]]:
    """Parse quarantine.root_dirs: strings or {"path": ..., "volume_uuid": ...}; malformed entries fail closed."""
    q_cfg = config.get("quarantine", {})
    if q_cfg is None:
        return []
    if not isinstance(q_cfg, dict):
        raise ConfigError("Invalid 'quarantine' object in configuration")
    raw = q_cfg.get("root_dirs", [])
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ConfigError("Invalid 'quarantine.root_dirs': must be a list")
    entries: list[dict[str, str]] = []
    for entry in raw:
        if isinstance(entry, str) and entry.strip():
            entries.append({"path": entry.strip(), "volume_uuid": ""})
        elif isinstance(entry, dict) and isinstance(entry.get("path"), str) and entry["path"].strip():
            uuid = entry.get("volume_uuid", "")
            if uuid is not None and not isinstance(uuid, str):
                raise ConfigError("Invalid 'quarantine.root_dirs[].volume_uuid': must be a string")
            entries.append({"path": entry["path"].strip(), "volume_uuid": (uuid or "").strip()})
        else:
            raise ConfigError(
                "Invalid 'quarantine.root_dirs' entry: use a path string or {\"path\": ..., \"volume_uuid\": ...}"
            )
    return entries


def check_quarantine_root(raw_path: str, volume_uuid: str = "") -> tuple[Path, str]:
    """Return (root_path, rejection_reason). An empty reason means the root is usable.

    Roots under /Volumes/<name> must be a real mount point (os.path.ismount), writable, and,
    when volume_uuid is configured, match `diskutil info -plist` VolumeUUID. Other paths are
    treated as local directories.
    """
    abs_path = Path(os.path.abspath(os.path.expanduser(raw_path)))
    mount_root = external_mount_root(abs_path)
    if mount_root is None:
        return expand_path(raw_path), ""
    if mount_root.is_symlink():
        return abs_path, f"{mount_root} 是符号链接，不是外置盘挂载点。"
    if not os.path.ismount(mount_root):
        return abs_path, f"{mount_root} 没有挂载（不存在，或只是 /Volumes 下的普通目录），不当作外置盘。"
    if not os.access(mount_root, os.W_OK):
        return abs_path, f"{mount_root} 已挂载但当前用户不可写。"
    if volume_uuid:
        actual = read_volume_uuid(mount_root)
        if not actual:
            return abs_path, f"读不到 {mount_root} 的 VolumeUUID（diskutil info 失败），无法核对是不是配置的那块盘。"
        if actual.upper() != volume_uuid.upper():
            return abs_path, f"{mount_root} 的 VolumeUUID 是 {actual}，和配置的 {volume_uuid} 不一致（可能是同名的另一块盘）。"
    return abs_path, ""


def resolve_quarantine_root(config: dict[str, Any], explicit_dir: str | None = None) -> tuple[Path, dict[str, Any]]:
    """Pick the first usable configured quarantine root; never scans /Volumes on its own.

    Returns (root, info) where info carries per-root checks and, when falling back to the local
    ~/.Cleanup_Quarantine, the reason for the fallback.
    """
    if explicit_dir:
        root, why = check_quarantine_root(explicit_dir)
        if why:
            raise ConfigError(f"--quarantine-dir {explicit_dir} is not usable: {why}")
        return root, {
            "source": "cli",
            "external": external_mount_root(root) is not None,
            "checks": [{"path": str(root), "accepted": True, "reason": "命令行显式指定"}],
            "fallback_reason": "",
        }

    entries = _quarantine_root_entries(config)
    fallback = expand_path(LOCAL_QUARANTINE_FALLBACK)
    if not entries:
        return fallback, {
            "source": "fallback",
            "external": False,
            "checks": [],
            "fallback_reason": "配置里没有 quarantine.root_dirs；不自动遍历 /Volumes（会误把备份卷、NAS 共享当外置盘），使用本机 ~/.Cleanup_Quarantine。",
        }

    checks: list[dict[str, Any]] = []
    for entry in entries:
        root, why = check_quarantine_root(entry["path"], entry.get("volume_uuid", ""))
        checks.append({"path": str(root), "accepted": not why, "reason": why or "可用"})
        if why:
            continue
        is_external = external_mount_root(root) is not None
        rejected = [c["reason"] for c in checks if not c["accepted"]]
        return root, {
            "source": "config",
            "external": is_external,
            "checks": checks,
            "fallback_reason": ("前面的外置隔离根不可用：" + "；".join(r.rstrip("。") for r in rejected)) if rejected else "",
        }

    return fallback, {
        "source": "fallback",
        "external": False,
        "checks": checks,
        "fallback_reason": "配置的隔离根都不可用，退回本机 ~/.Cleanup_Quarantine："
        + "；".join(c["reason"].rstrip("。") for c in checks),
    }


def generate_unique_batch_id(q_root: Path, now: datetime | None = None) -> str:
    """Generate a collision-free quarantine batch ID (with microseconds and sequence fallback)."""
    dt = now or datetime.now(timezone.utc)
    base = f"{dt.strftime('%Y%m%d-%H%M%SZ')}-{dt.microsecond:06d}"
    candidate = base
    seq = 1
    try:
        while (q_root / candidate).exists():
            seq += 1
            candidate = f"{base}-{seq}"
    except OSError:
        pass
    return candidate


def _build_restore_script(batch_id: str, staged_items: list[dict[str, Any]]) -> str:
    """Build a hardened restore.sh that removes empty regenerated dirs and refuses non-empty collisions."""
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        f"# Auto-generated restore script for quarantine batch {batch_id}",
        "",
        "restore_errors=0",
        "",
        "restore_entry() {",
        '  local qpath="$1"',
        '  local orig="$2"',
        '  local orig_parent="$3"',
        "",
        '  if [ ! -e "$qpath" ] && [ ! -L "$qpath" ]; then',
        "    return 0",
        "  fi",
        "",
        '  mkdir -p "$orig_parent"',
        "",
        '  if [ -L "$orig" ]; then',
        '    echo "ERROR: Destination symlink $orig already exists; refusing to overwrite from $qpath" >&2',
        "    restore_errors=$((restore_errors + 1))",
        "    return 0",
        '  elif [ -d "$orig" ]; then',
        '    if rmdir "$orig" 2>/dev/null; then',
        "      :",
        "    else",
        '      echo "ERROR: Destination directory $orig already exists and is non-empty; refusing nested mv from $qpath" >&2',
        "      restore_errors=$((restore_errors + 1))",
        "      return 0",
        "    fi",
        '  elif [ -e "$orig" ]; then',
        '    echo "ERROR: Destination file $orig already exists; skipping restore from $qpath" >&2',
        "    restore_errors=$((restore_errors + 1))",
        "    return 0",
        "  fi",
        "",
        '  mv "$qpath" "$orig"',
        '  echo "Restored: $orig"',
        "}",
        "",
    ]
    for entry in staged_items:
        qpath = shlex.quote(entry["quarantine_path"])
        orig = shlex.quote(entry["original_path"])
        orig_parent = shlex.quote(str(Path(entry["original_path"]).parent))
        lines.append(f"restore_entry {qpath} {orig} {orig_parent}")
    lines.extend(
        [
            "",
            'if [ "$restore_errors" -ne 0 ]; then',
            "  exit 1",
            "fi",
        ]
    )
    return "\n".join(lines) + "\n"


def _persist_quarantine_ledger(
    batch_dir: Path,
    manifest_path: Path,
    restore_script_path: Path,
    batch_id: str,
    manifest_payload: dict[str, Any],
    staged_items: list[dict[str, Any]],
) -> None:
    batch_dir.mkdir(parents=True, exist_ok=True)
    write_secure_json(manifest_path, manifest_payload, overwrite=True)
    restore_text = _build_restore_script(batch_id, staged_items)
    restore_script_path.write_text(restore_text, encoding="utf-8")
    os.chmod(restore_script_path, 0o700)


def cmd_quarantine(args: argparse.Namespace) -> int:
    """Move explicitly selected, preflight-safe paths into a Quarantine batch with manifest.json and restore.sh."""
    config = load_topology(args.config)
    wl_index = build_whitelist_index(config)

    selected_ids, per_id_modes = parse_ids_spec(args.ids, require_non_empty=True)
    selected_set = set(selected_ids)
    cli_uninstall_mode = getattr(args, "uninstall_mode", None)
    if cli_uninstall_mode is not None:
        normalize_uninstall_mode(cli_uninstall_mode, context="--uninstall-mode")

    if args.output and not args.overwrite and Path(args.output).exists():
        raise ConfigError(f"Output file already exists (pass --overwrite to replace): {args.output}")

    thresholds = config.get("thresholds", {})
    recent_minutes = (
        args.recent_minutes
        if args.recent_minutes is not None
        else float(thresholds.get("recent_activity_minutes", 60))
    )
    worktree_protect_minutes = (
        args.recent_minutes
        if args.recent_minutes is not None
        else float(thresholds.get("worktree_protect_minutes", recent_minutes))
    )
    cutoff_ts = time.time() - (recent_minutes * 60.0)
    wt_cutoff_ts = time.time() - (worktree_protect_minutes * 60.0)

    try:
        plan_data = json.loads(Path(args.targets).read_text(encoding="utf-8"))
    except Exception as exc:
        raise ConfigError(f"Failed to read targets/plan file {args.targets}: {exc}") from exc

    raw_items = plan_data.get("targets") or plan_data.get("candidates") or []

    q_root, q_info = resolve_quarantine_root(config, args.quarantine_dir)
    batch_id = generate_unique_batch_id(q_root)
    batch_dir = q_root / batch_id
    files_dir = batch_dir / "files"
    restore_script_path = batch_dir / "restore.sh"
    manifest_path = batch_dir / "manifest.json"

    staged_items: list[dict[str, Any]] = []
    blocked_items: list[dict[str, Any]] = []
    refused_items: list[dict[str, Any]] = []
    skipped_items: list[dict[str, Any]] = []
    failed_items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    def build_manifest_payload() -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": SCHEMA_VERSION,
            "batch_id": batch_id,
            "created_at": utc_now_iso(),
            "dry_run": bool(args.dry_run),
            "quarantine_root": str(q_root),
            "quarantine_root_external": bool(q_info.get("external")),
            "quarantine_root_checks": q_info.get("checks", []),
            "quarantine_root_fallback_reason": q_info.get("fallback_reason", ""),
            "batch_dir": str(batch_dir),
            "restore_script": str(restore_script_path),
            "requested_ids": selected_ids,
            "unknown_ids": [i for i in selected_ids if i not in seen_ids],
            "staged_count": len(staged_items),
            "blocked_count": len(blocked_items),
            "refused_count": len(refused_items),
            "skipped_count": len(skipped_items),
            "failed_count": len(failed_items),
            "staged_items": staged_items,
            "blocked_items": blocked_items,
            "refused_items": refused_items,
            "skipped_items": skipped_items,
            "failed_items": failed_items,
            "errors": failed_items,
        }
        if per_id_modes:
            payload["requested_uninstall_modes"] = dict(per_id_modes)
        return payload

    ledger_written = False
    try:
        for item in raw_items:
            item_id = str(item.get("id", "unknown"))
            if item_id not in selected_set:
                continue
            seen_ids.add(item_id)
            paths_to_check, active_mode = resolve_item_paths(
                item,
                cli_uninstall_mode=cli_uninstall_mode,
                item_mode_override=per_id_modes.get(item_id),
            )

            refusal = quarantine_refusal_for(item, paths_to_check)
            if refusal:
                refused_items.append(
                    {
                        "id": item_id,
                        "path": str(item.get("path") or (paths_to_check[0] if paths_to_check else "")),
                        "category": item.get("category", ""),
                        "status": "REFUSED_NOT_MOVABLE",
                        "reason": refusal,
                    }
                )
                continue

            for raw_p in paths_to_check:
                res = evaluate_single_target(
                    str(raw_p),
                    item,
                    wl_index,
                    cutoff_ts,
                    recent_minutes,
                    args.skip_lsof,
                    worktree_cutoff_ts=wt_cutoff_ts,
                    worktree_protect_minutes=worktree_protect_minutes,
                    config=config,
                )
                if res["status"] in ("MISSING_ALREADY_DONE", "SKIPPED_WHITELIST"):
                    skipped_items.append({"id": item_id, **res})
                    continue
                if res["status"] != "SAFE_TO_EXECUTE":
                    blocked_items.append({"id": item_id, **res})
                    continue

                src_path = Path(res["path"]).resolve(strict=False)
                if src_path == q_root or src_path in q_root.parents:
                    blocked_items.append(
                        {
                            "id": item_id,
                            "path": str(src_path),
                            "status": "REFUSED_NOT_MOVABLE",
                            "reason": "目标包含隔离区本身，不能把目录移进它自己的子目录。",
                        }
                    )
                    continue
                rel_dest = str(src_path).lstrip("/")
                dst_path = files_dir / rel_dest
                size_bytes, _, _ = bounded_dir_size(src_path, max_entries=10000, max_seconds=2.0)

                if not args.dry_run:
                    if dst_path.exists() or dst_path.is_symlink():
                        failed_items.append(
                            {
                                "id": item_id,
                                "path": str(src_path),
                                "quarantine_path": str(dst_path),
                                "status": "FAILED_MOVE",
                                "error_type": "FileExistsError",
                                "reason": f"Quarantine destination already exists: {dst_path}",
                            }
                        )
                        continue
                    try:
                        dst_path.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(src_path), str(dst_path))
                    except (OSError, shutil.Error) as exc:
                        # If src_path vanished and dst_path exists, record it in staged_items so restore.sh can still recover it
                        if not src_path.exists() and dst_path.exists():
                            recovered_entry: dict[str, Any] = {
                                "id": item_id,
                                "original_path": str(src_path),
                                "quarantine_path": str(dst_path),
                                "size_bytes": size_bytes,
                                "staged_at": utc_now_iso(),
                                "move_warning": str(exc),
                            }
                            if active_mode:
                                recovered_entry["uninstall_mode"] = active_mode
                            staged_items.append(recovered_entry)
                        failed_items.append(
                            {
                                "id": item_id,
                                "path": str(src_path),
                                "quarantine_path": str(dst_path),
                                "status": "FAILED_MOVE",
                                "error_type": type(exc).__name__,
                                "reason": str(exc),
                            }
                        )
                        continue

                staged_entry: dict[str, Any] = {
                    "id": item_id,
                    "original_path": str(src_path),
                    "quarantine_path": str(dst_path),
                    "size_bytes": size_bytes,
                    "staged_at": utc_now_iso(),
                }
                if active_mode:
                    staged_entry["uninstall_mode"] = active_mode
                staged_items.append(staged_entry)
    finally:
        if staged_items and not args.dry_run:
            _persist_quarantine_ledger(
                batch_dir=batch_dir,
                manifest_path=manifest_path,
                restore_script_path=restore_script_path,
                batch_id=batch_id,
                manifest_payload=build_manifest_payload(),
                staged_items=staged_items,
            )
            ledger_written = True

    manifest_payload = build_manifest_payload()
    if staged_items and not args.dry_run and not ledger_written:
        _persist_quarantine_ledger(
            batch_dir=batch_dir,
            manifest_path=manifest_path,
            restore_script_path=restore_script_path,
            batch_id=batch_id,
            manifest_payload=manifest_payload,
            staged_items=staged_items,
        )

    if args.output:
        write_secure_json(Path(args.output), manifest_payload, overwrite=args.overwrite)
    else:
        json.dump(manifest_payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    return 1 if failed_items else 0


def annotate_quarantine_eligibility(entry: dict[str, Any], move_refusal: str) -> None:
    """Tell the caller up front whether `quarantine` would ever move this item, using the same rule."""
    entry["quarantine_allowed"] = not move_refusal
    if move_refusal:
        entry["quarantine_refusal"] = move_refusal


def cmd_preflight(args: argparse.Namespace) -> int:
    config = load_topology(args.config)
    wl_index = build_whitelist_index(config)

    selected_list, per_id_modes = parse_ids_spec(args.ids, require_non_empty=(args.ids is not None))
    selected_ids = set(selected_list) if args.ids is not None else None
    cli_uninstall_mode = getattr(args, "uninstall_mode", None)
    if cli_uninstall_mode is not None:
        normalize_uninstall_mode(cli_uninstall_mode, context="--uninstall-mode")

    thresholds = config.get("thresholds", {})
    recent_minutes = (
        args.recent_minutes
        if args.recent_minutes is not None
        else float(thresholds.get("recent_activity_minutes", 60))
    )
    worktree_protect_minutes = (
        args.recent_minutes
        if args.recent_minutes is not None
        else float(thresholds.get("worktree_protect_minutes", recent_minutes))
    )
    cutoff_ts = time.time() - (recent_minutes * 60.0)
    wt_cutoff_ts = time.time() - (worktree_protect_minutes * 60.0)

    try:
        plan_data = json.loads(Path(args.targets).read_text(encoding="utf-8"))
    except Exception as exc:
        raise ConfigError(f"Failed to read targets/plan file {args.targets}: {exc}") from exc

    raw_items = plan_data.get("targets") or plan_data.get("candidates") or []

    results: list[dict[str, Any]] = []
    blocked_count = 0

    for item in raw_items:
        item_id = str(item.get("id", "unknown"))
        if selected_ids is not None and item_id not in selected_ids:
            continue

        resolved_paths, active_mode = resolve_item_paths(
            item,
            cli_uninstall_mode=cli_uninstall_mode,
            item_mode_override=per_id_modes.get(item_id),
        )
        move_refusal = quarantine_refusal_for(item, resolved_paths)
        if len(resolved_paths) > 1 or active_mode is not None or (isinstance(item.get("paths"), list) and item.get("paths")):
            sub_results = [
                evaluate_single_target(
                    str(p),
                    item,
                    wl_index,
                    cutoff_ts,
                    recent_minutes,
                    args.skip_lsof,
                    worktree_cutoff_ts=wt_cutoff_ts,
                    worktree_protect_minutes=worktree_protect_minutes,
                    config=config,
                )
                for p in resolved_paths
            ]
            safe_paths = [r["path"] for r in sub_results if r["status"] == "SAFE_TO_EXECUTE"]
            blocked_subs = [r for r in sub_results if r["status"].startswith("BLOCKED_")]
            skipped_wl_subs = [r for r in sub_results if r["status"] == "SKIPPED_WHITELIST"]
            if blocked_subs:
                blocked_count += 1
                first_blocked = blocked_subs[0]
                entry_res: dict[str, Any] = {
                    "id": item_id,
                    "path": str(item.get("path") or first_blocked["path"]),
                    "status": first_blocked["status"],
                    "reason": (
                        f"{len(blocked_subs)}/{len(sub_results)} sub-targets blocked "
                        f"(first: {first_blocked['path']} -> {first_blocked['reason']})"
                    ),
                    "safe_paths": safe_paths,
                    "blocked_paths": [r["path"] for r in blocked_subs],
                    "sub_results": sub_results,
                }
                if active_mode:
                    entry_res["uninstall_mode"] = active_mode
                results.append(entry_res)
            elif skipped_wl_subs and not safe_paths:
                first_wl = skipped_wl_subs[0]
                entry_res = {
                    "id": item_id,
                    "path": str(item.get("path") or first_wl["path"]),
                    "status": "SKIPPED_WHITELIST",
                    "reason": first_wl["reason"],
                    "safe_paths": [],
                    "blocked_paths": [],
                    "sub_results": sub_results,
                }
                if active_mode:
                    entry_res["uninstall_mode"] = active_mode
                results.append(entry_res)
            elif all(r["status"] == "MISSING_ALREADY_DONE" for r in sub_results):
                entry_res = {
                    "id": item_id,
                    "path": str(item.get("path") or (sub_results[0]["path"] if sub_results else "")),
                    "status": "MISSING_ALREADY_DONE",
                    "reason": "All sub-target paths no longer exist.",
                    "safe_paths": [],
                    "blocked_paths": [],
                    "sub_results": sub_results,
                }
                if active_mode:
                    entry_res["uninstall_mode"] = active_mode
                results.append(entry_res)
            else:
                entry_res = {
                    "id": item_id,
                    "path": str(item.get("path") or (sub_results[0]["path"] if sub_results else "")),
                    "status": "SAFE_TO_EXECUTE",
                    "reason": f"All {len(safe_paths)} present sub-targets passed preflight checks.",
                    "safe_paths": safe_paths,
                    "blocked_paths": [],
                    "sub_results": sub_results,
                }
                if active_mode:
                    entry_res["uninstall_mode"] = active_mode
                results.append(entry_res)
            annotate_quarantine_eligibility(results[-1], move_refusal)
            continue

        raw_path = resolved_paths[0] if resolved_paths else str(item.get("path") or item.get("path_or_spec") or "")
        single = evaluate_single_target(
            raw_path,
            item,
            wl_index,
            cutoff_ts,
            recent_minutes,
            args.skip_lsof,
            worktree_cutoff_ts=wt_cutoff_ts,
            worktree_protect_minutes=worktree_protect_minutes,
            config=config,
        )
        if single["status"].startswith("BLOCKED_"):
            blocked_count += 1
        entry_single: dict[str, Any] = {
            "id": item_id,
            "path": single["path"],
            "status": single["status"],
            "reason": single["reason"],
        }
        if active_mode:
            entry_single["uninstall_mode"] = active_mode
        results.append(entry_single)
        annotate_quarantine_eligibility(results[-1], move_refusal)

    payload = {
        "schema": SCHEMA_VERSION,
        "checked_at": utc_now_iso(),
        "overall_status": "READY" if blocked_count == 0 else "PARTIAL_BLOCKED",
        "safe_count": sum(1 for r in results if r["status"] == "SAFE_TO_EXECUTE"),
        "blocked_count": blocked_count,
        "results": results,
    }
    if per_id_modes:
        payload["requested_uninstall_modes"] = dict(per_id_modes)

    if args.output:
        write_secure_json(Path(args.output), payload, overwrite=args.overwrite)
    else:
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Full-stack maintenance scanner, snapshot differ, pre-flight concurrency checker, and quarantine stager."
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    p_scan = sub.add_parser(
        "scan-local",
        help="Run read-only local scan for orphans, app uninstaller, caches, launch items, updates, peripherals, and .git bloat.",
    )
    p_scan.add_argument("--config", required=True, help="Path to topology.json or topology.local.json")
    p_scan.add_argument("--skip-probes", action="store_true", help="Skip live CLI probes (brew/npm/rustup/system_profiler)")
    p_scan.add_argument("--output", help="Write JSON snapshot to path (0600 permissions)")
    p_scan.add_argument("--overwrite", action="store_true", help="Overwrite output file if it exists")

    p_diff = sub.add_parser("diff", help="Compare two snapshots and report storage deltas and top growth drivers.")
    p_diff.add_argument("--before", required=True, help="Path to previous baseline snapshot JSON")
    p_diff.add_argument("--after", required=True, help="Path to current snapshot JSON")
    p_diff.add_argument("--output", help="Write diff JSON to path")
    p_diff.add_argument("--overwrite", action="store_true", help="Overwrite output file if it exists")

    p_pre = sub.add_parser("preflight", help="Verify concurrency, recent activity, handles, and whitelist before cleanup.")
    p_pre.add_argument("--config", required=True, help="Path to topology.json or topology.local.json")
    p_pre.add_argument("--targets", required=True, help="Path to snapshot or targets JSON")
    p_pre.add_argument("--ids", help="Comma-separated candidate IDs to check (default: all)")
    p_pre.add_argument(
        "--uninstall-mode",
        choices=["purge-all", "keep-data", "purge_all", "keep_data"],
        default=None,
        help="Uninstall mode for app_uninstall targets: keep-data (default, user data stays) or purge-all",
    )
    p_pre.add_argument("--recent-minutes", type=float, default=None, help="Override recent activity window in minutes")
    p_pre.add_argument("--skip-lsof", action="store_true", help="Skip lsof invocation (for fast fixture tests)")
    p_pre.add_argument("--output", help="Write preflight JSON to path")
    p_pre.add_argument("--overwrite", action="store_true", help="Overwrite output file if it exists")

    p_quar = sub.add_parser(
        "quarantine",
        help=(
            "Move explicitly selected, preflight-safe build artifacts, app leftovers or app bundles into a "
            "Quarantine batch with manifest.json + restore.sh (Git data, governance docs, launch items, "
            "update/firmware advisories and worktrees are refused)."
        ),
    )
    p_quar.add_argument("--config", required=True, help="Path to topology.json or topology.local.json")
    p_quar.add_argument("--targets", required=True, help="Path to snapshot or targets JSON")
    p_quar.add_argument(
        "--ids",
        required=True,
        help="Comma-separated candidate IDs to quarantine (required; use IDs that preflight reported as SAFE_TO_EXECUTE)",
    )
    p_quar.add_argument(
        "--uninstall-mode",
        choices=["purge-all", "keep-data", "purge_all", "keep_data"],
        default=None,
        help="Uninstall mode for app_uninstall targets: keep-data (default, user data stays) or purge-all",
    )
    p_quar.add_argument("--quarantine-dir", help="Explicit quarantine root directory")
    p_quar.add_argument("--recent-minutes", type=float, default=None, help="Override recent activity window in minutes")
    p_quar.add_argument("--skip-lsof", action="store_true", help="Skip lsof invocation")
    p_quar.add_argument("--dry-run", action="store_true", help="Preview quarantine actions without moving files")
    p_quar.add_argument("--output", help="Write quarantine manifest JSON to path")
    p_quar.add_argument("--overwrite", action="store_true", help="Overwrite output file if it exists")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.subcommand == "scan-local":
            return cmd_scan_local(args)
        if args.subcommand == "diff":
            return cmd_diff(args)
        if args.subcommand == "preflight":
            return cmd_preflight(args)
        if args.subcommand == "quarantine":
            return cmd_quarantine(args)
    except ConfigError as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
