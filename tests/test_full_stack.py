import json
import os
import plistlib
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import full_stack_tool  # noqa: E402
import render_dashboard  # noqa: E402


class FullStackMaintenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_plist(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as fp:
            plistlib.dump(payload, fp)

    def test_fail_closed_config_validation(self) -> None:
        missing = self.root / "missing.json"
        self.assertEqual(full_stack_tool.main(["scan-local", "--config", str(missing)]), 2)

        bad_json = self.root / "bad.json"
        bad_json.write_text("{not-json", encoding="utf-8")
        self.assertEqual(full_stack_tool.main(["scan-local", "--config", str(bad_json)]), 2)

        bad_wl = self.root / "bad_wl.json"
        bad_wl.write_text(
            json.dumps({"version": 1, "whitelist": {"paths": "not-a-list"}}),
            encoding="utf-8",
        )
        self.assertEqual(full_stack_tool.main(["scan-local", "--config", str(bad_wl)]), 2)

    def test_scan_local_orphans_git_pack_and_backup_drift(self) -> None:
        apps_dir = self.root / "Applications"
        lib_support = self.root / "Library" / "Application Support"
        ws_dir = self.root / "Codex"

        # 1. Installed app: SuperStudio.app
        self._write_plist(
            apps_dir / "SuperStudio.app" / "Contents" / "Info.plist",
            {
                "CFBundleIdentifier": "com.acme.SuperStudio",
                "CFBundleName": "SuperStudio",
            },
        )
        # Matching support dir (should NOT be flagged as orphan)
        installed_support = lib_support / "com.acme.SuperStudio"
        installed_support.mkdir(parents=True)
        (installed_support / "cache.bin").write_bytes(b"x" * (2 * 1024 * 1024))

        # 2. Uninstalled app support dir (should be flagged as app_orphan)
        orphan_support = lib_support / "AbandonedApp"
        orphan_support.mkdir(parents=True)
        (orphan_support / "data.bin").write_bytes(b"y" * (2 * 1024 * 1024))

        # 3. Whitelisted app support dir (should be marked whitelisted=True)
        wl_support = lib_support / "ProtectedLegacyApp"
        wl_support.mkdir(parents=True)
        (wl_support / "keep.bin").write_bytes(b"z" * (2 * 1024 * 1024))

        # 4. Git repository with bloated pack, missing pre-merge line, and unexcluded preservation dir
        repo_dir = ws_dir / "demo-project"
        pack_dir = repo_dir / ".git" / "objects" / "pack"
        pack_dir.mkdir(parents=True)
        (pack_dir / "pack-123.pack").write_bytes(b"p" * (2 * 1024 * 1024))
        (repo_dir / "AGENTS.md").write_text("# Demo Rules\nNo check line here.\n", encoding="utf-8")

        pres_dir = repo_dir / "demo-preservation"
        pres_dir.mkdir(parents=True)
        (pres_dir / "artifact.bin").write_bytes(b"a" * (2 * 1024 * 1024))

        backup_script = self.root / "backup.py"
        backup_script.write_text("# excludes: target, node_modules\n", encoding="utf-8")

        config_path = self.root / "topology.json"
        config_data = {
            "version": 1,
            "workspace_roots": [str(ws_dir)],
            "worktree_hubs": [],
            "app_roots": [str(apps_dir)],
            "library_roots": [str(lib_support)],
            "thresholds": {
                "min_orphan_mb": 1,
                "min_git_pack_mb": 1,
                "min_build_artifact_mb": 1,
                "recent_activity_minutes": 60,
            },
            "storage_gauges": [
                {
                    "id": "mac-data",
                    "label": "Test Volume",
                    "mount_path": str(self.root),
                    "warn_used_pct": 80.0,
                }
            ],
            "backup_pipelines": [
                {
                    "id": "test-backup",
                    "label": "Test Cloud Backup",
                    "script_path": str(backup_script),
                    "watched_roots": [str(ws_dir)],
                    "expected_exclude_globs": ["target", "node_modules"],
                }
            ],
            "homelab": {"enabled": False},
            "whitelist": {
                "paths": [str(wl_support)],
                "repos": [],
                "app_names": [],
                "guest_ids": [],
                "notes": {str(wl_support): "Keep legacy plugin data"},
            },
        }
        config_path.write_text(json.dumps(config_data), encoding="utf-8")

        out_path = self.root / "snapshot.json"
        rc = full_stack_tool.main(["scan-local", "--config", str(config_path), "--output", str(out_path)])
        self.assertEqual(rc, 0)

        snap = json.loads(out_path.read_text(encoding="utf-8"))
        by_id = {c["id"]: c for c in snap["candidates"]}

        self.assertNotIn("orphan-com-acme-superstudio", by_id)
        self.assertIn("orphan-abandonedapp", by_id)
        self.assertFalse(by_id["orphan-abandonedapp"]["whitelisted"])

        self.assertIn("orphan-protectedlegacyapp", by_id)
        self.assertTrue(by_id["orphan-protectedlegacyapp"]["whitelisted"])
        self.assertEqual(by_id["orphan-protectedlegacyapp"]["whitelist_note"], "Keep legacy plugin data")

        self.assertIn("git-pack-demo-project", by_id)
        self.assertIn("doc-premerge-demo-project", by_id)
        self.assertIn("build-demo-project-demo-preservation", by_id)
        self.assertIn("drift-test-backup-demo-project-demo-preservation", by_id)

    def test_diff_and_render_dashboard(self) -> None:
        before_path = self.root / "before.json"
        after_path = self.root / "after.json"
        diff_path = self.root / "diff.json"
        html_path = self.root / "dashboard.html"

        before_snap = {
            "schema": 1,
            "generated_at": "2026-10-20T00:00:00+00:00",
            "storage_gauges": [
                {
                    "id": "mac-data",
                    "label": "Mac Data",
                    "status": "ok",
                    "total_bytes": 100 * 1024**3,
                    "used_bytes": 60 * 1024**3,
                    "free_bytes": 40 * 1024**3,
                    "used_pct": 60.0,
                    "warn_used_pct": 80.0,
                }
            ],
            "candidates": [{"id": "old-cache", "title": "Old Cache", "size_bytes": 1024}],
            "directory_sizes": {"/tmp/project/target": 1 * 1024**3},
        }
        after_snap = {
            "schema": 1,
            "generated_at": "2026-10-28T00:00:00+00:00",
            "storage_gauges": [
                {
                    "id": "mac-data",
                    "label": "Mac Data",
                    "status": "ok",
                    "total_bytes": 100 * 1024**3,
                    "used_bytes": 75 * 1024**3,
                    "free_bytes": 25 * 1024**3,
                    "used_pct": 75.0,
                    "warn_used_pct": 80.0,
                }
            ],
            "candidates": [
                {
                    "id": "build-target",
                    "domain": "mac",
                    "tier": "A",
                    "title": "Rust Build Target",
                    "path": "/tmp/project/target",
                    "size_bytes": 10 * 1024**3,
                    "gauge_id": "mac-data",
                    "whitelisted": False,
                    "evidence": "Reproducible via cargo build",
                }
            ],
            "directory_sizes": {"/tmp/project/target": 10 * 1024**3},
        }
        before_path.write_text(json.dumps(before_snap), encoding="utf-8")
        after_path.write_text(json.dumps(after_snap), encoding="utf-8")

        rc_diff = full_stack_tool.main(
            ["diff", "--before", str(before_path), "--after", str(after_path), "--output", str(diff_path)]
        )
        self.assertEqual(rc_diff, 0)
        diff_data = json.loads(diff_path.read_text(encoding="utf-8"))
        self.assertEqual(diff_data["gauge_deltas"][0]["used_delta_bytes"], 15 * 1024**3)
        self.assertEqual(diff_data["top_growth_paths"][0]["path"], "/tmp/project/target")
        self.assertEqual(diff_data["new_candidate_ids"], ["build-target"])
        self.assertEqual(diff_data["resolved_candidate_ids"], ["old-cache"])

        rc_html = render_dashboard.main(
            [
                "--input",
                str(after_path),
                "--diff",
                str(diff_path),
                "--output",
                str(html_path),
            ]
        )
        self.assertEqual(rc_html, 0)
        html_content = html_path.read_text(encoding="utf-8")
        self.assertIn("Rust Build Target", html_content)
        self.assertIn("localStorage", html_content)
        self.assertIn("微调备注", html_content)
        self.assertIn("距上次巡检基线变化", html_content)

    def test_preflight_concurrency_and_whitelist_guards(self) -> None:
        safe_dir = self.root / "safe_old_cache"
        safe_dir.mkdir()
        safe_file = safe_dir / "old.dat"
        safe_file.write_bytes(b"123")
        old_ts = time.time() - 7200  # 2 hours ago
        os.utime(safe_file, (old_ts, old_ts))
        os.utime(safe_dir, (old_ts, old_ts))

        active_dir = self.root / "active_concurrent_build"
        active_dir.mkdir()
        (active_dir / "fresh.o").write_bytes(b"456")

        wl_dir = self.root / "whitelisted_dir"
        wl_dir.mkdir()
        os.utime(wl_dir, (old_ts, old_ts))

        symlink_dir = self.root / "link_to_safe"
        symlink_dir.symlink_to(safe_dir)

        config_path = self.root / "topology.json"
        config_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "thresholds": {"recent_activity_minutes": 60},
                    "whitelist": {
                        "paths": [str(wl_dir)],
                        "repos": [],
                        "app_names": [],
                        "guest_ids": [],
                        "notes": {},
                    },
                }
            ),
            encoding="utf-8",
        )

        targets_path = self.root / "targets.json"
        targets_path.write_text(
            json.dumps(
                {
                    "candidates": [
                        {"id": "t-safe", "path": str(safe_dir)},
                        {"id": "t-active", "path": str(active_dir)},
                        {"id": "t-wl", "path": str(wl_dir)},
                        {"id": "t-link", "path": str(symlink_dir)},
                        {"id": "t-missing", "path": str(self.root / "nonexistent")},
                    ]
                }
            ),
            encoding="utf-8",
        )

        out_path = self.root / "preflight.json"
        rc = full_stack_tool.main(
            [
                "preflight",
                "--config",
                str(config_path),
                "--targets",
                str(targets_path),
                "--skip-lsof",
                "--output",
                str(out_path),
            ]
        )
        self.assertEqual(rc, 0)
        res = json.loads(out_path.read_text(encoding="utf-8"))
        status_map = {r["id"]: r["status"] for r in res["results"]}

        self.assertEqual(status_map["t-safe"], "SAFE_TO_EXECUTE")
        self.assertEqual(status_map["t-active"], "BLOCKED_RECENT_ACTIVITY")
        self.assertEqual(status_map["t-wl"], "BLOCKED_WHITELISTED")
        self.assertEqual(status_map["t-link"], "BLOCKED_SYMLINK_ROOT")
        self.assertEqual(status_map["t-missing"], "MISSING_ALREADY_DONE")
        self.assertEqual(res["overall_status"], "PARTIAL_BLOCKED")


if __name__ == "__main__":
    unittest.main()
