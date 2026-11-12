import io
import json
import os
import plistlib
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

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
        self.assertNotIn("git gc --prune=now", by_id["git-pack-demo-project"]["evidence"])
        self.assertIn("普通 `git gc`", by_id["git-pack-demo-project"]["evidence"])

        # Git pack, governance doc and backup-drift candidates are refused by quarantine, never moved
        q_dir = self.root / "quarantine_refuse"
        manifest_out = self.root / "q_refuse.json"
        rc_q = full_stack_tool.main(
            [
                "quarantine",
                "--config",
                str(config_path),
                "--targets",
                str(out_path),
                "--ids",
                "git-pack-demo-project,doc-premerge-demo-project,drift-test-backup-demo-project-demo-preservation",
                "--quarantine-dir",
                str(q_dir),
                "--skip-lsof",
                "--output",
                str(manifest_out),
            ]
        )
        self.assertEqual(rc_q, 0)
        manifest = json.loads(manifest_out.read_text(encoding="utf-8"))
        self.assertEqual(manifest["staged_count"], 0)
        refused = {r["id"]: r for r in manifest["refused_items"]}
        self.assertEqual(
            set(refused),
            {"git-pack-demo-project", "doc-premerge-demo-project", "drift-test-backup-demo-project-demo-preservation"},
        )
        self.assertTrue(all(r["status"] == "REFUSED_NOT_MOVABLE" and r["reason"] for r in refused.values()))
        self.assertTrue((pack_dir / "pack-123.pack").is_file())
        self.assertTrue((repo_dir / "AGENTS.md").is_file())
        self.assertTrue((pres_dir / "artifact.bin").is_file())
        self.assertFalse(q_dir.exists())

    def test_premerge_declaration_matches_git_ship_rules(self) -> None:
        parse = full_stack_tool.parse_premerge_decl
        self.assertEqual(parse("- 合入前检查：`make test`（单测）\n"), ("command", "make test"))
        self.assertEqual(parse("**合入前检查**: `npm run check`\n"), ("command", "npm run check"))
        self.assertEqual(parse("合入前检查：无\n"), ("none", None))
        self.assertEqual(parse("- 合并前检查：`make test`\n"), (None, None))
        self.assertEqual(parse("说明见 合入前检查：`make test`\n"), (None, None))
        self.assertEqual(parse("- 合入前检查：`a` 和 `b`\n")[0], "invalid")

        only_claude = self.root / "only-claude"
        only_claude.mkdir()
        (only_claude / "AGENTS.md").write_text("# rules\n", encoding="utf-8")
        (only_claude / "CLAUDE.md").write_text("- 合入前检查：`make check`\n", encoding="utf-8")
        self.assertEqual(
            full_stack_tool.premerge_decl_for_repo(only_claude), ("command", "make check", "CLAUDE.md")
        )

        missing = self.root / "missing-decl"
        missing.mkdir()
        (missing / "AGENTS.md").write_text("提到合入前检查：但不在行首的写法\n", encoding="utf-8")
        self.assertEqual(full_stack_tool.premerge_decl_for_repo(missing), (None, None, "AGENTS.md"))

        no_docs = self.root / "no-docs"
        no_docs.mkdir()
        self.assertEqual(full_stack_tool.premerge_decl_for_repo(no_docs), (None, None, None))

    def test_quarantine_requires_ids(self) -> None:
        config_path = self.root / "topology.json"
        config_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "whitelist": {"paths": [], "repos": [], "app_names": [], "guest_ids": [], "notes": {}},
                }
            ),
            encoding="utf-8",
        )
        idle = self.root / "idle"
        idle.mkdir()
        targets_path = self.root / "targets.json"
        targets_path.write_text(json.dumps({"candidates": [{"id": "idle", "path": str(idle)}]}), encoding="utf-8")
        base = ["quarantine", "--config", str(config_path), "--targets", str(targets_path), "--dry-run"]

        with mock.patch("sys.stderr", new_callable=io.StringIO):
            with self.assertRaises(SystemExit) as ctx:
                full_stack_tool.main(base)
        self.assertEqual(ctx.exception.code, 2)
        with mock.patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(full_stack_tool.main([*base, "--ids", " , "]), 2)
        self.assertTrue(idle.is_dir())

    def test_quarantine_refuses_git_governance_launch_update_and_worktree_paths(self) -> None:
        old_ts = time.time() - 7200

        def age(*paths: Path) -> None:
            for root_p in paths:
                for dirpath, dirnames, filenames in os.walk(root_p):
                    for n in dirnames + filenames:
                        os.utime(Path(dirpath) / n, (old_ts, old_ts), follow_symlinks=False)
                os.utime(root_p, (old_ts, old_ts), follow_symlinks=False)

        clean_repo = self.root / "clean_repo"
        clean_repo.mkdir()
        subprocess.run(["git", "-C", str(clean_repo), "init", "-q"], check=True)
        pack_dir = clean_repo / ".git" / "objects" / "pack"
        pack_dir.mkdir(parents=True, exist_ok=True)
        (pack_dir / "pack-1.pack").write_bytes(b"p")

        plain_dir = self.root / "plain_project"
        plain_dir.mkdir()
        agents_md = plain_dir / "AGENTS.md"
        agents_md.write_text("# rules\n", encoding="utf-8")

        launch_dir = self.root / "LaunchAgents"
        launch_dir.mkdir()
        plist = launch_dir / "com.example.helper.plist"
        plist.write_bytes(b"<plist/>")

        stale_wt = self.root / "stale_worktree_copy"
        stale_wt.mkdir()
        (stale_wt / "file.txt").write_text("x", encoding="utf-8")

        idle = self.root / "idle_build"
        idle.mkdir()
        (idle / "out.o").write_bytes(b"o")
        age(clean_repo, plain_dir, launch_dir, stale_wt, idle)

        config_path = self.root / "topology.json"
        config_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "thresholds": {"recent_activity_minutes": 60, "worktree_protect_minutes": 60},
                    "whitelist": {"paths": [], "repos": [], "app_names": [], "guest_ids": [], "notes": {}},
                }
            ),
            encoding="utf-8",
        )
        refused_ids = [
            "raw-pack",
            "raw-agents",
            "repo-root",
            "launch",
            "launch-raw",
            "app-upd",
            "env-upd",
            "firmware",
            "wt-category",
            "remote",
        ]
        candidates = [
            {"id": "raw-pack", "path": str(pack_dir)},
            {"id": "raw-agents", "path": str(agents_md)},
            {"id": "repo-root", "path": str(clean_repo)},
            {"id": "launch", "category": "launch_item", "action_type": "cleanup", "path": str(plist)},
            {"id": "launch-raw", "path": str(plist)},
            {"id": "app-upd", "category": "app_update", "path": str(idle)},
            {"id": "env-upd", "category": "env_update", "path": str(idle)},
            {"id": "firmware", "category": "peripheral_firmware", "path": str(idle)},
            {"id": "wt-category", "category": "stale_worktree", "path": str(stale_wt)},
            {"id": "remote", "domain": "nas", "path": "pve:local-lvm"},
            {"id": "idle", "category": "build_artifact", "path": str(idle)},
        ]
        targets_path = self.root / "targets.json"
        targets_path.write_text(json.dumps({"candidates": candidates}), encoding="utf-8")
        all_ids = ",".join([*refused_ids, "idle", "not-in-plan"])

        pre_out = self.root / "preflight.json"
        rc_pre = full_stack_tool.main(
            [
                "preflight",
                "--config",
                str(config_path),
                "--targets",
                str(targets_path),
                "--ids",
                all_ids,
                "--skip-lsof",
                "--output",
                str(pre_out),
            ]
        )
        self.assertEqual(rc_pre, 0)
        pre = {r["id"]: r for r in json.loads(pre_out.read_text(encoding="utf-8"))["results"]}

        q_dir = self.root / "quarantine_store"
        q_out = self.root / "quarantine.json"
        rc_q = full_stack_tool.main(
            [
                "quarantine",
                "--config",
                str(config_path),
                "--targets",
                str(targets_path),
                "--ids",
                all_ids,
                "--quarantine-dir",
                str(q_dir),
                "--skip-lsof",
                "--output",
                str(q_out),
            ]
        )
        self.assertEqual(rc_q, 0)
        manifest = json.loads(q_out.read_text(encoding="utf-8"))
        refused = {r["id"]: r for r in manifest["refused_items"]}
        self.assertEqual(set(refused), set(refused_ids))
        for rid in refused_ids:
            self.assertEqual(refused[rid]["status"], "REFUSED_NOT_MOVABLE")
            self.assertTrue(refused[rid]["reason"])
            # preflight reports the same verdict before anything moves
            self.assertFalse(pre[rid]["quarantine_allowed"], rid)
            self.assertTrue(pre[rid]["quarantine_refusal"], rid)
        self.assertIn("repo-cleanup", refused["repo-root"]["reason"])
        self.assertIn("repo-cleanup", refused["wt-category"]["reason"])
        self.assertIn(".git", refused["raw-pack"]["reason"])
        self.assertTrue(pre["idle"]["quarantine_allowed"])
        self.assertEqual(manifest["unknown_ids"], ["not-in-plan"])

        self.assertTrue((pack_dir / "pack-1.pack").is_file())
        self.assertTrue(agents_md.is_file())
        self.assertTrue(plist.is_file())
        self.assertTrue((clean_repo / ".git").is_dir())
        self.assertTrue((stale_wt / "file.txt").is_file())
        self.assertEqual([s["id"] for s in manifest["staged_items"]], ["idle"])
        self.assertFalse(idle.exists())

    def test_quarantine_root_requires_real_mount_and_matching_uuid(self) -> None:
        fake_home = self.root / "home"
        fake_home.mkdir()
        disk = "/Volumes/FSMTestDisk"
        real_access = os.access

        def fake_access(path, mode, *a, **kw):
            if str(path).startswith(disk):
                return True
            return real_access(path, mode, *a, **kw)

        with mock.patch.dict(os.environ, {"HOME": str(fake_home)}):
            fallback = (fake_home / ".Cleanup_Quarantine").resolve()

            # No configured roots: never scan /Volumes, fall back locally with a reason
            root, info = full_stack_tool.resolve_quarantine_root({"version": 1})
            self.assertEqual(root, fallback)
            self.assertIn("不自动遍历 /Volumes", info["fallback_reason"])

            # Configured external root that is not mounted (does not exist): fall back, explain why
            cfg = {"quarantine": {"root_dirs": ["/Volumes/FSMTestNotMounted-4c1e/.Cleanup_Quarantine"]}}
            root, info = full_stack_tool.resolve_quarantine_root(cfg)
            self.assertEqual(root, fallback)
            self.assertFalse(info["external"])
            self.assertIn("没有挂载", info["fallback_reason"])

            with mock.patch.object(
                full_stack_tool.os.path, "ismount", side_effect=lambda p: str(p) == disk
            ), mock.patch.object(full_stack_tool.os, "access", side_effect=fake_access):
                uuid_cfg = {
                    "quarantine": {
                        "root_dirs": [
                            {"path": f"{disk}/.Cleanup_Quarantine", "volume_uuid": "BBBB-2222"},
                            "~/.Cleanup_Quarantine",
                        ]
                    }
                }
                with mock.patch.object(full_stack_tool, "read_volume_uuid", return_value="AAAA-1111"):
                    root, info = full_stack_tool.resolve_quarantine_root(uuid_cfg)
                self.assertEqual(root, fallback)
                self.assertIn("不一致", info["fallback_reason"])
                self.assertEqual([c["accepted"] for c in info["checks"]], [False, True])

                with mock.patch.object(full_stack_tool, "read_volume_uuid", return_value="bbbb-2222"):
                    root, info = full_stack_tool.resolve_quarantine_root(uuid_cfg)
                self.assertEqual(root, Path(f"{disk}/.Cleanup_Quarantine"))
                self.assertTrue(info["external"])
                self.assertEqual(info["fallback_reason"], "")

                with mock.patch.object(full_stack_tool, "read_volume_uuid", return_value="") as uuid_probe:
                    root, info = full_stack_tool.resolve_quarantine_root(uuid_cfg)
                self.assertEqual(root, fallback)
                self.assertIn("VolumeUUID", info["fallback_reason"])
                uuid_probe.assert_called_once()

                with mock.patch.object(full_stack_tool, "read_volume_uuid") as uuid_probe:
                    root, _ = full_stack_tool.resolve_quarantine_root(
                        {"quarantine": {"root_dirs": [f"{disk}/.Cleanup_Quarantine"]}}
                    )
                self.assertEqual(root, Path(f"{disk}/.Cleanup_Quarantine"))
                uuid_probe.assert_not_called()

            with self.assertRaises(full_stack_tool.ConfigError):
                full_stack_tool.resolve_quarantine_root({}, "/Volumes/FSMTestNotMounted-4c1e/.Q")
            with self.assertRaises(full_stack_tool.ConfigError):
                full_stack_tool.resolve_quarantine_root({"quarantine": {"root_dirs": [42]}})

            # End to end in --dry-run: unmounted root -> local fallback reported in the manifest, nothing written
            idle = self.root / "idle"
            idle.mkdir()
            (idle / "a.bin").write_bytes(b"a")
            old_ts = time.time() - 7200
            os.utime(idle / "a.bin", (old_ts, old_ts))
            os.utime(idle, (old_ts, old_ts))
            config_path = self.root / "topology.json"
            config_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "quarantine": cfg["quarantine"],
                        "whitelist": {"paths": [], "repos": [], "app_names": [], "guest_ids": [], "notes": {}},
                    }
                ),
                encoding="utf-8",
            )
            targets_path = self.root / "targets.json"
            targets_path.write_text(json.dumps({"candidates": [{"id": "idle", "path": str(idle)}]}), encoding="utf-8")
            q_out = self.root / "q.json"
            rc = full_stack_tool.main(
                [
                    "quarantine",
                    "--config",
                    str(config_path),
                    "--targets",
                    str(targets_path),
                    "--ids",
                    "idle",
                    "--skip-lsof",
                    "--dry-run",
                    "--output",
                    str(q_out),
                ]
            )
            self.assertEqual(rc, 0)
            manifest = json.loads(q_out.read_text(encoding="utf-8"))
            self.assertEqual(manifest["quarantine_root"], str(fallback))
            self.assertIn("没有挂载", manifest["quarantine_root_fallback_reason"])
            self.assertEqual(manifest["staged_count"], 1)
            self.assertTrue((idle / "a.bin").is_file())
            self.assertFalse(fallback.exists())

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
        self.assertIn("function drawRing(", html_content)
        self.assertIn("mini-rings-strip", html_content)
        self.assertIn("animateRingTo", html_content)
        self.assertIn("workbench-page", html_content)
        self.assertNotRegex(html_content, r"__[A-Z_]+__")
        self.assertIn("transition-timing-function: linear(0, 1)", html_content)
        self.assertIn('.row-main[aria-expanded="true"] .row-title { white-space: normal;', html_content)

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
                "--ids",
                "nested-in-dirty-wt,nested-in-active-wt,idle-eligible",
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

    def test_uninstall_defaults_to_keep_data_and_reports_last_used_basis(self) -> None:
        now_ts = time.time()
        old_ts = now_ts - (200 * 86400)
        apps_dir = self.root / "Applications"
        lib_support = self.root / "Library" / "Application Support"
        lib_caches = self.root / "Library" / "Caches"

        def make_app(name: str, bundle_id: str) -> tuple[Path, Path, Path]:
            app = apps_dir / f"{name}.app"
            self._write_plist(app / "Contents" / "Info.plist", {"CFBundleIdentifier": bundle_id, "CFBundleName": name})
            (app / "Contents" / "MacOS").mkdir(parents=True, exist_ok=True)
            (app / "Contents" / "MacOS" / "bin").write_bytes(b"b" * (2 * 1024 * 1024))
            data = lib_support / bundle_id
            data.mkdir(parents=True)
            (data / "user.db").write_bytes(b"d" * (1024 * 1024))
            cache = lib_caches / bundle_id
            cache.mkdir(parents=True)
            (cache / "c.bin").write_bytes(b"c" * (1024 * 1024))
            for root_p in (app, data, cache):
                for dirpath, dirnames, filenames in os.walk(root_p):
                    for n in dirnames + filenames:
                        os.utime(Path(dirpath) / n, (old_ts, old_ts), follow_symlinks=False)
                os.utime(root_p, (old_ts, old_ts))
            return app, data, cache

        idle_app, idle_data, idle_cache = make_app("IdleTool", "com.example.IdleTool")
        opened_app, _, _ = make_app("OpenedRecently", "com.example.OpenedRecently")
        spot_app, _, _ = make_app("SpotlightOld", "com.example.SpotlightOld")

        config_path = self.root / "topology.json"
        config_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "app_roots": [str(apps_dir)],
                    "library_roots": [str(lib_support), str(lib_caches)],
                    "thresholds": {"min_app_size_mb": 1, "unused_app_days": 90, "recent_activity_minutes": 60},
                    "probe_fixtures": {
                        "app_last_used": {
                            # Spotlight says opened 5 days ago: counts as used even though files are old
                            str(opened_app): int(now_ts - 5 * 86400),
                            str(spot_app): time.strftime("%Y-%m-%d %H:%M:%S +0000", time.gmtime(old_ts)),
                        }
                    },
                    "whitelist": {"paths": [], "repos": [], "app_names": [], "guest_ids": [], "notes": {}},
                }
            ),
            encoding="utf-8",
        )
        snap_out = self.root / "snapshot.json"
        self.assertEqual(full_stack_tool.main(["scan-local", "--config", str(config_path), "--output", str(snap_out)]), 0)
        snap = json.loads(snap_out.read_text(encoding="utf-8"))
        by_id = {c["id"]: c for c in snap["candidates"]}
        inv = {a["name"]: a for a in snap["app_inventory"]["apps"]}

        self.assertEqual(inv["OpenedRecently"]["usage_status"], "frequent")
        self.assertEqual(inv["OpenedRecently"]["last_used_basis"], "spotlight")
        self.assertNotIn("app-uninstall-com-example-openedrecently", by_id)

        spot = by_id["app-uninstall-com-example-spotlightold"]
        self.assertEqual(spot["last_used_basis"], "spotlight")
        self.assertIn("天未打开", spot["title"])
        self.assertIn("kMDItemLastUsedDate", spot["evidence"])

        idle = by_id["app-uninstall-com-example-idletool"]
        self.assertEqual(idle["last_used_basis"], "file_mtime")
        self.assertIn("天无文件改动", idle["title"])
        self.assertNotIn("未打开", idle["title"])
        self.assertEqual(idle["selected_uninstall_mode"], "keep_data")
        self.assertEqual(idle["paths"], idle["uninstall_modes"]["keep_data"]["paths"])

        self.assertEqual(full_stack_tool._parse_mdls_date("(null)"), 0)
        self.assertEqual(full_stack_tool._parse_mdls_date("2026-10-13 04:38:32 +0000"), 1791866312)

        # Without --uninstall-mode, preflight and quarantine both resolve keep_data: user data stays
        pre_out = self.root / "preflight.json"
        rc_pre = full_stack_tool.main(
            [
                "preflight",
                "--config",
                str(config_path),
                "--targets",
                str(snap_out),
                "--ids",
                "app-uninstall-com-example-idletool",
                "--skip-lsof",
                "--output",
                str(pre_out),
            ]
        )
        self.assertEqual(rc_pre, 0)
        pre = json.loads(pre_out.read_text(encoding="utf-8"))["results"][0]
        self.assertEqual(pre["uninstall_mode"], "keep_data")
        self.assertNotIn(str(idle_data.resolve()), pre["safe_paths"])

        q_out = self.root / "q.json"
        rc_q = full_stack_tool.main(
            [
                "quarantine",
                "--config",
                str(config_path),
                "--targets",
                str(snap_out),
                "--ids",
                "app-uninstall-com-example-idletool",
                "--quarantine-dir",
                str(self.root / "q"),
                "--skip-lsof",
                "--output",
                str(q_out),
            ]
        )
        self.assertEqual(rc_q, 0)
        manifest = json.loads(q_out.read_text(encoding="utf-8"))
        self.assertEqual({s["uninstall_mode"] for s in manifest["staged_items"]}, {"keep_data"})
        self.assertFalse(idle_app.exists())
        self.assertFalse(idle_cache.exists())
        self.assertTrue((idle_data / "user.db").is_file())

        html_out = self.root / "dashboard.html"
        self.assertEqual(render_dashboard.main(["--input", str(snap_out), "--output", str(html_out)]), 0)
        self.assertIn('return "keep_data";', html_out.read_text(encoding="utf-8"))

    def test_mac_extended_inspections_and_dual_mode_uninstall(self) -> None:
        now_ts = time.time()
        old_app_ts = now_ts - (120 * 86400)  # 120 days ago (unused)
        recent_app_ts = now_ts - (5 * 86400)  # 5 days ago (frequently used)
        safe_old_mtime = now_ts - 10800  # 3 hours ago (safe for preflight)

        apps_dir = self.root / "Applications"
        lib_root = self.root / "Library"
        lib_support = lib_root / "Application Support"
        lib_caches = lib_root / "Caches"
        lib_containers = lib_root / "Containers"
        lib_saved_state = lib_root / "Saved Application State"
        launch_agents = lib_root / "LaunchAgents"
        global_cache = self.root / "GlobalCaches" / "DerivedData"

        # 1. Frequently used app: DailyCoder.app (should appear in app_inventory as frequent, NOT in app_uninstall)
        daily_app = apps_dir / "DailyCoder.app"
        self._write_plist(
            daily_app / "Contents" / "Info.plist",
            {"CFBundleIdentifier": "com.example.DailyCoder", "CFBundleName": "DailyCoder"},
        )
        (daily_app / "Contents" / "MacOS").mkdir(parents=True, exist_ok=True)
        (daily_app / "Contents" / "MacOS" / "bin").write_bytes(b"d" * (2 * 1024 * 1024))
        os.utime(daily_app, (recent_app_ts, recent_app_ts))

        # 2. Unused app: OldHeavyGame.app (120 days unused, has app bundle + user data + cache)
        unused_app = apps_dir / "OldHeavyGame.app"
        self._write_plist(
            unused_app / "Contents" / "Info.plist",
            {"CFBundleIdentifier": "com. studio.OldHeavyGame".replace(" ", ""), "CFBundleName": "OldHeavyGame"},
        )
        (unused_app / "Contents" / "MacOS").mkdir(parents=True, exist_ok=True)
        app_bin = unused_app / "Contents" / "MacOS" / "game"
        app_bin.write_bytes(b"g" * (2 * 1024 * 1024))

        game_data = lib_support / "com.studio.OldHeavyGame"
        game_data.mkdir(parents=True)
        save_file = game_data / "savegame.dat"
        save_file.write_bytes(b"s" * (1 * 1024 * 1024))

        game_cache = lib_caches / "com.studio.OldHeavyGame"
        game_cache.mkdir(parents=True)
        shader_file = game_cache / "shaders.bin"
        shader_file.write_bytes(b"c" * (1 * 1024 * 1024))

        game_state = lib_saved_state / "com.studio.OldHeavyGame.savedState"
        game_state.mkdir(parents=True)
        state_file = game_state / "windows.plist"
        state_file.write_bytes(b"w" * (512 * 1024))

        for p in [unused_app, app_bin, game_data, save_file, game_cache, shader_file, game_state, state_file]:
            os.utime(p, (old_app_ts, old_app_ts), follow_symlinks=False)
        os.utime(unused_app / "Contents" / "Info.plist", (old_app_ts, old_app_ts))
        os.utime(unused_app / "Contents" / "MacOS", (old_app_ts, old_app_ts))
        os.utime(unused_app / "Contents", (old_app_ts, old_app_ts))
        os.utime(unused_app, (old_app_ts, old_app_ts))

        # 3. Uninstalled app leftovers scattered across multiple Library dirs (should group into 1 multi-path app_orphan)
        orphan_sup = lib_support / "com.ghost.RemovedSuite"
        orphan_sup.mkdir(parents=True)
        (orphan_sup / "db.sqlite").write_bytes(b"1" * (1 * 1024 * 1024))
        orphan_cache = lib_caches / "com.ghost.RemovedSuite"
        orphan_cache.mkdir(parents=True)
        (orphan_cache / "thumb.cache").write_bytes(b"2" * (1 * 1024 * 1024))
        orphan_cont = lib_containers / "com.ghost.RemovedSuite"
        orphan_cont.mkdir(parents=True)
        (orphan_cont / "container.bin").write_bytes(b"3" * (1 * 1024 * 1024))

        # 4. Broken LaunchAgents (missing binary + crash loop)
        self._write_plist(
            launch_agents / "com.ghost.RemovedSuite.agent.plist",
            {
                "Label": "com.ghost.RemovedSuite.agent",
                "ProgramArguments": ["/Applications/RemovedSuite.app/Contents/MacOS/helper", "--daemon"],
            },
        )
        # Crash loop agent belonging to installed DailyCoder app so it reaches check #3 (crash loop)
        valid_exec = daily_app / "Contents" / "MacOS" / "bin"
        self._write_plist(
            launch_agents / "com.example.DailyCoder.helper.plist",
            {
                "Label": "com.example.DailyCoder.helper",
                "Program": str(valid_exec),
            },
        )

        # 5. Reproducible global pure cache
        global_cache.mkdir(parents=True)
        (global_cache / "index.noindex").write_bytes(b"x" * (2 * 1024 * 1024))

        config_path = self.root / "topology_ext.json"
        config_data = {
            "version": 1,
            "workspace_roots": [],
            "app_roots": [str(apps_dir)],
            "library_roots": [
                str(lib_support),
                str(lib_caches),
                str(lib_containers),
                str(lib_saved_state),
            ],
            "launch_agent_roots": [str(launch_agents)],
            "global_cache_roots": [str(global_cache)],
            "thresholds": {
                "min_orphan_mb": 1,
                "min_app_size_mb": 1,
                "unused_app_days": 90,
                "min_cache_mb": 1,
                "recent_activity_minutes": 60,
            },
            "storage_gauges": [
                {
                    "id": "mac-data",
                    "label": "Mac Data",
                    "mount_path": str(self.root),
                    "warn_used_pct": 85.0,
                }
            ],
            "probe_fixtures": {
                "launchctl_status": {"com.example.DailyCoder.helper": {"pid": None, "exit_code": 78}},
                "app_updates": [
                    {
                        "name": "Raycast",
                        "token": "raycast",
                        "current_version": "1.80.0",
                        "latest_version": "1.84.2",
                        "source": "Homebrew Cask",
                        "command": "brew upgrade --cask raycast",
                    }
                ],
                "env_updates": [
                    {
                        "ecosystem": "Homebrew Formulae",
                        "count": 2,
                        "packages": ["ripgrep (14.0 -> 14.1)", "fd (9.0 -> 10.2)"],
                        "command": "brew upgrade",
                    }
                ],
                "peripherals": {
                    "storage_devices": [
                        {
                            "name": "External NVMe Media",
                            "kind": "external_drive",
                            "vendor": "Samsung",
                            "firmware_version": "SVT01B6Q",
                            "smart_status": "Failing",
                            "link_speed": "Up to 480 Mb/s",
                            "transport": "USB",
                        }
                    ],
                    "docks_and_hubs": [
                        {
                            "name": "CalDigit TS4 Dock",
                            "vendor": "CalDigit",
                            "firmware_version": "39.1",
                            "latest_firmware": "43.1",
                            "update_available": True,
                            "link_speed": "40 Gb/s",
                            "transport": "Thunderbolt",
                        }
                    ],
                    "input_devices": [
                        {
                            "name": "MX Master 3S",
                            "kind": "mouse",
                            "vendor": "Logitech",
                            "firmware_version": "22.01.0015",
                            "battery_pct": 15,
                            "transport": "Bluetooth",
                        }
                    ],
                },
            },
            "whitelist": {"paths": [], "repos": [], "app_names": [], "guest_ids": [], "notes": {}},
        }
        config_path.write_text(json.dumps(config_data), encoding="utf-8")

        snap_out = self.root / "snapshot_ext.json"
        rc = full_stack_tool.main(["scan-local", "--config", str(config_path), "--output", str(snap_out)])
        self.assertEqual(rc, 0)

        snap = json.loads(snap_out.read_text(encoding="utf-8"))
        by_id = {c["id"]: c for c in snap["candidates"]}

        # Verify app_inventory summary
        inv = snap["app_inventory"]
        self.assertEqual(inv["total_apps"], 2)
        self.assertEqual(inv["frequent_count"], 1)
        self.assertEqual(inv["infrequent_count"], 1)

        # Verify multi-directory app_orphan grouping
        self.assertIn("orphan-com-ghost-removedsuite", by_id)
        orphan_cand = by_id["orphan-com-ghost-removedsuite"]
        self.assertEqual(len(orphan_cand["paths"]), 3)

        # Verify dual-mode app_uninstall candidate
        self.assertIn("app-uninstall-com-studio-oldheavygame", by_id)
        uninst_cand = by_id["app-uninstall-com-studio-oldheavygame"]
        modes = uninst_cand["uninstall_modes"]
        self.assertIn("purge_all", modes)
        self.assertIn("keep_data", modes)
        self.assertGreater(modes["purge_all"]["size_bytes"], modes["keep_data"]["size_bytes"])
        self.assertIn(str(game_data.resolve()), modes["purge_all"]["paths"])
        self.assertNotIn(str(game_data.resolve()), modes["keep_data"]["paths"])
        self.assertIn(str(game_cache.resolve()), modes["keep_data"]["paths"])
        self.assertIn(str(game_state.resolve()), modes["keep_data"]["paths"])

        # Verify launch_item, system_cache, app_update, env_update, peripheral_firmware
        self.assertIn("launch-broken-com-ghost-removedsuite-agent", by_id)
        self.assertIn("launch-crash-com-example-dailycoder-helper", by_id)
        self.assertTrue(any(c["category"] == "system_cache" for c in snap["candidates"]))
        self.assertIn("app-update-raycast", by_id)
        self.assertIn("env-update-homebrew-formulae", by_id)
        self.assertIn("hw-smart-external-nvme-media", by_id)
        self.assertIn("hw-speed-external-nvme-media", by_id)
        self.assertIn("hw-fw-caldigit-ts4-dock", by_id)
        self.assertIn("hw-battery-mx-master-3s", by_id)

        # Render dashboard HTML and verify dual-mode UI & category labels
        html_out = self.root / "dashboard_ext.html"
        self.assertEqual(render_dashboard.main(["--input", str(snap_out), "--output", str(html_out)]), 0)
        html_str = html_out.read_text(encoding="utf-8")
        self.assertIn("uninstall-mode-box", html_str)
        self.assertIn("setUninstallMode", html_str)
        self.assertIn("保留用户数据", html_str)
        self.assertIn("一键全部删除", html_str)

        # Test quarantine in keep-data mode: should stage .app + cache + savedState, but keep Application Support data!
        q_dir = self.root / "quarantine_keep_data"
        q_manifest_path = self.root / "q_keep_data.json"
        rc_q = full_stack_tool.main(
            [
                "quarantine",
                "--config",
                str(config_path),
                "--targets",
                str(snap_out),
                "--ids",
                "app-uninstall-com-studio-oldheavygame",
                "--uninstall-mode",
                "keep-data",
                "--quarantine-dir",
                str(q_dir),
                "--skip-lsof",
                "--output",
                str(q_manifest_path),
            ]
        )
        self.assertEqual(rc_q, 0)
        q_manifest = json.loads(q_manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(q_manifest["staged_count"], 3)
        self.assertFalse(unused_app.exists())
        self.assertFalse(game_cache.exists())
        self.assertFalse(game_state.exists())
        # User data in Application Support MUST still exist!
        self.assertTrue(save_file.is_file())

        # Restore via restore.sh and verify .app and caches come back
        subprocess.run(["bash", str(q_manifest["restore_script"])], check=True)
        self.assertTrue(app_bin.is_file())
        self.assertTrue(shader_file.is_file())
        self.assertTrue(state_file.is_file())

        # Reset mtimes back to old_app_ts after restore so preflight sees them as idle, then test purge-all mode
        for root_p in [unused_app, game_data, game_cache, game_state]:
            for dirpath, dirnames, filenames in os.walk(root_p):
                for n in dirnames + filenames:
                    os.utime(Path(dirpath) / n, (old_app_ts, old_app_ts), follow_symlinks=False)
            os.utime(root_p, (old_app_ts, old_app_ts))

        q_dir_purge = self.root / "quarantine_purge_all"
        q_purge_path = self.root / "q_purge_all.json"
        rc_q2 = full_stack_tool.main(
            [
                "quarantine",
                "--config",
                str(config_path),
                "--targets",
                str(snap_out),
                "--ids",
                "app-uninstall-com-studio-oldheavygame",
                "--uninstall-mode",
                "purge-all",
                "--quarantine-dir",
                str(q_dir_purge),
                "--skip-lsof",
                "--output",
                str(q_purge_path),
            ]
        )
        self.assertEqual(rc_q2, 0)
        q_purge = json.loads(q_purge_path.read_text(encoding="utf-8"))
        self.assertEqual(q_purge["staged_count"], 4)
        self.assertFalse(unused_app.exists())
        self.assertFalse(game_data.exists())
        self.assertFalse(game_cache.exists())
        self.assertFalse(game_state.exists())


if __name__ == "__main__":
    unittest.main()
