# full-stack-maintenance

[English](README.md) | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-TW.md) | [日本語](README.ja.md)

[![Tests](https://github.com/Sorasukiawa/full-stack-maintenance/actions/workflows/tests.yml/badge.svg)](https://github.com/Sorasukiawa/full-stack-maintenance/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

An orchestrator skill for Codex and Claude Code that runs parallel read-only audits across **macOS**, **local multi-repository workspaces**, **GitHub repositories**, and **NAS / Proxmox VE / Docker homelabs**, compares findings against previous baseline snapshots, renders a self-contained **interactive HTML waterline simulator dashboard**, and enforces pre-flight concurrency checks before executing approved actions.

The helper scripts use only the Python 3.10+ standard library and never delete files, uninstall apps, or modify services on their own.

**Language scope:** This introduction is available in four languages. Skill instructions, detailed references, and generated dashboard labels are currently in Simplified Chinese. CLI flags and JSON field names use English.

## What it does

| Capability | Output and scope |
|---|---|
| 4-domain parallel audit | Coordinates read-only inspection across Mac storage & backup pipelines, local project workspaces & worktrees, GitHub remote branches/CI/archive candidates, and NAS/Proxmox/Docker infrastructure |
| Orphan & `.git` bloat scan | Cross-references `~/Library/{Application Support,Caches,Containers}` against installed `/Applications` bundles (`Info.plist`), flags bloated `.git/objects/pack` directories, and detects backup exclusion drift |
| Baseline snapshot diff | Compares the current snapshot against a previous baseline (`diff`) to highlight storage pool deltas and the fastest-growing directories ("what grew since the last check") |
| Interactive HTML dashboard | Generates a zero-dependency single-file `.html` board with live storage waterline simulation, A/B/C risk filters, `localStorage` state persistence, per-item adjustment notes, a persistent whitelist toggle, and one-click prompt copy |
| Pre-flight concurrency guard | Verifies `whitelist` rules (fail-closed), symlink boundaries, recent file modifications (`-mmin`), open file handles (`lsof`), and Git worktree cleanliness right before execution to protect concurrent AI coding sessions |
| Cross-layer trap playbooks | Guides post-cleanup physical space reclamation across APFS local snapshots (`tmutil thinlocalsnapshots`), iCloud dataless chunks, Proxmox LXC `pct fstrim`, and NAS SMB `#recycle` |

It is designed to work alongside single-domain skills such as [`mac-maintenance`](https://github.com/Sorasukiawa/mac-maintenance), [`repo-cleanup`](https://github.com/Sorasukiawa/repo-cleanup), and [`codex-maintenance`](https://github.com/Sorasukiawa/codex-maintenance).

## Install

### Codex (`$skill-installer` or manual)

```sh
mkdir -p "$HOME/.agents/skills"
git clone https://github.com/Sorasukiawa/full-stack-maintenance.git "$HOME/.agents/skills/full-stack-maintenance"
```

### Claude Code Plugin or Skills CLI

```sh
npx skills add Sorasukiawa/full-stack-maintenance --skill full-stack-maintenance --agent claude-code --global
```

## Quick start

Copy the example topology configuration to a Git-ignored local file and customize your workspace roots, backup pipelines, storage thresholds, and whitelist:

```sh
cp -n config/topology.example.json config/topology.local.json
```

1. **Run a read-only local scan:**
   ```sh
   mkdir -p work/full-stack
   python3 scripts/full_stack_tool.py scan-local \
     --config config/topology.local.json \
     --output work/full-stack/current-snapshot.json --overwrite
   ```

2. **Compare against a previous baseline (optional):**
   ```sh
   python3 scripts/full_stack_tool.py diff \
     --before work/full-stack/baseline-snapshot.json \
     --after work/full-stack/current-snapshot.json \
     --output work/full-stack/diff.json --overwrite
   ```

3. **Render the interactive HTML waterline dashboard:**
   ```sh
   python3 scripts/render_dashboard.py \
     --input work/full-stack/current-snapshot.json \
     --diff work/full-stack/diff.json \
     --output work/full-stack/inspection-dashboard.html --overwrite
   ```

4. **Run pre-flight concurrency verification before executing checked items:**
   ```sh
   python3 scripts/full_stack_tool.py preflight \
     --config config/topology.local.json \
     --targets work/full-stack/current-snapshot.json \
     --ids "build-example-target" \
     --output work/full-stack/preflight.json --overwrite
   ```

## Validation and tests

```sh
python3 -B -m unittest discover -v -s tests
```

All unit tests run inside isolated temporary fixtures and never scan or modify the host system.

## Design references and license

Design research incorporated ideas from:
- [SomSamantray/mac-storage-doctor](https://github.com/SomSamantray/mac-storage-doctor) for parallel subagent slicing, `~/Library` vs `/Applications` orphan cross-referencing, and `.git` pack bloat checks;
- [jmagar/claude-homelab](https://github.com/jmagar/claude-homelab) and [Sunwood-ai-labs/proxmox-skill](https://github.com/Sunwood-ai-labs/proxmox-skill) for externalized topology configuration and container/Proxmox health checks;
- [tw93/Mole](https://github.com/tw93/Mole) and [fengyiqicoder/CleanMyMac](https://github.com/fengyiqicoder/CleanMyMac) for persistent fail-closed whitelists and post-cleanup physical space reclamation;
- Community interactive approval report patterns for `localStorage` checkbox/note persistence and one-click execution prompt export.

All scripts and documentation in this repository were implemented independently under the [MIT License](LICENSE).
