# full-stack-maintenance（全栈多域巡检与交互看板技能）

[English](README.md) | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-TW.md) | [日本語](README.ja.md)

[![Tests](https://github.com/Sorasukiawa/full-stack-maintenance/actions/workflows/tests.yml/badge.svg)](https://github.com/Sorasukiawa/full-stack-maintenance/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

面向 Codex、Claude Code 与 Antigravity 的总管级全栈维护技能：跨 **Mac 本机与备份管线**、**本地多仓库工作区与 Worktree**、**GitHub 全账号远端仓库** 以及 **NAS / Proxmox VE / Docker 家庭服务器** 四大域执行并行只读巡检，自动对比上次巡检基线（「这几天是谁偷偷占了空间」），生成带**实时水位模拟器的单文件交互式 HTML 看板**，并在执行前强制运行**二次并发安全复查（`preflight`）**，保护正在并行干活的其他 AI 会话。

附带脚本仅使用 Python 3.10+ 标准库，只负责采集、对比、渲染看板与执行前并发安全校验，绝不自动删除文件、卸载软件或改动服务。

## 核心能力

| 模块 | 作用与范围 |
|---|---|
| **四域并行只读巡检** | 联动 [`mac-maintenance`](https://github.com/Sorasukiawa/mac-maintenance)、[`repo-cleanup`](https://github.com/Sorasukiawa/repo-cleanup) 与 [`codex-maintenance`](https://github.com/Sorasukiawa/codex-maintenance)，一次盘点 Mac、本地项目群、GitHub 远端与 NAS/PVE |
| **孤儿残留与 `.git` 膨胀扫描** | 自动将 `~/Library/{Application Support,Caches,Containers}` 与已安装 `/Applications` 的 `Info.plist` 交叉比对揪出卸载残留；扫描 `.git/objects/pack` 膨胀与云端/NAS 备份漏排目录 |
| **前后基线快照对比（`diff`）** | 对比上次快照与本次快照，直接算出各存储池水位涨跌与体积增长最快的 Top 目录 |
| **交互式 HTML 水位模拟看板** | 零外部依赖单文件 HTML：顶部红线水位实时模拟、A/B/C 三级风险筛选、`localStorage` 刷新不丢勾选、**单项微调备注输入框**、**长期豁免白名单抽屉**与**一键复制执行指令** |
| **动手前二次并发安全复查（`preflight`）** | 执行前逐项复核白名单（Fail-Closed）、符号链接边界、最近 60 分钟内文件变动（`-mmin`）、打开句柄（`lsof`）与 Git 工作区脏状态，自动跳过其他会话正在读写的目录 |
| **五大跨层存储陷阱手册** | 覆盖「备份排除穿透」「APFS 本地快照锁死 (`tmutil thinlocalsnapshots`)」「iCloud 占位分块回下风暴」「PVE LXC `pct fstrim`」与「NAS SMB `#recycle` 二次截留」 |

## 安装与配置

将个人拓扑（工作区路径、备份脚本、NAS/PVE 地址与豁免白名单）存放在 Git 忽略的 `config/topology.local.json` 中：

```sh
cp -n config/topology.example.json config/topology.local.json
```

常用命令：

```sh
mkdir -p work/full-stack
# 1. 本机只读扫描（孤儿残留、.git膨胀、构建目录与备份漏排检测）
python3 scripts/full_stack_tool.py scan-local \
  --config config/topology.local.json \
  --output work/full-stack/current-snapshot.json --overwrite

# 2. 与上次基线对比
python3 scripts/full_stack_tool.py diff \
  --before work/full-stack/baseline-snapshot.json \
  --after work/full-stack/current-snapshot.json \
  --output work/full-stack/diff.json --overwrite

# 3. 生成单文件交互式 HTML 看板
python3 scripts/render_dashboard.py \
  --input work/full-stack/current-snapshot.json \
  --diff work/full-stack/diff.json \
  --output work/full-stack/inspection-dashboard.html --overwrite

# 4. 动手前二次并发安全复查
python3 scripts/full_stack_tool.py preflight \
  --config config/topology.local.json \
  --targets work/full-stack/current-snapshot.json \
  --ids "build-example-target" \
  --output work/full-stack/preflight.json --overwrite
```

## 测试验证

```sh
python3 -B -m unittest discover -v -s tests
```

单测全部运行在临时隔离目录（`tempfile.TemporaryDirectory`）中，不会扫描或改动本机真实文件。

## 设计参考与许可

本技能在实战流程基础上，借鉴了以下开源项目的优秀思路并独立实现：
- [`SomSamantray/mac-storage-doctor`](https://github.com/SomSamantray/mac-storage-doctor)：并行子代理分工、`~/Library` 与 `/Applications` 孤儿残留交叉比对、Git Pack 膨胀检查；
- [`jmagar/claude-homelab`](https://github.com/jmagar/claude-homelab) 与 [`Sunwood-ai-labs/proxmox-skill`](https://github.com/Sunwood-ai-labs/proxmox-skill)：外置化环境拓扑配置、Proxmox 与 Docker 容器健康度深检；
- [`tw93/Mole`](https://github.com/tw93/Mole) 与 [`fengyiqicoder/CleanMyMac`](https://github.com/fengyiqicoder/CleanMyMac)：持久化 Fail-Closed 白名单保护与清理后物理空间回收闭环；
- 社区 Interactive Approval Report 模式：`localStorage` 状态记忆、单项微调备注框与一键复制指令回传。

采用 [MIT License](LICENSE) 开源。
