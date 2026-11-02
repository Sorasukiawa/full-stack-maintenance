#!/usr/bin/env python3
"""Read-only local scanner, baseline snapshot differ, and pre-flight concurrency guard.

Uses only the Python 3.10+ standard library. Never deletes files, uninstalls apps,
or modifies system services.
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


def collect_installed_apps(app_roots: list[str]) -> dict[str, set[str]]:
    """Collect bundle IDs, normalized names, and vendor tokens from installed .app bundles."""
    bundle_ids: set[str] = set()
    norm_names: set[str] = set()
    vendor_tokens: set[str] = set(COMMON_SHARED_TOOL_TOKENS)

    for raw_root in app_roots:
        root = expand_path(raw_root)
        if not root.is_dir():
            continue
        # Scan depth 1 and 2 for *.app
        candidates: list[Path] = []
        try:
            for child in root.iterdir():
                if child.suffix.lower() == ".app":
                    candidates.append(child)
                elif child.is_dir() and not child.is_symlink():
                    try:
                        for sub in child.iterdir():
                            if sub.suffix.lower() == ".app":
                                candidates.append(sub)
                    except OSError:
                        pass
        except OSError:
            continue

        for app_dir in candidates:
            stem_norm = normalize_token(app_dir.stem)
            if stem_norm:
                norm_names.add(stem_norm)
            for word in re.split(r"[^a-zA-Z0-9]+", app_dir.stem):
                if len(word) >= 3:
                    vendor_tokens.add(word.lower())

            plist_path = app_dir / "Contents" / "Info.plist"
            if plist_path.is_file():
                try:
                    with plist_path.open("rb") as fp:
                        info = plistlib.load(fp)
                    bid = str(info.get("CFBundleIdentifier") or "").strip().lower()
                    if bid:
                        bundle_ids.add(bid)
                        parts = [p for p in bid.split(".") if p not in ("com", "org", "net", "io", "app", "mac", "macos")]
                        for part in parts:
                            if len(part) >= 3:
                                vendor_tokens.add(normalize_token(part))
                    for key in ("CFBundleName", "CFBundleDisplayName", "CFBundleExecutable"):
                        val = str(info.get(key) or "").strip()
                        if val:
                            norm_names.add(normalize_token(val))
                except Exception:
                    pass

    return {
        "bundle_ids": bundle_ids,
        "norm_names": norm_names,
        "vendor_tokens": vendor_tokens,
    }


def is_entry_matched_to_installed_app(entry_name: str, installed: dict[str, set[str]]) -> bool:
    lower = entry_name.strip().lower()
    if any(lower.startswith(prefix.lower()) for prefix in SYSTEM_LIBRARY_IGNORE_PREFIXES):
        return True

    norm = normalize_token(entry_name)
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


def scan_app_orphans(
    config: dict[str, Any],
    wl_index: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    app_roots = config.get("app_roots", ["/Applications", "~/Applications"])
    library_roots = config.get(
        "library_roots",
        ["~/Library/Application Support", "~/Library/Caches", "~/Library/Containers"],
    )
    min_mb = float(config.get("thresholds", {}).get("min_orphan_mb", 50))
    min_bytes = int(min_mb * 1024 * 1024)

    installed = collect_installed_apps(app_roots)
    candidates: list[dict[str, Any]] = []
    dir_sizes: dict[str, int] = {}

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
            if size_bytes < min_bytes:
                continue

            whitelisted, wl_note = check_path_whitelisted(entry, wl_index)
            if not whitelisted and normalize_token(entry.name) in wl_index["app_tokens"]:
                whitelisted = True
                wl_note = f"Protected by whitelist.app_names ({entry.name})"

            slug = re.sub(r"[^a-z0-9]+", "-", entry.name.lower()).strip("-")[:32]
            candidates.append(
                {
                    "id": f"orphan-{slug}",
                    "domain": "mac",
                    "category": "app_orphan",
                    "tier": "C",
                    "title": f"疑似已卸载应用残留：{entry.name}",
                    "path": str(entry),
                    "size_bytes": size_bytes,
                    "size_truncated": truncated,
                    "latest_mtime": latest_mtime,
                    "gauge_id": "mac-data",
                    "whitelisted": whitelisted,
                    "whitelist_note": wl_note,
                    "evidence": (
                        f"位于 {lib_dir.name}，未在 {app_roots} 中匹配到同 Bundle ID 或应用名称；"
                        "删除前需确认该软件确已停用且不含需保留的个人配置或工程数据。"
                    ),
                }
            )

    return candidates, dir_sizes


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
                                "可在无并发操作时执行 git gc --prune=now 或检查是否误提交过大二进制文件。"
                            ),
                        }
                    )

        # 2. Check AGENTS.md / CLAUDE.md pre-merge check governance
        agents_md = repo / "AGENTS.md"
        if agents_md.is_file():
            try:
                text = agents_md.read_text(encoding="utf-8", errors="replace")
                if "合入前检查：" not in text and "合入前检查:" not in text:
                    candidates.append(
                        {
                            "id": f"doc-premerge-{repo_slug}",
                            "domain": "projects",
                            "category": "governance_doc",
                            "tier": "B",
                            "title": f"补充合入前检查声明：{repo.name}/AGENTS.md",
                            "path": str(agents_md),
                            "size_bytes": 0,
                            "size_truncated": False,
                            "latest_mtime": int(agents_md.stat().st_mtime),
                            "gauge_id": "",
                            "whitelisted": whitelisted_repo,
                            "whitelist_note": wl_repo_note,
                            "evidence": "AGENTS.md 缺少「合入前检查：`命令`」或「合入前检查：无」约定行。",
                        }
                    )
            except OSError:
                pass

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

    gauges = collect_storage_gauges(config)
    orphan_candidates, orphan_sizes = scan_app_orphans(config, wl_index)
    ws_candidates, ws_sizes = scan_workspaces_and_backups(config, wl_index)

    all_candidates = orphan_candidates + ws_candidates
    all_sizes = {**orphan_sizes, **ws_sizes}

    snapshot = {
        "schema": SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "config_path": str(Path(args.config).resolve()),
        "storage_gauges": gauges,
        "candidates": all_candidates,
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


def check_codex_sqlite_activity(
    repo_root: Path,
    cutoff_ts: float,
    sessions_dir: Path | None = None,
) -> tuple[bool, str]:
    """Check if recent Codex session rollouts in ~/.codex/sessions reference repo_root after cutoff_ts."""
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
    return False


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

        codex_active, codex_reason = check_codex_sqlite_activity(enclosing_repo, wt_cutoff)
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


def resolve_quarantine_root(config: dict[str, Any], explicit_dir: str | None = None) -> Path:
    """Select external drive quarantine root first (to free Mac space immediately), falling back to ~/.Cleanup_Quarantine."""
    if explicit_dir:
        return expand_path(explicit_dir)
    q_cfg = config.get("quarantine", {})
    candidates = q_cfg.get(
        "root_dirs",
        [
            "/Volumes/Sora2T/.Cleanup_Quarantine",
            "/Volumes/Sora4T/.Cleanup_Quarantine",
            "~/.Cleanup_Quarantine",
        ],
    )
    for raw in candidates:
        p = expand_path(raw)
        if str(p).startswith("/Volumes/"):
            parts = p.parts
            if len(parts) >= 3:
                mount_root = Path("/") / parts[1] / parts[2]
                if mount_root.is_dir() and os.access(mount_root, os.W_OK):
                    return p
        else:
            return p
    return expand_path("~/.Cleanup_Quarantine")


def cmd_quarantine(args: argparse.Namespace) -> int:
    """Move safe project/worktree/artifact targets into a Quarantine batch with manifest.json and restore.sh."""
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

    q_root = resolve_quarantine_root(config, args.quarantine_dir)
    batch_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    batch_dir = q_root / batch_id
    files_dir = batch_dir / "files"

    staged_items: list[dict[str, Any]] = []
    blocked_items: list[dict[str, Any]] = []

    for item in raw_items:
        item_id = item.get("id", "unknown")
        if selected_ids is not None and item_id not in selected_ids:
            continue
        paths_to_check = item.get("paths") if isinstance(item.get("paths"), list) and item.get("paths") else [item.get("path", "")]
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
            if res["status"] != "SAFE_TO_EXECUTE" or is_remote_or_non_fs_spec(str(raw_p), item):
                blocked_items.append({"id": item_id, **res})
                continue

            src_path = Path(res["path"]).resolve(strict=False)
            rel_dest = str(src_path).lstrip("/")
            dst_path = files_dir / rel_dest
            size_bytes, _, _ = bounded_dir_size(src_path, max_entries=10000, max_seconds=2.0)

            if not args.dry_run:
                dst_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src_path), str(dst_path))

            staged_items.append(
                {
                    "id": item_id,
                    "original_path": str(src_path),
                    "quarantine_path": str(dst_path),
                    "size_bytes": size_bytes,
                    "staged_at": utc_now_iso(),
                }
            )

    restore_script_path = batch_dir / "restore.sh"
    manifest_path = batch_dir / "manifest.json"
    manifest_payload = {
        "schema": SCHEMA_VERSION,
        "batch_id": batch_id,
        "created_at": utc_now_iso(),
        "dry_run": bool(args.dry_run),
        "quarantine_root": str(q_root),
        "batch_dir": str(batch_dir),
        "restore_script": str(restore_script_path),
        "staged_count": len(staged_items),
        "blocked_count": len(blocked_items),
        "staged_items": staged_items,
        "blocked_items": blocked_items,
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

    results: list[dict[str, Any]] = []
    blocked_count = 0

    for item in raw_items:
        item_id = item.get("id", "unknown")
        if selected_ids is not None and item_id not in selected_ids:
            continue

        raw_paths = item.get("paths")
        if isinstance(raw_paths, list) and len(raw_paths) > 0:
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
                for p in raw_paths
            ]
            safe_paths = [r["path"] for r in sub_results if r["status"] == "SAFE_TO_EXECUTE"]
            blocked_subs = [r for r in sub_results if r["status"].startswith("BLOCKED_")]
            if blocked_subs:
                blocked_count += 1
                first_blocked = blocked_subs[0]
                results.append(
                    {
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
                )
            elif all(r["status"] == "MISSING_ALREADY_DONE" for r in sub_results):
                results.append(
                    {
                        "id": item_id,
                        "path": str(item.get("path") or sub_results[0]["path"]),
                        "status": "MISSING_ALREADY_DONE",
                        "reason": "All sub-target paths no longer exist.",
                        "safe_paths": [],
                        "blocked_paths": [],
                        "sub_results": sub_results,
                    }
                )
            else:
                results.append(
                    {
                        "id": item_id,
                        "path": str(item.get("path") or sub_results[0]["path"]),
                        "status": "SAFE_TO_EXECUTE",
                        "reason": f"All {len(safe_paths)} present sub-targets passed preflight checks.",
                        "safe_paths": safe_paths,
                        "blocked_paths": [],
                        "sub_results": sub_results,
                    }
                )
            continue

        raw_path = str(item.get("path") or "")
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

    p_scan = sub.add_parser("scan-local", help="Run read-only local scan for orphans, .git bloat, and backup drift.")
    p_scan.add_argument("--config", required=True, help="Path to topology.json or topology.local.json")
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
    p_pre.add_argument("--recent-minutes", type=float, default=None, help="Override recent activity window in minutes")
    p_pre.add_argument("--skip-lsof", action="store_true", help="Skip lsof invocation (for fast fixture tests)")
    p_pre.add_argument("--output", help="Write preflight JSON to path")
    p_pre.add_argument("--overwrite", action="store_true", help="Overwrite output file if it exists")

    p_quar = sub.add_parser(
        "quarantine",
        help="Stage idle project/worktree/artifact targets into external/local Quarantine with manifest.json + restore.sh.",
    )
    p_quar.add_argument("--config", required=True, help="Path to topology.json or topology.local.json")
    p_quar.add_argument("--targets", required=True, help="Path to snapshot or targets JSON")
    p_quar.add_argument("--ids", help="Comma-separated candidate IDs to quarantine (default: all)")
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
