from __future__ import annotations

import hashlib
import io
import json
import os
import plistlib
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import full_stack_tool  # noqa: E402


class EngineSafetyAndMatchingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_plist(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as fp:
            plistlib.dump(payload, fp)

    def _age_tree(self, root_path: Path, ts: float) -> None:
        if not root_path.exists():
            return
        for dirpath, dirnames, filenames in os.walk(root_path):
            for name in dirnames + filenames:
                os.utime(Path(dirpath) / name, (ts, ts), follow_symlinks=False)
        os.utime(root_path, (ts, ts), follow_symlinks=False)

    def _minimal_config(self, **overrides: object) -> Path:
        cfg = {
            "version": 1,
            "workspace_roots": [],
            "app_roots": [],
            "library_roots": [],
            "launch_agent_roots": [],
            "global_cache_roots": [],
            "storage_gauges": [],
            "thresholds": {
                "min_orphan_mb": 1,
                "min_app_size_mb": 1,
                "unused_app_days": 90,
                "min_cache_mb": 1,
                "min_git_pack_mb": 1,
                "min_build_artifact_mb": 1,
                "recent_activity_minutes": 60,
                "worktree_protect_minutes": 60,
            },
            "whitelist": {
                "paths": [],
                "repos": [],
                "app_names": [],
                "guest_ids": [],
                "notes": {},
            },
        }
        cfg.update(overrides)
        cfg_path = self.root / "topology.json"
        cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
        return cfg_path

    # ------------------------------------------------------------------
    # 1. Quarantine partial-failure resilience, ledger safety & restore.sh
    # ------------------------------------------------------------------
    def test_quarantine_partial_failure_writes_manifest_and_restore_and_returns_nonzero(self) -> None:
        old_ts = time.time() - 7200
        dir_ok1 = self.root / "item_ok_1"
        dir_fail = self.root / "item_perm_denied"
        dir_ok2 = self.root / "item_ok_2"
        for d, content in ((dir_ok1, b"one"), (dir_fail, b"denied"), (dir_ok2, b"two")):
            d.mkdir(parents=True)
            (d / "data.bin").write_bytes(content)
            self._age_tree(d, old_ts)

        cfg_path = self._minimal_config()
        targets_path = self.root / "targets.json"
        targets_path.write_text(
            json.dumps(
                {
                    "candidates": [
                        {"id": "ok-1", "category": "build_artifact", "path": str(dir_ok1)},
                        {"id": "perm-fail", "category": "build_artifact", "path": str(dir_fail)},
                        {"id": "ok-2", "category": "build_artifact", "path": str(dir_ok2)},
                    ]
                }
            ),
            encoding="utf-8",
        )

        q_dir = self.root / "quarantine_root"
        out_json = self.root / "q_result.json"
        real_move = full_stack_tool.shutil.move

        def flaky_move(src: str, dst: str, *args: object, **kwargs: object) -> object:
            if Path(src).resolve(strict=False) == dir_fail.resolve(strict=False):
                raise PermissionError(13, "Operation not permitted", src)
            return real_move(src, dst, *args, **kwargs)

        with mock.patch.object(full_stack_tool.shutil, "move", side_effect=flaky_move):
            rc = full_stack_tool.main(
                [
                    "quarantine",
                    "--config",
                    str(cfg_path),
                    "--targets",
                    str(targets_path),
                    "--ids",
                    "ok-1,perm-fail,ok-2",
                    "--quarantine-dir",
                    str(q_dir),
                    "--skip-lsof",
                    "--output",
                    str(out_json),
                ]
            )

        self.assertEqual(rc, 1)
        result = json.loads(out_json.read_text(encoding="utf-8"))
        self.assertEqual(result["staged_count"], 2)
        self.assertEqual(result["failed_count"], 1)
        self.assertEqual([s["id"] for s in result["staged_items"]], ["ok-1", "ok-2"])
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(result["failed_items"][0]["id"], "perm-fail")
        self.assertIn("Operation not permitted", result["failed_items"][0]["reason"])

        # Failed path stays untouched; succeeded paths were moved and persisted in batch manifest + restore.sh
        self.assertTrue((dir_fail / "data.bin").is_file())
        self.assertFalse(dir_ok1.exists())
        self.assertFalse(dir_ok2.exists())

        batch_manifest = Path(result["batch_dir"]) / "manifest.json"
        restore_sh = Path(result["restore_script"])
        self.assertTrue(batch_manifest.is_file())
        self.assertTrue(restore_sh.is_file())
        self.assertTrue(bool(stat.S_IMODE(restore_sh.stat().st_mode) & stat.S_IXUSR))

        subprocess.run(["bash", str(restore_sh)], check=True)
        self.assertTrue((dir_ok1 / "data.bin").is_file())
        self.assertTrue((dir_ok2 / "data.bin").is_file())

        # Even if an unexpected non-OSError exception aborts mid-batch after 1 item moved, finally writes ledger
        self._age_tree(dir_ok1, old_ts)
        self._age_tree(dir_ok2, old_ts)
        q_dir_crash = self.root / "quarantine_crash"

        def crash_on_second(src: str, dst: str, *args: object, **kwargs: object) -> object:
            if Path(src).resolve(strict=False) == dir_ok2.resolve(strict=False):
                raise RuntimeError("Simulated abrupt crash after first move")
            return real_move(src, dst, *args, **kwargs)

        with mock.patch.object(full_stack_tool.shutil, "move", side_effect=crash_on_second):
            with self.assertRaises(RuntimeError):
                full_stack_tool.main(
                    [
                        "quarantine",
                        "--config",
                        str(cfg_path),
                        "--targets",
                        str(targets_path),
                        "--ids",
                        "ok-1,ok-2",
                        "--quarantine-dir",
                        str(q_dir_crash),
                        "--skip-lsof",
                    ]
                )

        batches = list(q_dir_crash.iterdir())
        self.assertEqual(len(batches), 1)
        crash_manifest = json.loads((batches[0] / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(crash_manifest["staged_count"], 1)
        self.assertTrue((batches[0] / "restore.sh").is_file())

    def test_restore_script_handles_empty_dir_and_refuses_nonempty_collision(self) -> None:
        old_ts = time.time() - 7200
        d_normal = self.root / "restore_normal"
        d_empty = self.root / "restore_empty_regen"
        d_nonempty = self.root / "restore_nonempty_regen"
        for d in (d_normal, d_empty, d_nonempty):
            d.mkdir(parents=True)
            (d / "payload.txt").write_text(f"original-{d.name}", encoding="utf-8")
            self._age_tree(d, old_ts)

        cfg_path = self._minimal_config()
        targets_path = self.root / "targets_restore.json"
        targets_path.write_text(
            json.dumps(
                {
                    "candidates": [
                        {"id": "c-normal", "category": "build_artifact", "path": str(d_normal)},
                        {"id": "c-empty", "category": "build_artifact", "path": str(d_empty)},
                        {"id": "c-nonempty", "category": "build_artifact", "path": str(d_nonempty)},
                    ]
                }
            ),
            encoding="utf-8",
        )
        q_dir = self.root / "q_restore"
        out_json = self.root / "q_restore_out.json"
        rc = full_stack_tool.main(
            [
                "quarantine",
                "--config",
                str(cfg_path),
                "--targets",
                str(targets_path),
                "--ids",
                "c-normal,c-empty,c-nonempty",
                "--quarantine-dir",
                str(q_dir),
                "--skip-lsof",
                "--output",
                str(out_json),
            ]
        )
        self.assertEqual(rc, 0)
        manifest = json.loads(out_json.read_text(encoding="utf-8"))
        restore_sh = Path(manifest["restore_script"])

        # Simulate an app recreating an empty directory at d_empty, and a non-empty directory at d_nonempty
        d_empty.mkdir(parents=True)
        d_nonempty.mkdir(parents=True)
        (d_nonempty / "fresh_user_file.txt").write_text("keep-me", encoding="utf-8")

        proc = subprocess.run(
            ["bash", str(restore_sh)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("non-empty", proc.stderr)

        # Normal and empty-regenerated targets are restored cleanly in place (not nested!)
        self.assertEqual((d_normal / "payload.txt").read_text(encoding="utf-8"), "original-restore_normal")
        self.assertEqual((d_empty / "payload.txt").read_text(encoding="utf-8"), "original-restore_empty_regen")
        self.assertFalse((d_empty / d_empty.name).exists())

        # Non-empty target was NOT nested into <orig>/<basename>, and quarantined copy remains intact
        self.assertEqual((d_nonempty / "fresh_user_file.txt").read_text(encoding="utf-8"), "keep-me")
        self.assertFalse((d_nonempty / d_nonempty.name).exists())
        nonempty_qpath = next(
            Path(s["quarantine_path"]) for s in manifest["staged_items"] if s["id"] == "c-nonempty"
        )
        self.assertTrue((nonempty_qpath / "payload.txt").is_file())

    def test_batch_id_avoids_same_second_collision(self) -> None:
        q_root = self.root / "batches"
        q_root.mkdir()
        fixed_dt = datetime(2026, 11, 15, 3, 0, 0, 42000, tzinfo=timezone.utc)
        id1 = full_stack_tool.generate_unique_batch_id(q_root, now=fixed_dt)
        (q_root / id1).mkdir()
        id2 = full_stack_tool.generate_unique_batch_id(q_root, now=fixed_dt)
        (q_root / id2).mkdir()
        id3 = full_stack_tool.generate_unique_batch_id(q_root, now=fixed_dt)
        self.assertNotEqual(id1, id2)
        self.assertNotEqual(id2, id3)
        self.assertTrue(id1.startswith("20261115-030000Z-042000"))
        self.assertEqual(id2, f"{id1}-2")
        self.assertEqual(id3, f"{id1}-3")

    # ------------------------------------------------------------------
    # 2. App association precision & orphan vendor_tokens stopword filter
    # ------------------------------------------------------------------
    def test_app_matching_avoids_substring_damage_and_detects_generic_word_orphans(self) -> None:
        now_ts = time.time()
        old_ts = now_ts - (180 * 86400)
        apps_dir = self.root / "Applications"
        lib_support = self.root / "Library" / "Application Support"
        lib_caches = self.root / "Library" / "Caches"
        lib_groups = self.root / "Library" / "Group Containers"

        def create_app(stem: str, bid: str, display_name: str) -> Path:
            app_p = apps_dir / f"{stem}.app"
            self._write_plist(
                app_p / "Contents" / "Info.plist",
                {"CFBundleIdentifier": bid, "CFBundleName": display_name},
            )
            (app_p / "Contents" / "MacOS").mkdir(parents=True, exist_ok=True)
            (app_p / "Contents" / "MacOS" / stem).write_bytes(b"a" * (2 * 1024 * 1024))
            self._age_tree(app_p, old_ts)
            return app_p

        create_app("Craft", "com.lukilabs.lukiapp", "Craft")
        create_app("Focus", "co.bradjasper.focus", "Focus")
        create_app("StudioProManager", "com.acme.StudioProManager", "Studio Pro Manager")

        def make_lib_dir(parent: Path, name: str) -> Path:
            p = parent / name
            p.mkdir(parents=True, exist_ok=True)
            (p / "blob.dat").write_bytes(b"x" * (1200 * 1024))
            self._age_tree(p, old_ts)
            return p

        # True Craft directories (exact bid, group.<bid>, group.<bid>.*, team.<bid>, standalone word)
        craft_bid_dir = make_lib_dir(lib_support, "com.lukilabs.lukiapp")
        craft_grp_dir = make_lib_dir(lib_groups, "group.com.lukilabs.lukiapp")
        craft_grp_sub = make_lib_dir(lib_groups, "group.com.lukilabs.lukiapp.shared")
        craft_team_dir = make_lib_dir(lib_groups, "TEAM99.com.lukilabs.lukiapp")
        craft_cache_dir = make_lib_dir(lib_caches, "Craft")

        # Unrelated user data that MUST NOT be claimed by Craft.app or Focus.app, and MUST surface as orphans
        minecraft_dir = make_lib_dir(lib_support, "minecraft")
        omnifocus_dir = make_lib_dir(lib_support, "com.omnigroup.OmniFocus3")
        # Orphan sharing only generic stopwords ("studio", "pro", "cloud", "helper", "pdf", "viewer") with installed apps
        generic_orphan_1 = make_lib_dir(lib_support, "com.deadvendor.CloudStudioHelper")
        generic_orphan_2 = make_lib_dir(lib_support, "PDF Viewer Pro")

        cfg_path = self._minimal_config(
            app_roots=[str(apps_dir)],
            library_roots=[str(lib_support), str(lib_caches), str(lib_groups)],
        )
        snap_path = self.root / "snap_apps.json"
        self.assertEqual(
            full_stack_tool.main(["scan-local", "--config", str(cfg_path), "--output", str(snap_path)]),
            0,
        )
        snap = json.loads(snap_path.read_text(encoding="utf-8"))
        by_id = {c["id"]: c for c in snap["candidates"]}

        # Craft uninstall candidate must include all 5 legitimate Craft dirs, never Minecraft or OmniFocus3
        craft_cand = by_id["app-uninstall-com-lukilabs-lukiapp"]
        purge_paths = set(craft_cand["uninstall_modes"]["purge_all"]["paths"])
        for expected in (craft_bid_dir, craft_grp_dir, craft_grp_sub, craft_team_dir, craft_cache_dir):
            self.assertIn(str(expected.resolve()), purge_paths)
        self.assertNotIn(str(minecraft_dir.resolve()), purge_paths)
        self.assertNotIn(str(omnifocus_dir.resolve()), purge_paths)

        focus_cand = by_id["app-uninstall-co-bradjasper-focus"]
        self.assertNotIn(str(omnifocus_dir.resolve()), set(focus_cand["uninstall_modes"]["purge_all"]["paths"]))

        # Orphans must NOT be masked by substring ("craft" in "minecraft") or generic vendor stopwords ("studio", "pro")
        orphan_paths = {
            p
            for c in snap["candidates"]
            if c["category"] == "app_orphan"
            for p in ([c["path"]] + c.get("paths", []))
        }
        self.assertIn(str(minecraft_dir.resolve()), orphan_paths)
        self.assertIn(str(omnifocus_dir.resolve()), orphan_paths)
        self.assertIn(str(generic_orphan_1.resolve()), orphan_paths)
        self.assertIn(str(generic_orphan_2.resolve()), orphan_paths)

        # Executing purge-all on Craft.app must leave Minecraft and OmniFocus3 completely intact
        q_dir = self.root / "q_craft"
        q_out = self.root / "q_craft.json"
        rc_q = full_stack_tool.main(
            [
                "quarantine",
                "--config",
                str(cfg_path),
                "--targets",
                str(snap_path),
                "--ids",
                "app-uninstall-com-lukilabs-lukiapp:purge-all",
                "--quarantine-dir",
                str(q_dir),
                "--skip-lsof",
                "--output",
                str(q_out),
            ]
        )
        self.assertEqual(rc_q, 0)
        self.assertTrue((minecraft_dir / "blob.dat").is_file())
        self.assertTrue((omnifocus_dir / "blob.dat").is_file())
        self.assertFalse(craft_bid_dir.exists())
        self.assertFalse(craft_grp_sub.exists())

    # ------------------------------------------------------------------
    # 3. Whitelist repos & guest_ids enforcement across scan / preflight / quarantine
    # ------------------------------------------------------------------
    def test_whitelist_repos_and_guest_ids_skip_in_scan_preflight_and_quarantine(self) -> None:
        old_ts = time.time() - 7200
        ws_dir = self.root / "workspaces"

        def make_repo(name: str, remote_url: str | None = None) -> tuple[Path, Path]:
            repo = ws_dir / name
            repo.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
            exclude = repo / ".git" / "info" / "exclude"
            exclude.parent.mkdir(parents=True, exist_ok=True)
            exclude.write_text("target/\n", encoding="utf-8")
            pack = repo / ".git" / "objects" / "pack"
            pack.mkdir(parents=True, exist_ok=True)
            (pack / "pack-a.pack").write_bytes(b"p" * (1300 * 1024))
            if remote_url:
                with (repo / ".git" / "config").open("a", encoding="utf-8") as cfg_fp:
                    cfg_fp.write(f'[remote "origin"]\n\turl = {remote_url}\n')
            target = repo / "target"
            target.mkdir()
            (target / "bin.o").write_bytes(b"o" * (1300 * 1024))
            self._age_tree(repo, old_ts)
            return repo, target

        _, wl_remote_target = make_repo("service-checkout", "git@github.com:AcmeOrg/Pinned-Repo.git")
        _, wl_plain_target = make_repo("local-secret-repo", None)
        _, non_wl_other_owner_target = make_repo("other-checkout", "https://github.com/OtherOrg/Pinned-Repo.git")

        cfg_path = self._minimal_config(
            workspace_roots=[str(ws_dir)],
            probe_fixtures={
                "github_repos": [
                    "acmeorg/pinned-repo",
                    "otherorg/pinned-repo",
                ],
                "homelab_guests": [
                    {"id": "pve-vm-101", "guest_id": 101, "path_or_spec": "pve:qemu/101"},
                    {"id": "pve-ct-202", "path_or_spec": "pve:lxc/202"},
                    {"id": "docker-core", "name": "core-gateway", "path_or_spec": "docker:core-gateway"},
                    {"id": "pve-vm-1010", "guest_id": 1010, "path_or_spec": "pve:qemu/1010"},
                ],
            },
            whitelist={
                "paths": [],
                "repos": ["AcmeOrg/Pinned-Repo", "local-secret-repo"],
                "app_names": [],
                "guest_ids": [101, "202", "Core-Gateway"],
                "notes": {},
            },
        )

        snap_path = self.root / "snap_wl.json"
        self.assertEqual(
            full_stack_tool.main(["scan-local", "--config", str(cfg_path), "--output", str(snap_path)]),
            0,
        )
        snap = json.loads(snap_path.read_text(encoding="utf-8"))
        by_id = {c["id"]: c for c in snap["candidates"]}

        # Whitelisted local & GitHub repos and remote guests must have whitelisted=True, default_checked=False
        for wid in (
            "git-pack-service-checkout",
            "build-service-checkout-target",
            "git-pack-local-secret-repo",
            "build-local-secret-repo-target",
            "gh-repo-acmeorg-pinned-repo",
            "pve-vm-101",
            "pve-ct-202",
            "docker-core",
        ):
            self.assertTrue(by_id[wid]["whitelisted"], wid)
            self.assertFalse(by_id[wid]["default_checked"], wid)

        # Same repo name under a different GitHub owner or guest 1010 must NOT be whitelisted
        self.assertFalse(by_id["git-pack-other-checkout"]["whitelisted"])
        self.assertFalse(by_id["build-other-checkout-target"]["whitelisted"])
        self.assertFalse(by_id["gh-repo-otherorg-pinned-repo"]["whitelisted"])
        self.assertFalse(by_id["pve-vm-1010"]["whitelisted"])

        # Strip pre-computed "whitelisted" flags to verify real-time wl_index check inside evaluate_single_target
        stripped_targets = self.root / "stripped_targets.json"
        stripped_cands = []
        for c in snap["candidates"]:
            copy_c = dict(c)
            copy_c.pop("whitelisted", None)
            copy_c.pop("whitelist_note", None)
            stripped_cands.append(copy_c)
        stripped_targets.write_text(json.dumps({"candidates": stripped_cands}), encoding="utf-8")

        pre_out = self.root / "pre_wl.json"
        self.assertEqual(
            full_stack_tool.main(
                [
                    "preflight",
                    "--config",
                    str(cfg_path),
                    "--targets",
                    str(stripped_targets),
                    "--skip-lsof",
                    "--output",
                    str(pre_out),
                ]
            ),
            0,
        )
        pre_by_id = {r["id"]: r for r in json.loads(pre_out.read_text(encoding="utf-8"))["results"]}

        # Remote specs MUST hit SKIPPED_WHITELIST BEFORE is_remote_or_non_fs_spec returns SAFE_TO_EXECUTE
        self.assertEqual(pre_by_id["gh-repo-acmeorg-pinned-repo"]["status"], "SKIPPED_WHITELIST")
        self.assertEqual(pre_by_id["pve-vm-101"]["status"], "SKIPPED_WHITELIST")
        self.assertEqual(pre_by_id["pve-ct-202"]["status"], "SKIPPED_WHITELIST")
        self.assertEqual(pre_by_id["docker-core"]["status"], "SKIPPED_WHITELIST")
        self.assertEqual(pre_by_id["build-service-checkout-target"]["status"], "SKIPPED_WHITELIST")
        self.assertEqual(pre_by_id["build-local-secret-repo-target"]["status"], "SKIPPED_WHITELIST")

        # Non-whitelisted remote/local items still return SAFE_TO_EXECUTE
        self.assertEqual(pre_by_id["gh-repo-otherorg-pinned-repo"]["status"], "SAFE_TO_EXECUTE")
        self.assertEqual(pre_by_id["pve-vm-1010"]["status"], "SAFE_TO_EXECUTE")
        self.assertEqual(pre_by_id["build-other-checkout-target"]["status"], "SAFE_TO_EXECUTE")

        # Quarantine must skip whitelisted repo build artifacts and only stage non-whitelisted ones
        q_out = self.root / "q_wl.json"
        self.assertEqual(
            full_stack_tool.main(
                [
                    "quarantine",
                    "--config",
                    str(cfg_path),
                    "--targets",
                    str(stripped_targets),
                    "--ids",
                    "build-service-checkout-target,build-local-secret-repo-target,build-other-checkout-target",
                    "--quarantine-dir",
                    str(self.root / "q_wl_store"),
                    "--skip-lsof",
                    "--output",
                    str(q_out),
                ]
            ),
            0,
        )
        q_manifest = json.loads(q_out.read_text(encoding="utf-8"))
        self.assertEqual([s["id"] for s in q_manifest["staged_items"]], ["build-other-checkout-target"])
        skipped_ids = {s["id"]: s["status"] for s in q_manifest["skipped_items"]}
        self.assertEqual(skipped_ids["build-service-checkout-target"], "SKIPPED_WHITELIST")
        self.assertEqual(skipped_ids["build-local-secret-repo-target"], "SKIPPED_WHITELIST")
        self.assertTrue(wl_remote_target.is_dir())
        self.assertTrue(wl_plain_target.is_dir())
        self.assertFalse(non_wl_other_owner_target.exists())

    # ------------------------------------------------------------------
    # 4. Build tiers, project domain, dynamic gauge_id, ID dedup & reclaimable_bytes
    # ------------------------------------------------------------------
    def test_workspace_tiers_domains_dynamic_gauges_id_dedup_and_reclaimable_bytes(self) -> None:
        vol_main = self.root / "vol_main"
        vol_ext = vol_main / "ext_ssd"
        vol_other = self.root / "unmatched_disk"

        ws_main = vol_main / "ws_a"
        ws_ext = vol_ext / "ws_b"
        ws_unmatched = vol_other / "ws_c"

        # Two repos named "shared-svc" in different workspace roots -> test git-pack & build ID deduplication
        for ws, marker in ((ws_main, b"m"), (ws_ext, b"e")):
            repo = ws / "shared-svc"
            pack = repo / ".git" / "objects" / "pack"
            pack.mkdir(parents=True)
            (pack / "p.pack").write_bytes(marker * (1300 * 1024))
            (repo / "AGENTS.md").write_text("- 合入前检查：无\n", encoding="utf-8")

        # Monorepo with two packages both named "web/node_modules", plus *-builds and *-preservation archives
        mono = ws_ext / "monorepo"
        nm_pkg = mono / "packages" / "web" / "node_modules"
        nm_app = mono / "apps" / "web" / "node_modules"
        rel_builds = mono / "signed-builds"
        qa_pres = mono / "audit-preservation"
        unmatched_target = ws_unmatched / "solo" / "target"

        for d in (nm_pkg, nm_app, rel_builds, qa_pres, unmatched_target):
            d.mkdir(parents=True)
            (d / "artifact.bin").write_bytes(b"x" * (1300 * 1024))

        backup_script = self.root / "backup.sh"
        backup_script.write_text("# exclude node_modules\n", encoding="utf-8")

        cfg_path = self._minimal_config(
            workspace_roots=[str(ws_main), str(ws_ext), str(ws_unmatched)],
            storage_gauges=[
                {"id": "pool-main", "label": "Main Pool", "mount_path": str(vol_main), "warn_used_pct": 85.0},
                {"id": "pool-ext", "label": "External Fast SSD", "mount_path": str(vol_ext), "warn_used_pct": 85.0},
            ],
            backup_pipelines=[
                {
                    "id": "nas-bkp",
                    "label": "NAS Backup",
                    "script_path": str(backup_script),
                    "watched_roots": [str(ws_ext)],
                    "expected_exclude_globs": ["node_modules"],
                }
            ],
        )

        snap_path = self.root / "snap_ws.json"
        self.assertEqual(
            full_stack_tool.main(["scan-local", "--config", str(cfg_path), "--output", str(snap_path)]),
            0,
        )
        snap = json.loads(snap_path.read_text(encoding="utf-8"))
        cands = snap["candidates"]
        ids = [c["id"] for c in cands]
        self.assertEqual(len(ids), len(set(ids)), "All candidate IDs in a snapshot must be unique")

        by_path_cat = {(c["category"], Path(c["path"]).resolve()): c for c in cands}

        # *-builds and *-preservation must be tier C, default_checked=False, domain="projects"
        for archive_dir in (rel_builds, qa_pres):
            c_arch = by_path_cat[("build_artifact", archive_dir.resolve())]
            self.assertEqual(c_arch["tier"], "C")
            self.assertFalse(c_arch["default_checked"])
            self.assertEqual(c_arch["domain"], "projects")
            self.assertEqual(c_arch["gauge_id"], "pool-ext")

        # Standard build dirs remain tier A, default_checked=True, domain="projects"
        c_nm_pkg = by_path_cat[("build_artifact", nm_pkg.resolve())]
        c_nm_app = by_path_cat[("build_artifact", nm_app.resolve())]
        self.assertEqual(c_nm_pkg["tier"], "A")
        self.assertTrue(c_nm_pkg["default_checked"])
        self.assertEqual(c_nm_pkg["domain"], "projects")
        self.assertEqual(c_nm_pkg["gauge_id"], "pool-ext")

        # First colliding ID stays readable; second appends 6-char SHA-1 of canonical path
        nm_ids = {c_nm_pkg["id"], c_nm_app["id"]}
        self.assertIn("build-web-node-modules", nm_ids)
        other_nm = c_nm_app if c_nm_pkg["id"] == "build-web-node-modules" else c_nm_pkg
        expected_hash = hashlib.sha1(str(Path(other_nm["path"]).resolve()).encode("utf-8")).hexdigest()[:6]
        self.assertEqual(other_nm["id"], f"build-web-node-modules-{expected_hash}")

        # Longest mount_path prefix match vs fallback "mac-data"
        c_pack_main = by_path_cat[("git_pack_bloat", (ws_main / "shared-svc" / ".git" / "objects" / "pack").resolve())]
        c_pack_ext = by_path_cat[("git_pack_bloat", (ws_ext / "shared-svc" / ".git" / "objects" / "pack").resolve())]
        c_unmatched = by_path_cat[("build_artifact", unmatched_target.resolve())]
        self.assertEqual(c_pack_main["gauge_id"], "pool-main")
        self.assertEqual(c_pack_ext["gauge_id"], "pool-ext")
        self.assertEqual(c_unmatched["gauge_id"], "mac-data")

        # Non-cleanup space semantics: git_pack_bloat & backup_exclusion_drift report reclaimable_bytes=0 + action_type="optimize"
        self.assertEqual(c_pack_main["action_type"], "optimize")
        self.assertEqual(c_pack_main["reclaimable_bytes"], 0)
        self.assertGreater(c_pack_main["size_bytes"], 0)

        c_drift = by_path_cat[("backup_exclusion_drift", rel_builds.resolve())]
        self.assertEqual(c_drift["domain"], "projects")
        self.assertEqual(c_drift["action_type"], "optimize")
        self.assertEqual(c_drift["reclaimable_bytes"], 0)
        self.assertGreater(c_drift["size_bytes"], 0)
        self.assertEqual(c_drift["gauge_id"], "pool-ext")

    # ------------------------------------------------------------------
    # 5. Multi-AI client session & Orca/Git worktree read-only detection
    # ------------------------------------------------------------------
    def test_multi_ai_client_sessions_and_orca_worktrees_block_in_preflight(self) -> None:
        old_ts = time.time() - 10800  # 3 hours ago
        fake_home = self.root / "fake_ai_home"

        def make_clean_idle_repo(rel: str) -> tuple[Path, Path]:
            repo = self.root / rel
            target = repo / "target"
            target.mkdir(parents=True)
            subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
            exclude = repo / ".git" / "info" / "exclude"
            exclude.parent.mkdir(parents=True, exist_ok=True)
            exclude.write_text("target/\n", encoding="utf-8")
            (target / "out.rlib").write_bytes(b"lib")
            self._age_tree(repo, old_ts)
            return repo, target

        repo_codex, target_codex = make_clean_idle_repo("projects/codex_repo")
        _, target_codex_sibling = make_clean_idle_repo("projects/codex_repo-v2")
        repo_claude, target_claude = make_clean_idle_repo("projects/claude_repo")
        repo_agy_cli, target_agy_cli = make_clean_idle_repo("projects/agy_cli_repo")
        repo_agy_app, target_agy_app = make_clean_idle_repo("projects/agy_app_repo")

        # 1. Codex session log referencing repo_codex (must NOT falsely block codex_repo-v2 or vice versa)
        codex_log = fake_home / ".codex" / "sessions" / "2026" / "11" / "rollout-codex.jsonl"
        codex_log.parent.mkdir(parents=True)
        codex_log.write_text(json.dumps({"cwd": str(repo_codex.resolve())}) + "\n", encoding="utf-8")

        # 2. Claude Code session log under ~/.claude/projects/<slug>/session.jsonl
        claude_slug = full_stack_tool._claude_project_slug(repo_claude.resolve())
        claude_log = fake_home / ".claude" / "projects" / f"-{claude_slug}" / "sess-1.jsonl"
        claude_log.parent.mkdir(parents=True)
        claude_log.write_text('{"type":"user","message":"checking repo"}\n', encoding="utf-8")

        # 3. Antigravity logs under both ~/.gemini/antigravity-cli/brain and ~/.gemini/antigravity/brain
        agy_cli_log = (
            fake_home / ".gemini" / "antigravity-cli" / "brain" / "conv-1" / ".system_generated" / "logs" / "transcript.jsonl"
        )
        agy_cli_log.parent.mkdir(parents=True)
        agy_cli_log.write_text(json.dumps({"workspace": str(repo_agy_cli.resolve())}) + "\n", encoding="utf-8")

        agy_app_log = fake_home / ".gemini" / "antigravity" / "brain" / "conv-2" / "activity.log"
        agy_app_log.parent.mkdir(parents=True)
        agy_app_log.write_text(f"opened {repo_agy_app.resolve()}/src/main.py\n", encoding="utf-8")

        # 4. Active Orca workspace worktree (orca/workspaces/<proj>/<wt>) with recent file activity
        orca_wt = self.root / "orca" / "workspaces" / "demo-skill" / "feature-branch"
        orca_target = orca_wt / "dist"
        orca_target.mkdir(parents=True)
        (orca_target / "bundle.js").write_text("old", encoding="utf-8")
        self._age_tree(orca_wt, old_ts)
        # Touch a sibling file inside the Orca worktree recently
        (orca_wt / "notes.txt").write_text("active session edit", encoding="utf-8")

        # 5. Linked Git worktree (.git file pointing to main repo .git/worktrees/...)
        main_repo, _ = make_clean_idle_repo("projects/main_repo_for_wt")
        (main_repo / "README.md").write_text("init\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(main_repo), "add", "README.md"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(main_repo),
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-q",
                "-m",
                "chore: init",
            ],
            check=True,
        )
        linked_wt = self.root / "linked_wt"
        subprocess.run(
            ["git", "-C", str(main_repo), "worktree", "add", "-q", "-b", "test/wt", str(linked_wt)],
            check=True,
        )
        linked_target = linked_wt / "target"
        linked_target.mkdir()
        (linked_target / "old.o").write_bytes(b"o")
        self._age_tree(main_repo, old_ts)
        self._age_tree(linked_wt, old_ts)
        # Touch git index/HEAD of linked worktree recently while keeping working tree clean
        gitdir_line = (linked_wt / ".git").read_text(encoding="utf-8").strip()
        wt_gitdir = (linked_wt / gitdir_line.split(":", 1)[1].strip()).resolve()
        now_ts = time.time()
        os.utime(wt_gitdir / "HEAD", (now_ts, now_ts))

        cfg_path = self._minimal_config()
        targets_path = self.root / "targets_sessions.json"
        targets_path.write_text(
            json.dumps(
                {
                    "candidates": [
                        {"id": "c-codex", "path": str(target_codex)},
                        {"id": "c-codex-v2-idle", "path": str(target_codex_sibling)},
                        {"id": "c-claude", "path": str(target_claude)},
                        {"id": "c-agy-cli", "path": str(target_agy_cli)},
                        {"id": "c-agy-app", "path": str(target_agy_app)},
                        {"id": "c-orca-wt", "path": str(orca_target)},
                        {"id": "c-linked-wt", "path": str(linked_target)},
                    ]
                }
            ),
            encoding="utf-8",
        )

        pre_out = self.root / "pre_sessions.json"
        # Inject isolated session home via env var; never touches real host ~
        with mock.patch.dict(os.environ, {"FSM_SESSION_HOME": str(fake_home)}):
            rc = full_stack_tool.main(
                [
                    "preflight",
                    "--config",
                    str(cfg_path),
                    "--targets",
                    str(targets_path),
                    "--skip-lsof",
                    "--output",
                    str(pre_out),
                ]
            )
        self.assertEqual(rc, 0)
        by_id = {r["id"]: r for r in json.loads(pre_out.read_text(encoding="utf-8"))["results"]}

        self.assertEqual(by_id["c-codex"]["status"], "BLOCKED_ACTIVE_SESSION")
        self.assertIn("Codex", by_id["c-codex"]["reason"])

        # Prefix boundary check: codex_repo must not block codex_repo-v2 when reversed; here codex_repo-v2 is idle
        self.assertEqual(by_id["c-codex-v2-idle"]["status"], "SAFE_TO_EXECUTE")

        self.assertEqual(by_id["c-claude"]["status"], "BLOCKED_ACTIVE_SESSION")
        self.assertIn("Claude Code", by_id["c-claude"]["reason"])

        self.assertEqual(by_id["c-agy-cli"]["status"], "BLOCKED_ACTIVE_SESSION")
        self.assertIn("Antigravity", by_id["c-agy-cli"]["reason"])

        self.assertEqual(by_id["c-agy-app"]["status"], "BLOCKED_ACTIVE_SESSION")
        self.assertIn("Antigravity", by_id["c-agy-app"]["reason"])

        self.assertEqual(by_id["c-orca-wt"]["status"], "BLOCKED_ACTIVE_SESSION")
        self.assertIn("Orca", by_id["c-orca-wt"]["reason"])

        self.assertEqual(by_id["c-linked-wt"]["status"], "BLOCKED_ACTIVE_SESSION")
        self.assertIn("Git worktree", by_id["c-linked-wt"]["reason"])

        # Also verify direct parameter injection (session_roots=...) prevents false prefix match when codex_repo-v2 is active
        codex_log.write_text(json.dumps({"cwd": str((self.root / "projects/codex_repo-v2").resolve())}) + "\n", encoding="utf-8")
        direct_res = full_stack_tool.evaluate_single_target(
            str(target_codex),
            {"id": "c-codex", "path": str(target_codex)},
            full_stack_tool.build_whitelist_index({"whitelist": {}}),
            cutoff_ts=now_ts - 3600,
            recent_minutes=60.0,
            skip_lsof=True,
            session_roots={"codex": [fake_home / ".codex" / "sessions"]},
        )
        self.assertEqual(direct_res["status"], "SAFE_TO_EXECUTE")

    # ------------------------------------------------------------------
    # 6. Per-item uninstall mode overrides (<id>:keep-data / <id>:purge-all)
    # ------------------------------------------------------------------
    def test_per_item_uninstall_mode_overrides_and_fail_closed_suffixes(self) -> None:
        old_ts = time.time() - (200 * 86400)
        apps_dir = self.root / "Applications"
        lib_support = self.root / "Library" / "Application Support"
        lib_caches = self.root / "Library" / "Caches"

        def build_unused_app(name: str, bid: str) -> tuple[Path, Path, Path]:
            app = apps_dir / f"{name}.app"
            self._write_plist(app / "Contents" / "Info.plist", {"CFBundleIdentifier": bid, "CFBundleName": name})
            (app / "Contents" / "MacOS").mkdir(parents=True, exist_ok=True)
            (app / "Contents" / "MacOS" / name).write_bytes(b"b" * (1500 * 1024))
            data = lib_support / bid
            data.mkdir(parents=True)
            (data / "userdata.sqlite").write_bytes(b"d" * (800 * 1024))
            cache = lib_caches / bid
            cache.mkdir(parents=True)
            (cache / "cache.dat").write_bytes(b"c" * (800 * 1024))
            for p in (app, data, cache):
                self._age_tree(p, old_ts)
            return app, data, cache

        alpha_app, alpha_data, alpha_cache = build_unused_app("AlphaApp", "com.example.AlphaApp")
        beta_app, beta_data, beta_cache = build_unused_app("BetaApp", "com.example.BetaApp")

        cfg_path = self._minimal_config(
            app_roots=[str(apps_dir)],
            library_roots=[str(lib_support), str(lib_caches)],
        )
        snap_path = self.root / "snap_modes.json"
        self.assertEqual(
            full_stack_tool.main(["scan-local", "--config", str(cfg_path), "--output", str(snap_path)]),
            0,
        )

        # Invalid mode suffixes, empty suffixes, missing IDs, or conflicting duplicate modes must fail-closed (rc == 2)
        for bad_ids in (
            "app-uninstall-com-example-alphaapp:wipe-everything",
            "app-uninstall-com-example-alphaapp:",
            ":keep-data",
            "app-uninstall-com-example-alphaapp:keep-data,app-uninstall-com-example-alphaapp:purge_all",
        ):
            with mock.patch("sys.stderr", new_callable=io.StringIO):
                rc_bad_pre = full_stack_tool.main(
                    [
                        "preflight",
                        "--config",
                        str(cfg_path),
                        "--targets",
                        str(snap_path),
                        "--ids",
                        bad_ids,
                        "--skip-lsof",
                    ]
                )
                rc_bad_q = full_stack_tool.main(
                    [
                        "quarantine",
                        "--config",
                        str(cfg_path),
                        "--targets",
                        str(snap_path),
                        "--ids",
                        bad_ids,
                        "--quarantine-dir",
                        str(self.root / "q_never"),
                        "--skip-lsof",
                    ]
                )
            self.assertEqual(rc_bad_pre, 2, bad_ids)
            self.assertEqual(rc_bad_q, 2, bad_ids)

        # Per-item override: Alpha uses purge-all (overrides global keep-data), Beta uses keep_data
        ids_arg = "app-uninstall-com-example-alphaapp:purge-all,app-uninstall-com-example-betaapp:keep_data"
        pre_out = self.root / "pre_modes.json"
        self.assertEqual(
            full_stack_tool.main(
                [
                    "preflight",
                    "--config",
                    str(cfg_path),
                    "--targets",
                    str(snap_path),
                    "--ids",
                    ids_arg,
                    "--uninstall-mode",
                    "keep-data",
                    "--skip-lsof",
                    "--output",
                    str(pre_out),
                ]
            ),
            0,
        )
        pre_res = {r["id"]: r for r in json.loads(pre_out.read_text(encoding="utf-8"))["results"]}
        self.assertEqual(pre_res["app-uninstall-com-example-alphaapp"]["uninstall_mode"], "purge_all")
        self.assertIn(str(alpha_data.resolve()), pre_res["app-uninstall-com-example-alphaapp"]["safe_paths"])
        self.assertEqual(pre_res["app-uninstall-com-example-betaapp"]["uninstall_mode"], "keep_data")
        self.assertNotIn(str(beta_data.resolve()), pre_res["app-uninstall-com-example-betaapp"]["safe_paths"])

        q_out = self.root / "q_modes.json"
        self.assertEqual(
            full_stack_tool.main(
                [
                    "quarantine",
                    "--config",
                    str(cfg_path),
                    "--targets",
                    str(snap_path),
                    "--ids",
                    ids_arg,
                    "--uninstall-mode",
                    "keep-data",
                    "--quarantine-dir",
                    str(self.root / "q_modes_store"),
                    "--skip-lsof",
                    "--output",
                    str(q_out),
                ]
            ),
            0,
        )
        q_manifest = json.loads(q_out.read_text(encoding="utf-8"))
        modes_by_id = {s["id"]: s["uninstall_mode"] for s in q_manifest["staged_items"]}
        self.assertEqual(modes_by_id["app-uninstall-com-example-alphaapp"], "purge_all")
        self.assertEqual(modes_by_id["app-uninstall-com-example-betaapp"], "keep_data")

        # Alpha: app + cache + user data all quarantined; Beta: app + cache quarantined, user data preserved!
        self.assertFalse(alpha_app.exists())
        self.assertFalse(alpha_cache.exists())
        self.assertFalse(alpha_data.exists())
        self.assertFalse(beta_app.exists())
        self.assertFalse(beta_cache.exists())
        self.assertTrue((beta_data / "userdata.sqlite").is_file())

    def test_symlink_cache_root_cross_vendor_matching_and_boundary_guards(self) -> None:
        # 1. Symlinked global cache root (e.g. ~/.cache/uv -> external SSD) must NEVER be penetrated
        real_ext_cache = self.root / "external_ssd" / "uv_shared_cache"
        real_ext_cache.mkdir(parents=True)
        (real_ext_cache / "wheels.bin").write_bytes(b"w" * (1500 * 1024))
        symlink_cache = self.root / "home_cache_uv_link"
        symlink_cache.symlink_to(real_ext_cache)

        cfg_path = self._minimal_config(global_cache_roots=[str(symlink_cache)])
        snap_path = self.root / "snap_sym.json"
        self.assertEqual(
            full_stack_tool.main(["scan-local", "--config", str(cfg_path), "--output", str(snap_path)]),
            0,
        )
        snap = json.loads(snap_path.read_text(encoding="utf-8"))
        self.assertEqual(snap["candidates"], [])

        # 2. Cross-vendor Group Containers, short vendor IDs, and sibling apps must NOT match
        focus_brad = {
            "name": "Focus",
            "stem": "Focus",
            "bundle_id": "co.bradjasper.focus",
            "bundle_id_lower": "co.bradjasper.focus",
            "clean_name_tuples": [("focus",)],
        }
        focus_short = {
            "name": "Focus",
            "stem": "Focus",
            "bundle_id": "io.ab.focus",
            "bundle_id_lower": "io.ab.focus",
            "clean_name_tuples": [("focus",)],
        }
        notion_app = {
            "name": "Notion",
            "stem": "Notion",
            "bundle_id": "notion.id",
            "bundle_id_lower": "notion.id",
            "clean_name_tuples": [("notion",)],
        }
        self.assertFalse(full_stack_tool.match_library_entry_to_app("TEAM99.com.omnigroup.Focus", focus_brad))
        self.assertFalse(full_stack_tool.match_library_entry_to_app("com.qq.Focus", focus_brad))
        self.assertFalse(full_stack_tool.match_library_entry_to_app("app.studio.Focus", focus_brad))
        self.assertFalse(full_stack_tool.match_library_entry_to_app("org.mozilla.Focus", focus_short))
        self.assertFalse(full_stack_tool.match_library_entry_to_app("Notion Calendar", notion_app))
        self.assertTrue(full_stack_tool.match_library_entry_to_app("Notion Helper", notion_app))

        # 3. Localized README* governance docs must be refused by quarantine
        readme_zh = self.root / "README.zh-CN.md"
        readme_zh.write_text("# Doc\n", encoding="utf-8")
        self.assertTrue(bool(full_stack_tool.quarantine_path_refusal(readme_zh)))

        # 4. Port :101 on IP host must NOT match guest_ids=[101]
        wl_idx = full_stack_tool.build_whitelist_index({"whitelist": {"guest_ids": [101]}})
        is_wl, _ = full_stack_tool.check_guest_whitelisted(
            {"domain": "nas", "category": "homelab_guest"},
            "192.0.2.5:101",
            wl_idx,
        )
        self.assertFalse(is_wl)

        # 5. Fail-closed on boolean version:true, mixed bare+suffixed duplicate IDs, and mode override on non-app item
        bool_ver_cfg = self.root / "bool_ver.json"
        bool_ver_cfg.write_text(json.dumps({"version": True, "whitelist": {}}), encoding="utf-8")
        with mock.patch("sys.stderr", new=io.StringIO()):
            self.assertEqual(full_stack_tool.main(["scan-local", "--config", str(bool_ver_cfg)]), 2)

        non_app_targets = self.root / "non_app.json"
        non_app_targets.write_text(
            json.dumps({"candidates": [{"id": "orphan-1", "category": "app_orphan", "path": str(real_ext_cache)}]}),
            encoding="utf-8",
        )
        with mock.patch("sys.stderr", new=io.StringIO()):
            self.assertEqual(
                full_stack_tool.main(
                    ["preflight", "--config", str(cfg_path), "--targets", str(non_app_targets), "--ids", "orphan-1:keep-data"]
                ),
                2,
            )
            self.assertEqual(
                full_stack_tool.main(
                    ["preflight", "--config", str(cfg_path), "--targets", str(non_app_targets), "--ids", "a,a:keep-data"]
                ),
                2,
            )
            self.assertEqual(
                full_stack_tool.main(
                    ["preflight", "--config", str(cfg_path), "--targets", str(non_app_targets), "--ids", ""]
                ),
                2,
            )


if __name__ == "__main__":
    unittest.main()
