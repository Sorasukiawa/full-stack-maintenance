# full-stack-maintenance（全棧多域巡檢與互動看板技能）

[English](README.md) | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-TW.md) | [日本語](README.ja.md)

[![Tests](https://github.com/Sorasukiawa/full-stack-maintenance/actions/workflows/tests.yml/badge.svg)](https://github.com/Sorasukiawa/full-stack-maintenance/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

面向 Codex、Claude Code 與 Antigravity 的總管級全棧維護技能：跨 **Mac 本機與備份管線**、**本機多倉庫工作區與 Worktree**、**GitHub 全帳號遠端倉庫** 以及 **NAS / Proxmox VE / Docker 家庭伺服器** 四大領域執行平行唯讀巡檢，自動對比上次巡檢基線，產生帶**即時水位模擬器的單檔互動式 HTML 看板**，並在執行前強制運行**二次並行安全複查（`preflight`）**。

附帶腳本僅使用 Python 3.10+ 標準庫，不會自動刪除檔案、解除安裝軟體或修改系統服務。技能指令與報表標籤目前為簡體中文，CLI 參數與 JSON 欄位為英文。詳見 [簡體中文說明](README.zh-CN.md) 與 [English README](README.md)。

## 驗證與測試

```sh
python3 -B -m unittest discover -v -s tests
```

採用 [MIT License](LICENSE) 授權。
