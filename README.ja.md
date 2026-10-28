<div align="center">

<img src="assets/hero-banner.svg" alt="full-stack-maintenance Hero Banner" width="100%" />

<br/>

[![Tests](https://github.com/Sorasukiawa/full-stack-maintenance/actions/workflows/tests.yml/badge.svg)](https://github.com/Sorasukiawa/full-stack-maintenance/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-38bdf8.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B%20(標準ライブラリのみ)-34d399.svg)](#スクリプトの直接実行)
[![Agent Skills](https://img.shields.io/badge/Agent%20Skills-Claude%20Code%20%7C%20Codex%20%7C%20Antigravity%20%7C%20OpenCode%20%7C%20Cursor-818cf8.svg)](#マルチエージェント対応とインストール)

**[English](README.md) · [简体中文](README.zh-CN.md) · [繁體中文](README.zh-TW.md) · [日本語](README.ja.md)**

</div>

---

**`full-stack-maintenance`** は、**macOS 本体とバックアップパイプライン**、**ローカルの複数リポジトリ・Worktree**、**GitHub リモートリポジトリ**、**NAS / Proxmox VE / Docker ホームラボ**の 4 領域を並列・読み取り専用で点検するマルチエージェント統合メンテナンススキルです。前回スナップショットとの差分比較（`diff`）、**リアルタイム容量シミュレーター付き単一 HTML ダッシュボード**の生成、および実行直前の**並行セッション安全チェック（`preflight`）**を提供します。

<div align="center">
  <img src="assets/dashboard-preview.svg" alt="Interactive HTML Waterline Dashboard Preview" width="100%" />
</div>

---

## 🚀 マルチエージェント対応とインストール

**Claude Code、OpenAI Codex、Google Antigravity、OpenCode、Cursor、Windsurf** に対応しています（詳細は [マルチエージェント適応ガイド](references/multi-agent-hosts.md) を参照）：

```bash
# Claude Code プラグインマーケットプレイス
claude plugin marketplace add Sorasukiawa/full-stack-maintenance
claude plugin install full-stack-maintenance@full-stack-maintenance

# skills CLI（Codex / Claude Code / Antigravity / Cursor / OpenCode）
npx skills add Sorasukiawa/full-stack-maintenance --skill full-stack-maintenance --agent codex --global
npx skills add Sorasukiawa/full-stack-maintenance --skill full-stack-maintenance --agent claude-code --global
```

## 💻 スクリプトの直接実行

```bash
cp -n config/topology.example.json config/topology.local.json
mkdir -p work/full-stack

python3 scripts/full_stack_tool.py scan-local --config config/topology.local.json --output work/full-stack/current-snapshot.json --overwrite
python3 scripts/full_stack_tool.py diff --before work/full-stack/baseline-snapshot.json --after work/full-stack/current-snapshot.json --output work/full-stack/diff.json --overwrite
python3 scripts/render_dashboard.py --input work/full-stack/current-snapshot.json --diff work/full-stack/diff.json --output work/full-stack/inspection-dashboard.html --overwrite
python3 scripts/full_stack_tool.py preflight --config config/topology.local.json --targets work/full-stack/current-snapshot.json --ids "build-example-target" --output work/full-stack/preflight.json --overwrite
```

## 🧪 テストと検証

```bash
bash tests/check-skill.sh
python3 -B -m unittest discover -v -s tests
```

詳細は [English README](README.md) および [簡体字中国語ガイド](README.zh-CN.md) を参照してください。[MIT License](LICENSE) © 2026 [Sorasukiawa](https://github.com/Sorasukiawa).
