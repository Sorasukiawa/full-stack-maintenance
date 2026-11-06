import json
import os
import plistlib
import subprocess
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
        ws_dir = self.root / "Projects"

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
        self.assertIn("ring-overview-page", html_content)
        self.assertIn("ring-canvas", html_content)
        self.assertIn("drawActivityRings", html_content)
        self.assertIn("mini-rings-strip", html_content)
        self.assertIn("animateRingTo", html_content)
        self.assertIn("workbench-page", html_content)
        self.assertNotRegex(html_content, r"__[A-Z_]+__")
        self.assertIn("transition-timing-function: linear(0, 1)", html_content)

    def test_dashboard_escapes_report_data_and_spring_curve(self) -> None:
        snapshot = {
            "generated_at": "2026-10-28T00:00:00+00:00",
            "storage_gauges": [],
            "candidates": [
                {"id": "evil", "title": "</script><script>alert(1)</script> __TITLE__", "size_bytes": 0}
            ],
        }
        html_text = render_dashboard.render_dashboard_html(snapshot, title="<b>巡检</b>")
        self.assertEqual(html_text.count("</script>"), 1)
        self.assertIn("&lt;b&gt;巡检&lt;/b&gt;", html_text)
        self.assertIn("__TITLE__", html_text)  # placeholder text inside user data stays untouched

        curve, duration_ms = render_dashboard.spring_easing(damping=0.72, response=0.42)
        values = [float(v) for v in curve[len("linear("):-1].split(",")]
        self.assertEqual(values[0], 0.0)
        self.assertEqual(values[-1], 1.0)
        self.assertGreater(max(values), 1.01)  # light rebound
        self.assertLess(max(values), 1.08)
        self.assertGreater(duration_ms, 300)

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

    def test_preflight_git_auto_detect_multi_paths_and_remote_specs(self) -> None:
        old_ts = time.time() - 7200  # 2 hours ago

        # 1. Dirty git repo with custom category (should auto-detect .git and return BLOCKED_DIRTY_WORKTREE)
        dirty_repo = self.root / "dirty_worktree"
        dirty_repo.mkdir()
        subprocess.run(["git", "-C", str(dirty_repo), "init", "-q"], check=True)
        untracked = dirty_repo / "untracked.txt"
        untracked.write_text("wip", encoding="utf-8")
        for root_p, dirs, files in os.walk(dirty_repo):
            for name in files + dirs:
                p = Path(root_p) / name
                os.utime(p, (old_ts, old_ts), follow_symlinks=False)
        os.utime(dirty_repo, (old_ts, old_ts))

        # 2. Multi-path aggregated item (one safe sub-path, one active sub-path)
        sub_safe = self.root / "sub_safe"
        sub_safe.mkdir()
        f_safe = sub_safe / "a.bin"
        f_safe.write_bytes(b"ok")
        os.utime(f_safe, (old_ts, old_ts))
        os.utime(sub_safe, (old_ts, old_ts))

        sub_busy = self.root / "sub_busy"
        sub_busy.mkdir()
        (sub_busy / "recent.bin").write_bytes(b"busy")

        config_path = self.root / "topology.json"
        config_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "thresholds": {"recent_activity_minutes": 60},
                    "whitelist": {
                        "paths": [],
                        "repos": [],
                        "app_names": [],
                        "guest_ids": [],
                        "notes": {},
                    },
                }
            ),
            encoding="utf-8",
        )

        targets_path = self.root / "targets_ext.json"
        targets_path.write_text(
            json.dumps(
                {
                    "candidates": [
                        {
                            "id": "t-git-dirty",
                            "category": "~/Codex (本地项目与构建)",
                            "path": str(dirty_repo),
                        },
                        {
                            "id": "t-multi",
                            "path": str(self.root),
                            "paths": [str(sub_safe), str(sub_busy)],
                        },
                        {
                            "id": "t-remote",
                            "domain": "nas",
                            "path": "pve:local-lvm",
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )

        out_path = self.root / "preflight_ext.json"
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
        by_id = {r["id"]: r for r in res["results"]}

        self.assertEqual(by_id["t-git-dirty"]["status"], "BLOCKED_DIRTY_WORKTREE")
        self.assertEqual(by_id["t-multi"]["status"], "BLOCKED_RECENT_ACTIVITY")
        self.assertEqual(by_id["t-multi"]["safe_paths"], [str(sub_safe)])
        self.assertEqual(by_id["t-multi"]["blocked_paths"], [str(sub_busy)])
        self.assertEqual(by_id["t-remote"]["status"], "SAFE_TO_EXECUTE")

    def test_whole_worktree_cascading_protection_and_quarantine_restore(self) -> None:
        old_ts = time.time() - 10800  # 3 hours ago

        # 1. Worktree with old artifacts dir, but dirty status at worktree root -> nested artifacts MUST be blocked
        wt_dirty = self.root / "wt_dirty"
        wt_dirty_artifacts = wt_dirty / "distribution" / "artifacts"
        wt_dirty_artifacts.mkdir(parents=True)
        subprocess.run(["git", "-C", str(wt_dirty), "init", "-q"], check=True)
        sample_csv = wt_dirty_artifacts / "sample.csv"
        sample_csv.write_text("a,b\n1,2\n", encoding="utf-8")
        (wt_dirty / "wip.txt").write_text("in progress", encoding="utf-8")
        for root_p, dirs, files in os.walk(wt_dirty):
            for name in files + dirs:
                os.utime(Path(root_p) / name, (old_ts, old_ts), follow_symlinks=False)
        os.utime(wt_dirty, (old_ts, old_ts))

        # 2. Clean worktree with old target dir, but recent doc edit inside worktree -> nested target MUST be blocked
        wt_active = self.root / "wt_active"
        wt_active_target = wt_active / "src-tauri" / "target"
        wt_active_target.mkdir(parents=True)
        subprocess.run(["git", "-C", str(wt_active), "init", "-q"], check=True)
        old_bin = wt_active_target / "app.bin"
        old_bin.write_bytes(b"binary")
        for root_p, dirs, files in os.walk(wt_active):
            for name in files + dirs:
                os.utime(Path(root_p) / name, (old_ts, old_ts), follow_symlinks=False)
        os.utime(wt_active, (old_ts, old_ts))
        # Touch a file elsewhere in wt_active recently
        recent_doc = wt_active / "HANDOFF.md"
        recent_doc.write_text("recent note", encoding="utf-8")

        # 3. Truly idle directory eligible for quarantine
        idle_proj = self.root / "idle_project_artifacts"
        idle_proj.mkdir()
        idle_payload = idle_proj / "result.json"
        idle_payload.write_text('{"ok": true}\n', encoding="utf-8")
        os.utime(idle_payload, (old_ts, old_ts))
        os.utime(idle_proj, (old_ts, old_ts))

        config_path = self.root / "topology.json"
        config_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "thresholds": {
                        "recent_activity_minutes": 60,
                        "worktree_protect_minutes": 120,
                    },
                    "whitelist": {
                        "paths": [],
                        "repos": [],
                        "app_names": [],
                        "guest_ids": [],
                        "notes": {},
                    },
                }
            ),
            encoding="utf-8",
        )

        targets_path = self.root / "targets_wt.json"
        targets_path.write_text(
            json.dumps(
                {
                    "candidates": [
                        {"id": "nested-in-dirty-wt", "path": str(wt_dirty_artifacts)},
                        {"id": "nested-in-active-wt", "path": str(wt_active_target)},
                        {"id": "idle-eligible", "path": str(idle_proj)},
                    ]
                }
            ),
            encoding="utf-8",
        )

        q_dir = self.root / "quarantine_store"
        manifest_out = self.root / "quarantine_manifest.json"
        rc = full_stack_tool.main(
            [
                "quarantine",
                "--config",
                str(config_path),
                "--targets",
                str(targets_path),
                "--quarantine-dir",
                str(q_dir),
                "--skip-lsof",
                "--output",
                str(manifest_out),
            ]
        )
        self.assertEqual(rc, 0)
        manifest = json.loads(manifest_out.read_text(encoding="utf-8"))

        # Both active/dirty worktree subdirectories must be blocked and untouched
        self.assertTrue(sample_csv.exists())
        self.assertTrue(old_bin.exists())
        blocked_by_id = {b["id"]: b["status"] for b in manifest["blocked_items"]}
        self.assertEqual(blocked_by_id["nested-in-dirty-wt"], "BLOCKED_DIRTY_WORKTREE")
        self.assertEqual(blocked_by_id["nested-in-active-wt"], "BLOCKED_RECENT_ACTIVITY")

        # Idle directory must be moved into quarantine and restorable via restore.sh
        self.assertEqual(manifest["staged_count"], 1)
        self.assertFalse(idle_proj.exists())
        restore_sh = Path(manifest["restore_script"])
        self.assertTrue(restore_sh.is_file())
        subprocess.run(["bash", str(restore_sh)], check=True)
        self.assertTrue((idle_proj / "result.json").is_file())


if __name__ == "__main__":
    unittest.main()
