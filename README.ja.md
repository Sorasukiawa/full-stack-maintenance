# full-stack-maintenance（フルスタック点検＆インタラクティブダッシュボードスキル）

[English](README.md) | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-TW.md) | [日本語](README.ja.md)

[![Tests](https://github.com/Sorasukiawa/full-stack-maintenance/actions/workflows/tests.yml/badge.svg)](https://github.com/Sorasukiawa/full-stack-maintenance/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Codex、Claude Code、Antigravity 向けの統合メンテナンススキルです。**macOS 本体とバックアップパイプライン**、**ローカルの複数リポジトリ・Worktree**、**GitHub リモートリポジトリ**、**NAS / Proxmox VE / Docker ホームラボ**の 4 領域を並列・読み取り専用で点検し、前回スナップショットとの差分比較（`diff`）、**リアルタイム容量シミュレーター付き単一 HTML ダッシュボード**の生成、および実行直前の**並行セッション安全チェック（`preflight`）**を行います。

付属スクリプトは Python 3.10+ 標準ライブラリのみを使用し、ファイルの自動削除やサービスの変更は行いません。詳細な使い方は [English README](README.md) および [簡体字中国語ガイド](README.zh-CN.md) を参照してください。

## テスト

```sh
python3 -B -m unittest discover -v -s tests
```

[MIT License](LICENSE) の下で公開されています。
