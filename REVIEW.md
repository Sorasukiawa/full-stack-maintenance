# 高风险路径审查清单（REVIEW.md）

本仓库为公开分发的跨平台全栈巡检与交互式看板技能。任何改动只要触及下列高风险路径，在合入 `main` 前**必须**使用全新上下文完成一次独立代码审查与一次公开仓库安全／隐私审查；合并提交说明须附一行撤销（revert）方式。

## 1. 核心扫描、并发复核与隔离引擎

- **路径**：`scripts/full_stack_tool.py`
- **重点函数**：`cmd_quarantine`、`cmd_preflight`、`evaluate_single_target`、`quarantine_refusal_for`、`resolve_quarantine_root`、`check_quarantine_root`、`load_topology`、`build_whitelist_index`（白名单解析与索引构建）
- **必查项**：
  1. **显式目标与零直接破坏**：`quarantine` 子命令必须强制要求 `--ids` 非空，严禁默认处理全量候选；全脚本严禁出现直接删除文件（`rm` / `unlink` / `rmtree` 目标数据）、卸载应用、清理 Git 分支或销毁容器／虚拟机的破坏性调用。
  2. **符号链接不穿透**：目录体积统计、白名单设备／inode 比对与预检均须保持 `follow_symlinks=False`；符号链接根路径在 `evaluate_single_target` 中必须返回 `BLOCKED_SYMLINK_ROOT`，绝不穿透移走真实目标。
  3. **不可隔离类别硬拒绝**：Git 内部数据与对象库（`.git`、`git_pack_bloat`）、Git 工作树（`worktree`）、仓库治理与说明文档（`AGENTS.md`、`CLAUDE.md`、`GEMINI.md`、`REVIEW.md`、`CONTRIBUTING.md`、`README*`）、系统与用户启动项（`LaunchAgents` / `LaunchDaemons` 下的 `.plist`）、应用／开发环境更新建议（`app_update`、`env_update`）以及外设固件健康建议（`peripheral_firmware`）在 `preflight` 与 `quarantine` 中一律拒绝隔离，并给出安全的领域处理指引。
  4. **外置卷同盘隔离与防慢挂载**：外置卷上的候选项隔离时必须落在该外置卷本盘隔离目录（如本盘 `.Trash-quarantine` 或经真实挂载点 `os.path.ismount` 与 `VolumeUUID` 核验的同卷隔离根），严禁跨卷搬移大目录，严禁把未挂载的 `/Volumes/<名称>` 普通目录当作外置盘写入，也严禁自动遍历 `/Volumes` 触发网络盘或休眠盘阻塞。
  5. **配置解析 Fail-Closed**：`load_topology` 与白名单／隔离根解析在配置文件缺失、JSON 语法损坏、`version` 不兼容或 `whitelist` / `quarantine` 字段类型非法时，必须立即抛出 `ConfigError` 并以返回码 `2` 退出，绝不降级成空配置或无白名单放行。
  6. **整棵 Worktree 级联保护与双模式卸载边界**：处于活跃修改窗口、存在未提交改动、有活跃 AI 会话或被进程占用句柄的仓库／worktree，其内部构建缓存也必须整体受保护；应用卸载在未显式指定模式时必须默认 `keep_data`（完整保留 `~/Library` 用户配置、存档与容器数据），仅在显式传入 `purge_all` 时才包含已确认归属的应用数据目录，且绝不连带误扫系统前缀或共享开发工具缓存。

## 2. 看板渲染、前端资源与指令导出

- **路径**：`scripts/render_dashboard.py`、`scripts/dashboard_assets/`（若存在拆分前端模板／样式／脚本资源）
- **必查项**：
  1. **脚本闭合转义与 DOM XSS 防护**：向 HTML 内联注入快照与对比 JSON 时，必须对 `</` 转义为 `<\/`（防止构造 `</script>` 提前闭合标签），且数据占位替换放在模板替换最后一步；前端拼接候选路径、应用名、备注、挂载点与告警文案进入 `innerHTML` 时必须经过 `escapeHtml` 转义或改用 `textContent`。
  2. **CSP 与零外部请求**：生成的单文件 HTML 必须声明严格的 Content Security Policy（如 `default-src 'none'` 配合受控内联样式／脚本与 `img-src data:`），保持离线自包含，零外部 CDN、零远程字体、零遥测或外联请求。
  3. **导出执行指令准确性**：前端生成复核／隔离命令或审批文本时，必须准确携带用户勾选的 `--ids`，并按各应用候选项实际选定的卸载模式准确区分默认 `keep_data` 与显式选择的 `--uninstall-mode purge_all`；严禁把未勾选项混入审批文本，或把不可隔离项混入 `quarantine` 隔离命令。

## 3. 技能入口、宿主清单与工具权限边界

- **路径**：`SKILL.md`、`agents/openai.yaml`、`.claude-plugin/plugin.json`、`.claude-plugin/marketplace.json`
- **必查项**：
  1. **`allowed-tools` 最小只读边界**：`SKILL.md` frontmatter 的 `allowed-tools` 只允许预批准只读扫描、快照对比、看板渲染与 `preflight` 复核命令，严禁预批准 `quarantine`、`rm`、`git clean`、`git reset`、`git worktree remove/prune`、`git branch -D`、`git push` 等任何写操作或删除操作。
  2. **多宿主元数据一致与安全**：`SKILL.md`、`CHANGELOG.md` 与 `.claude-plugin/plugin.json` 版本号保持一致；`agents/openai.yaml` 的默认提示词与描述明确指向只读巡检与看板审批流程，不包含诱导跳过人工确认或越权执行的措辞。

## 4. 公开文档、示例配置与评测集隐私防线

- **路径**：`README.md`、`README.zh-CN.md`、`README.zh-TW.md`、`README.ja.md`、`SKILL.md`、`REVIEW.md`、`CHANGELOG.md`、`config/topology.json`、`config/topology.example.json`、`references/*`、`evals/*`
- **必查项**：
  1. **零隐私与本机拓扑泄露**：严禁出现真实个人家目录路径（统一用 `~` 或 `/Users/<user>`）、真实内网／Tailscale IP（示例统一用 RFC 5737 文档网段如 `192.0.2.x`）、真实主机名／Tailnet 域名、个人账号／邮箱、真实外置磁盘卷名或真实设备 UUID（外置卷示例统一用 `/Volumes/ExternalSSD`、`/Volumes/NAS-Fast` 与全零占位 UUID）。
  2. **多语言事实一致**：英文、简体中文、繁体中文、日文四份说明文档对安全边界、只读默认行为、双模式卸载与隔离恢复机制的描述必须保持事实一致，不夸大自动化删除能力。
