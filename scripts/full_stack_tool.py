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

    if not isinstance(data, dict) or data.get("version") != SCHEMA_VERSION:
        raise ConfigError(
            f"Unsupported or missing configuration version in {path} (expected {SCHEMA_VERSION})"
        )

    whitelist = data.get("whitelist")
    if not isinstance(whitelist, dict):
        raise ConfigError(f"Missing or invalid 'whitelist' object in {path}")

    for list_key in ("paths", "repos", "app_names", "guest_ids"):
        val = whitelist.get(list_key, [])
        if not isinstance(val, list) or not all(isinstance(x, str) and x.strip() for x in val):
            raise ConfigError(f"Invalid 'whitelist.{list_key}' in {path}: must be a list of non-empty strings")

    notes = whitelist.get("notes", {})
    if not isinstance(notes, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in notes.items()
    ):
        raise ConfigError(f"Invalid 'whitelist.notes' in {path}: must be a string-to-string map")

    return data


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
    repos = {r.strip().lower() for r in wl.get("repos", []) if r.strip()}
    guest_ids = {str(g).strip() for g in wl.get("guest_ids", []) if str(g).strip()}

    return {
        "paths": resolved_paths,
        "inode_keys": inode_keys,
        "notes_by_path": notes_by_path,
        "raw_notes": raw_notes,
        "app_tokens": app_tokens,
        "repos": repos,
        "guest_ids": guest_ids,
    }


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
            for word in re.split(r"[^a-zA-Z0-9]+", app_dir.stem):
                if len(word) >= 3:
                    vendor_tokens.add(word.lower())

            plist_path = app_dir / "Contents" / "Info.plist"
            if not plist_path.is_file():
                plist_path = app_dir / "Info.plist"
            info = _read_plist_safe(plist_path) or {}

            bid_orig = str(info.get("CFBundleIdentifier") or "").strip()
            bid = bid_orig.lower()
            app_norm_tokens: set[str] = {stem_norm} if stem_norm else set()
            if bid:
                bundle_ids.add(bid)
                parts = [p for p in bid.split(".") if p not in ("com", "org", "net", "io", "app", "mac", "macos")]
                for part in parts:
                    if len(part) >= 3:
                        vendor_tokens.add(normalize_token(part))

            display_name = str(info.get("CFBundleDisplayName") or info.get("CFBundleName") or app_dir.stem).strip()
            for key in ("CFBundleName", "CFBundleDisplayName", "CFBundleExecutable"):
                val = str(info.get(key) or "").strip()
                if val:
                    nt = normalize_token(val)
                    if nt:
                        norm_names.add(nt)
                        app_norm_tokens.add(nt)

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
                    "executable": str(info.get("CFBundleExecutable") or "").strip(),
                    "sparkle_feed": sparkle_feed,
                    "install_source": install_source,
                    "norm_tokens": sorted(app_norm_tokens),
                }
            )

    return {
        "bundle_ids": bundle_ids,
        "norm_names": norm_names,
        "vendor_tokens": vendor_tokens,
        "apps": apps_detail,
    }


def is_entry_matched_to_installed_app(entry_name: str, installed: dict[str, Any]) -> bool:
    lower = entry_name.strip().lower()
    for suffix in (".savedstate", ".binarycookies", ".plist"):
        if lower.endswith(suffix):
            lower = lower[: -len(suffix)]
            break
    if any(lower.startswith(prefix.lower()) for prefix in SYSTEM_LIBRARY_IGNORE_PREFIXES):
        return True

    norm = normalize_token(lower)
    if not norm:
        return True

    if lower in installed["bundle_ids"] or norm in installed["norm_names"]:
        return True

    for bid in installed["bundle_ids"]:
        if lower.startswith(bid + ".") or bid.startswith(lower + "."):
            return True
        if normalize_token(bid) == norm:
            return True

    # Check if normalized name contains or matches any installed app norm_name (>= 4 chars)
    for app_norm in installed["norm_names"]:
        if len(app_norm) >= 4 and (app_norm in norm or norm in app_norm):
            return True

    # Check bundle ID vendor segments (e.g. com.vendor.AppName)
    parts = [normalize_token(p) for p in re.split(r"[.\-_ ]+", lower) if p]
    for p in parts:
        if p in installed["vendor_tokens"]:
            return True

    return False


def match_library_entry_to_app(entry_name: str, app: dict[str, Any]) -> bool:
    """Return True if a Library directory/file name belongs to the given installed app."""
    lower = entry_name.strip().lower()
    for suffix in (".savedstate", ".binarycookies", ".plist"):
        if lower.endswith(suffix):
            lower = lower[: -len(suffix)]
            break
    bid = app.get("bundle_id_lower", "")
    if bid and (lower == bid or lower.startswith(bid + ".") or lower.endswith("." + bid)):
        return True
    norm = normalize_token(lower)
    if not norm:
        return False
    if bid and normalize_token(bid) == norm:
        return True
    for tok in app.get("norm_tokens", []):
        if len(tok) >= 4 and (tok == norm or (len(tok) >= 5 and tok in norm)):
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
        p = expand_path(raw)
        if not p.is_dir() or p.is_symlink():
            continue
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


def scan_workspaces_and_backups(
    config: dict[str, Any],
    wl_index: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    workspace_roots = config.get("workspace_roots", [])
    thresholds = config.get("thresholds", {})
    min_git_pack_bytes = int(float(thresholds.get("min_git_pack_mb", 100)) * 1024 * 1024)
    min_build_bytes = int(float(thresholds.get("min_build_artifact_mb", 100)) * 1024 * 1024)
    backup_pipelines = config.get("backup_pipelines", [])

    candidates: list[dict[str, Any]] = []
    dir_sizes: dict[str, int] = {}

    repos = iter_git_repos(workspace_roots)
    for repo in repos:
        repo_slug = re.sub(r"[^a-z0-9]+", "-", repo.name.lower()).strip("-")
        whitelisted_repo, wl_repo_note = check_path_whitelisted(repo, wl_index)

        # 1. Check .git/objects/pack size
        git_dir = repo / ".git"
        if git_dir.is_dir():
            pack_dir = git_dir / "objects" / "pack"
            if pack_dir.is_dir():
                pack_bytes, truncated, latest_mtime = bounded_dir_size(pack_dir)
                dir_sizes[str(pack_dir)] = pack_bytes
                if pack_bytes >= min_git_pack_bytes:
                    candidates.append(
                        {
                            "id": f"git-pack-{repo_slug}",
                            "domain": "projects",
                            "category": "git_pack_bloat",
                            "tier": "B",
                            "title": f"仓库 Git Pack 体积较大：{repo.name} (.git/objects/pack)",
                            "path": str(pack_dir),
                            "size_bytes": pack_bytes,
                            "size_truncated": truncated,
                            "latest_mtime": latest_mtime,
                            "gauge_id": "mac-data",
                            "whitelisted": whitelisted_repo,
                            "whitelist_note": wl_repo_note,
                            "evidence": (
                                f"{repo.name} 的 .git/objects/pack 达 {pack_bytes / (1024**2):.1f} MiB；"
                                "可在无并发操作时执行普通 `git gc`（不加 --prune=now，保留别的会话可能还在用的松散对象），"
                                "或检查是否误提交过大二进制文件。"
                            ),
                        }
                    )

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
                        slug = re.sub(r"[^a-z0-9]+", "-", f"{curr.name}-{child.name}".lower()).strip("-")[:36]
                        candidates.append(
                            {
                                "id": f"build-{slug}",
                                "domain": "mac",
                                "category": "build_artifact",
                                "tier": "A",
                                "title": f"可重建构建产物：{curr.name}/{child.name}",
                                "path": str(child),
                                "size_bytes": size_bytes,
                                "size_truncated": truncated,
                                "latest_mtime": latest_mtime,
                                "gauge_id": "mac-data",
                                "whitelisted": whitelisted,
                                "whitelist_note": wl_note,
                                "evidence": f"匹配构建/产物目录模式 ({child.name})，可通过工程构建命令重新生成。",
                            }
                        )
                        # Check backup exclusion drift against configured pipelines
                        for pipe in backup_pipelines:
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
                                        "domain": "mac",
                                        "category": "backup_exclusion_drift",
                                        "tier": "B",
                                        "title": f"备份排除规则疑似漏排：{child.name} → {pipe.get('label', pipe_id)}",
                                        "path": str(child),
                                        "size_bytes": size_bytes,
                                        "size_truncated": truncated,
                                        "latest_mtime": latest_mtime,
                                        "gauge_id": "mac-data",
                                        "whitelisted": False,
                                        "whitelist_note": "",
                                        "evidence": (
                                            f"目录 {child} ({size_bytes / (1024**2):.1f} MiB) 未被备份管线 "
                                            f"{pipe.get('label', pipe_id)} 的排除规则完整覆盖，可能穿透备份撑爆远端空间。"
                                        ),
                                    }
                                )
                else:
                    stack.append((child, depth + 1))

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
    ws_candidates, ws_sizes = scan_workspaces_and_backups(config, wl_index)

    all_candidates = (
        orphan_candidates
        + uninstall_candidates
        + cache_candidates
        + launch_candidates
        + app_update_candidates
        + env_update_candidates
        + hw_candidates
        + ws_candidates
    )
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
            ["git", "-C", str(target), "status", "--porcelain"],
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


def check_codex_session_activity(
    repo_root: Path,
    cutoff_ts: float,
    sessions_dir: Path | None = None,
) -> tuple[bool, str]:
    """Check if recent Codex session rollouts (~/.codex/sessions/**/*.jsonl) reference repo_root after cutoff_ts.

    Only Codex sessions are read. Claude Code, Antigravity and OpenCode sessions are not
    inspected; those are covered indirectly by recent-mtime, dirty-status and lsof checks.
    """
    sess_root = sessions_dir or (Path.home() / ".codex" / "sessions")
    if not sess_root.is_dir():
        return False, ""
    repo_bytes = str(repo_root).encode("utf-8", errors="ignore")
    try:
        for jsonl in sess_root.rglob("*.jsonl"):
            try:
                st = jsonl.stat()
            except OSError:
                continue
            if st.st_mtime < cutoff_ts:
                continue
            # Read first 64 KiB and last 64 KiB where cwd/worktree metadata lives
            with jsonl.open("rb") as fp:
                head = fp.read(65536)
                if repo_bytes in head:
                    age_min = max(0.0, (time.time() - st.st_mtime) / 60.0)
                    return (
                        True,
                        f"Active Codex session ({jsonl.name}) referenced {repo_root} {age_min:.1f}m ago; whole worktree is protected.",
                    )
                if st.st_size > 65536:
                    fp.seek(max(0, st.st_size - 65536))
                    tail = fp.read(65536)
                    if repo_bytes in tail:
                        age_min = max(0.0, (time.time() - st.st_mtime) / 60.0)
                        return (
                            True,
                            f"Active Codex session ({jsonl.name}) referenced {repo_root} {age_min:.1f}m ago; whole worktree is protected.",
                        )
    except Exception:
        return False, ""
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


def resolve_item_paths(item: dict[str, Any], cli_uninstall_mode: str | None = None) -> tuple[list[str], str | None]:
    """Resolve target paths for a candidate item, honoring keep_data / purge_all uninstall modes when present.

    Without an explicit mode the default is keep_data (user data stays); purge_all must be chosen explicitly.
    """
    modes = item.get("uninstall_modes")
    if isinstance(modes, dict) and modes:
        raw_mode = cli_uninstall_mode or item.get("selected_uninstall_mode") or "keep_data"
        norm_mode = str(raw_mode).strip().lower().replace("-", "_")
        if norm_mode not in modes:
            norm_mode = "keep_data" if "keep_data" in modes else next(iter(modes.keys()))
        mode_obj = modes.get(norm_mode, {})
        if isinstance(mode_obj, dict) and isinstance(mode_obj.get("paths"), list) and mode_obj["paths"]:
            return [str(p) for p in mode_obj["paths"]], norm_mode
    if isinstance(item.get("paths"), list) and item.get("paths"):
        return [str(p) for p in item["paths"]], None
    p = str(item.get("path") or "").strip()
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
) -> dict[str, Any]:
    if is_remote_or_non_fs_spec(raw_path, item):
        return {
            "path": raw_path,
            "status": "SAFE_TO_EXECUTE",
            "reason": "Non-filesystem or remote action; verify via domain tool.",
        }

    raw_expanded = os.path.expanduser(raw_path)
    target = Path(raw_expanded)
    whitelisted, wl_note = check_path_whitelisted(target, wl_index)
    if whitelisted or item.get("whitelisted"):
        return {
            "path": str(target),
            "status": "BLOCKED_WHITELISTED",
            "reason": wl_note or item.get("whitelist_note") or "Target is in whitelist",
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

        codex_active, codex_reason = check_codex_session_activity(enclosing_repo, wt_cutoff)
        if codex_active:
            return {
                "path": str(target),
                "status": "BLOCKED_ACTIVE_WORKTREE",
                "reason": codex_reason,
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
    if abs_target.name.lower() in GOVERNANCE_DOC_NAMES:
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


def cmd_quarantine(args: argparse.Namespace) -> int:
    """Move explicitly selected, preflight-safe paths into a Quarantine batch with manifest.json and restore.sh."""
    config = load_topology(args.config)
    wl_index = build_whitelist_index(config)

    selected_ids = [x.strip() for x in str(args.ids or "").split(",") if x.strip()]
    if not selected_ids:
        raise ConfigError("--ids must list at least one candidate ID; quarantine never defaults to all candidates")
    selected_set = set(selected_ids)

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
    cli_uninstall_mode = getattr(args, "uninstall_mode", None)

    q_root, q_info = resolve_quarantine_root(config, args.quarantine_dir)
    batch_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    batch_dir = q_root / batch_id
    files_dir = batch_dir / "files"

    staged_items: list[dict[str, Any]] = []
    blocked_items: list[dict[str, Any]] = []
    refused_items: list[dict[str, Any]] = []
    skipped_items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for item in raw_items:
        item_id = str(item.get("id", "unknown"))
        if item_id not in selected_set:
            continue
        seen_ids.add(item_id)
        paths_to_check, active_mode = resolve_item_paths(item, cli_uninstall_mode=cli_uninstall_mode)

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
            )
            if res["status"] == "MISSING_ALREADY_DONE":
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
                dst_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src_path), str(dst_path))

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

    restore_script_path = batch_dir / "restore.sh"
    manifest_path = batch_dir / "manifest.json"
    manifest_payload = {
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
        "staged_items": staged_items,
        "blocked_items": blocked_items,
        "refused_items": refused_items,
        "skipped_items": skipped_items,
    }

    if staged_items and not args.dry_run:
        batch_dir.mkdir(parents=True, exist_ok=True)
        write_secure_json(manifest_path, manifest_payload, overwrite=True)
        lines = [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            f"# Auto-generated restore script for quarantine batch {batch_id}",
        ]
        for entry in staged_items:
            orig = shlex.quote(entry["original_path"])
            qpath = shlex.quote(entry["quarantine_path"])
            orig_parent = shlex.quote(str(Path(entry["original_path"]).parent))
            lines.append(f"mkdir -p {orig_parent}")
            lines.append(f"if [ -e {qpath} ]; then mv {qpath} {orig}; echo 'Restored:' {orig}; fi")
        restore_script_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.chmod(restore_script_path, 0o700)

    if args.output:
        write_secure_json(Path(args.output), manifest_payload, overwrite=args.overwrite)
    else:
        json.dump(manifest_payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    return 0


def annotate_quarantine_eligibility(entry: dict[str, Any], move_refusal: str) -> None:
    """Tell the caller up front whether `quarantine` would ever move this item, using the same rule."""
    entry["quarantine_allowed"] = not move_refusal
    if move_refusal:
        entry["quarantine_refusal"] = move_refusal


def cmd_preflight(args: argparse.Namespace) -> int:
    config = load_topology(args.config)
    wl_index = build_whitelist_index(config)

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
    selected_ids = (
        {x.strip() for x in args.ids.split(",") if x.strip()} if args.ids else None
    )
    cli_uninstall_mode = getattr(args, "uninstall_mode", None)

    results: list[dict[str, Any]] = []
    blocked_count = 0

    for item in raw_items:
        item_id = item.get("id", "unknown")
        if selected_ids is not None and item_id not in selected_ids:
            continue

        resolved_paths, active_mode = resolve_item_paths(item, cli_uninstall_mode=cli_uninstall_mode)
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
                )
                for p in resolved_paths
            ]
            safe_paths = [r["path"] for r in sub_results if r["status"] == "SAFE_TO_EXECUTE"]
            blocked_subs = [r for r in sub_results if r["status"].startswith("BLOCKED_")]
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

        raw_path = resolved_paths[0] if resolved_paths else str(item.get("path") or "")
        single = evaluate_single_target(
            raw_path,
            item,
            wl_index,
            cutoff_ts,
            recent_minutes,
            args.skip_lsof,
            worktree_cutoff_ts=wt_cutoff_ts,
            worktree_protect_minutes=worktree_protect_minutes,
        )
        if single["status"].startswith("BLOCKED_"):
            blocked_count += 1
        results.append(
            {
                "id": item_id,
                "path": single["path"],
                "status": single["status"],
                "reason": single["reason"],
            }
        )
        annotate_quarantine_eligibility(results[-1], move_refusal)

    payload = {
        "schema": SCHEMA_VERSION,
        "checked_at": utc_now_iso(),
        "overall_status": "READY" if blocked_count == 0 else "PARTIAL_BLOCKED",
        "safe_count": sum(1 for r in results if r["status"] == "SAFE_TO_EXECUTE"),
        "blocked_count": blocked_count,
        "results": results,
    }

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
