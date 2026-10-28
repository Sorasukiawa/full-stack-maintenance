# 多 Agent 宿主适配指南（Claude Code / Codex / Antigravity / OpenCode / Cursor）

`full-stack-maintenance` 遵循通用 [Agent Skills 规范](https://agentskills.io)，同时内置了 **Claude Code Plugin 清单**（`.claude-plugin/`）与 **OpenAI Codex 界面清单**（`agents/openai.yaml`）。不同 AI 编程助手在路径解析、并行子代理（Subagent）调度以及 HTML 看板展示方式上存在差异，按本指南自动适配。

---

## 1. 宿主安装路径与脚本目录定位

无论在哪个宿主中运行，先按以下顺序定位本技能的根目录 `SKILL_ROOT`：

1. **Claude Code 插件或内置变量**：若环境变量或模板上下文提供 `${CLAUDE_SKILL_DIR}`，直接使用它；
2. **通用 Agent Skills 目录**：`~/.agents/skills/full-stack-maintenance`（推荐作为跨宿主统一入口，其他宿主通过软链接指向此处）；
3. **各宿主专属目录**：
   - OpenAI Codex：`~/.codex/skills/full-stack-maintenance`
   - Claude Code（独立安装）：`~/.claude/skills/full-stack-maintenance`
   - Google Antigravity：`~/.gemini/antigravity/skills/full-stack-maintenance`
   - OpenCode：`~/.config/opencode/skills/full-stack-maintenance`
   - 项目级目录：`<workspace>/.agents/skills/full-stack-maintenance` 或 `<workspace>/.claude/skills/full-stack-maintenance`

定位到 `SKILL_ROOT` 后，优先检查 `$SKILL_ROOT/config/topology.local.json` 是否存在；存在则将其传给 `--config`，否则使用 `$SKILL_ROOT/config/topology.json`。

---

## 2. 各宿主触发方式与权限预授

| 宿主客户端 | 触发方式 | 只读权限与工具适配 |
|---|---|---|
| **Claude Code** | `/full-stack-maintenance`（插件完整名为 `/full-stack-maintenance:full-stack-maintenance`）或自然语言描述 | `SKILL.md` 的 `allowed-tools` 已预先授权只读扫描（`scan-local`、`diff`、`preflight`、`render_dashboard.py`、`git status`、`gh repo list`、`df -h`），盘点阶段不会逐条弹窗确认 |
| **OpenAI Codex** | `$full-stack-maintenance` 或从侧栏技能面板点选「全栈巡检与看板」 | 自动读取 `agents/openai.yaml` 中的默认提示词与描述 |
| **Google Antigravity** | 自然语言（如「用 `full-stack-maintenance` 做一次全栈检查」） | 自动匹配技能描述并加载 `SKILL.md` |
| **OpenCode / Cursor / Windsurf / Cline** | `/full-stack-maintenance`、`@full-stack-maintenance` 或自然语言描述 | 通过 `npx skills add` 安装到 `~/.agents/skills/` 或宿主对应技能目录后自动生效 |

---

## 3. 四域并行子代理（Subagent）与单线程回退策略

在「阶段 3：四域只读巡检」中，根据当前宿主是否支持并发子代理工具自动切换执行策略：

### 策略 A：支持原生并行子代理的宿主（推荐）
- **Google Antigravity**：在同一轮调用 4 次 `invoke_subagent`（`TypeName: "research"` 或 `"self"`），分别负责 **Mac 与备份**、**本地多仓库与 Worktree**、**GitHub 全账号远端**、**NAS 与 PVE Homelab**。无需轮询等待，4 个子代理完成后会自动唤醒主会话汇总。
- **Claude Code**：在同一轮并行发起 4 个 `Task`（`subagent_type: "Explore"` 或 `"general-purpose"`），要求每个子代理只做只读盘点并返回结构化 JSON 候选列表。
- **OpenAI Codex / OpenCode**：若当前环境开启了并行 subagent / `task` 工具，按同样方式分 4 路只读派发。

### 策略 B：不支持子代理的宿主（Cursor / Windsurf / Cline 或受限环境）
- 自动回退为**主会话分批并行命令执行**：
  1. 先运行 `python3 "$SKILL_ROOT/scripts/full_stack_tool.py" scan-local` 完成本机孤儿残留、`.git` 膨胀与备份穿透扫描；
  2. 再依次执行 GitHub 远端只读查询（`gh repo list` / `gh run list`）与 Homelab SSH 只读探针（`df -h` / `lvs` / `docker system df`），将四域结果合并到同一个快照 JSON 中。

---

## 4. 交互式 HTML 看板的存放与打开方式

生成单文件 `inspection-dashboard.html` 时，按宿主特性选择最佳展示路径：

1. **Google Antigravity**：
   - 将看板直接渲染到当前会话的 Artifact 目录：`<appDataDir>/brain/<conversation-id>/inspection-dashboard.html`，并在回复中提供 `[inspection-dashboard.html](file:///...)` 链接，用户可在内置预览或浏览器中直接勾选。
2. **Claude Code / OpenAI Codex / OpenCode / Cursor**：
   - 将看板输出到工作区或临时维护目录（如 `work/full-stack/inspection-dashboard.html` 或 `/tmp/full-stack-dashboard-<date>.html`）；
   - 若用户要求「打开给我看看」或当前在桌面端环境，可在 macOS 上运行 `open "<html_path>"`（Linux 用 `xdg-open`，Windows 用 `Start-Process`）在默认浏览器中唤起看板，同时在回复中附上完整路径。
3. **Orca 终端环境（含 `ORCA_PANE_KEY`）**：
   - 可配合 `orca-browser` 技能在 Orca 内置浏览器面板中直接打开生成的本地 HTML 看板供用户交互勾选。
