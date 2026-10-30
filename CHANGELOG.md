# 更新记录 (Changelog)

版本号写在 `SKILL.md` 的 `metadata.version` 和 `.claude-plugin/plugin.json` 的 `version`，与本文件最上面的条目保持一致（`tests/check-skill.sh` 会自动检查）。

## 1.0.0 — 2026-10-28

### 核心能力

- **四域并行只读巡检编排**：跨 Mac 本机与备份管线、本地多仓库工作区与 Worktree、GitHub 全账号远端仓库、以及 NAS / Proxmox VE / Docker 家庭服务器执行只读盘点，并联动复用 `mac-maintenance`、`repo-cleanup` 与 `codex-maintenance`。
- **标准库三合一引擎（`scripts/full_stack_tool.py`）**：
  - `scan-local`：自动交叉比对 `~/Library/{Application Support,Caches,Containers}` 与已安装 `/Applications` 的 `Info.plist`，识别疑似卸载残留（`app_orphan`）；扫描本地仓库 `.git/objects/pack` 膨胀（`git_pack_bloat`）、`AGENTS.md` 合入前检查缺失（`governance_doc`）与穿透备份排除规则的大型构建目录（`backup_exclusion_drift`）。
  - `diff`：对比前后两次快照 JSON，输出各存储池水位涨跌与体积增长最快的 Top 目录。
  - `preflight`：动手前二次并发安全复查，检查 Fail-Closed 白名单、符号链接边界、最近 N 分钟修改记录（`-mmin`）、`lsof` 打开句柄与 Git 工作区脏状态，防止误伤并行运行的 AI 会话。
- **单文件交互式 HTML 看板（`scripts/render_dashboard.py`）**：
  - 零外部依赖，采用 Anthropic 社论暖曜石深色（`#141413`）与暖白纸张浅色（`#FAF9F5`）双主题设计，支持按默认顺序、释放空间或存储池排序；
  - 三层双色真实比例存储可视化系统（`左边总容量 ｜ 中间进度条动态展示陶土橙当前项目 + 鼠尾草绿本池其他已选 ｜ 右边清理后已用容量`），涵盖顶部五池切换条、主存储池解剖条、目录构成微缩图与每张卡片内嵌量级条，并附带距上次巡检基线涨跌横幅；
  - 沙箱安全 `localStorage` 持久化保存勾选状态、单项微调备注与长期豁免白名单（兼容无 `allow-same-origin` 的受限 IDE 预览 `iframe`），支持一键导出结构化执行指令。
- **多 Agent 宿主适配**：
  - 提供 Claude Code 插件与 marketplace 清单（`.claude-plugin/`）、OpenAI Codex 清单（`agents/openai.yaml`）、通用 `npx skills add` 支持及 [多 Agent 宿主适配指南](references/multi-agent-hosts.md)，兼容 Claude Code、Codex、Google Antigravity、OpenCode、Cursor 与 Windsurf。
