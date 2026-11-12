---
name: full-stack-maintenance
description: Use when a user requests a full-stack or cross-domain health check, storage audit, optimization, or interactive HTML approval dashboard spanning Mac, local multi-repo workspaces, GitHub repositories, and NAS/PVE/Homelab servers. Coordinates with mac-maintenance, repo-cleanup, and codex-maintenance.
license: MIT
compatibility: Requires Python 3.10+ (standard library only) and git. gh is optional for GitHub account audits; ssh is optional for NAS/Proxmox homelab checks. Works with Claude Code, Codex, Antigravity, OpenCode, Cursor, and Windsurf.
argument-hint: "[inspect only | dashboard | execute <ids> | mac | projects | github | nas]"
allowed-tools: Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/full_stack_tool.py scan-local *), Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/full_stack_tool.py diff *), Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/full_stack_tool.py preflight *), Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/render_dashboard.py *), Bash(git status *), Bash(git worktree list *), Bash(gh repo list *), Bash(gh run list *), Bash(df -h *), Bash(du -sh *)
metadata:
  version: "1.4.0"
---

# 全栈多域巡检与交互式看板维护

面向「Mac 本机 + 本地多仓库工作区 + GitHub 全账号远端 + NAS/PVE/Docker 家庭服务器」的总管级维护技能。负责跨四域并行只读盘点、跨层连带病灶追查、前后基线快照对比、带水位模拟器的单文件 HTML 审批看板生成，以及动手前二次并发安全复核。

## 1. 定位、多 Agent 适配与已有技能联动

本技能支持 **Claude Code、OpenAI Codex、Google Antigravity、OpenCode、Cursor、Windsurf** 等主流 Agent 宿主；各宿主的目录定位、并行子代理调度与看板打开方式见 [多 Agent 宿主适配指南](references/multi-agent-hosts.md)。

**分工**：只涉及这台 Mac 的请求（清缓存、卸载应用、查更新、启动项、固件）直接用 `$mac-maintenance`；跨 Mac、本地项目、GitHub、家庭服务器的总检查才用本技能，其中 Mac 部分优先调用 `$mac-maintenance` 的结论，`scan-local` 的同类扫描只作补充和交叉核对。提交、合并、推送交给 `$git-ship`；worktree 和分支退役交给 `$repo-cleanup`；模拟器和虚拟机的所有权与回收交给 `$temporary-environments`；本机 AI 工具、技能与 MCP 交给 `$codex-maintenance`。

| 领域 | 联动技能与工具 | 本技能补充的跨层职责 |
|---|---|---|
| **Mac 系统、应用与外设** | 优先调用 `$mac-maintenance`（`audit.py` / `cleanup.py`） | 补充**全量 APP 体积与常用度统计及双模式卸载**（默认 `keep_data` 保留用户数据；`purge_all` 连数据一起移走需用户明确选择）、**跨 8 类 Library 目录的卸载残留扫描与同源聚合**、**无用启动项排查**（失效路径/已卸载父应用/崩溃循环）、**APP 与开发依赖环境更新检查**、**外设与存储固件/健康度检查**（硬盘/SD 卡/扩展坞/键鼠）、**全局纯缓存扫描**、**云端/NAS 备份排除规则穿透检测**、APFS 本地快照锁死排查 |
| **本地多仓库与 AI 组件** | 优先调用 `$repo-cleanup`（`survey.sh`）与 `$codex-maintenance` | 补充跨仓库批量扫描、**`.git/objects/pack` 膨胀体检**、各仓库 `git worktree list` 汇总（只盘点，退役交给 `$repo-cleanup`），以及 `AGENTS.md`（没有再看 `CLAUDE.md`）里「合入前检查：」写法核对 |
| **GitHub 全账号远端** | 使用 `gh repo list`、`gh run list`、`git ls-remote` | 全量盘点公开/私有仓库、陈旧分支与 `checkpoints/*`、CI 失败真因（区分偶发竞态与真实报错）、归档候选与本地未建远端项目 |
| **NAS 与 PVE 家庭服务器** | 只读 SSH / 挂载卷检查 | 存储池红线水位、PVE `/boot/efi` 旧内核、`local-lvm` 未挂载孤儿盘与演练克隆机、LXC 未回收块、Docker 构建缓存与容器健康度、SMB/Btrfs `#recycle` 二次截留 |

## 2. 外置拓扑配置与持久化白名单（Fail-Closed）

1. **优先读取个人配置**：定位技能根目录（Claude Code 插件中为 `${CLAUDE_SKILL_DIR}`，其他宿主见 [多 Agent 宿主适配指南](references/multi-agent-hosts.md)）后，检查是否存在 Git 忽略的 `config/topology.local.json`；存在则显式传给 `--config`，否则使用通用默认配置 [topology.json](config/topology.json)（可参考 [topology.example.json](config/topology.example.json) 创建）。
2. **Fail-Closed 保护**：配置文件语法错误、版本不匹配、`whitelist` 结构非法或 `quarantine.root_dirs` 条目写错时，脚本返回退出码 `2` 并立即中止，绝不降级成「无白名单全量放行」。
3. **持久化白名单免打扰**：命中 `whitelist.paths`、`whitelist.repos`、`whitelist.app_names` 或 `whitelist.guest_ids` 的条目，在 JSON 与 HTML 看板中自动归入折叠的「🛡️ 已豁免白名单」分页，默认不勾选，且在 `preflight` 阶段强制拦截。用户在看板中点击「🛡️ 长期豁免」并回传指令后，将新增豁免项写回 `config/topology.local.json`。
4. **巡检产物放在哪**：快照、对比、看板、`preflight` 与 `quarantine` 结果以及下次对比用的基线，统一放技能目录下被 Git 忽略的 `state/`（下文写作 `${CLAUDE_SKILL_DIR}/state/`，其他宿主换成 `$SKILL_ROOT/state/`）。脚本只写 `--output` 指定的位置，不自己挑目录。

## 3. 四域并行只读巡检流程

收到全栈检查请求时，保持**100% 只读**（不删除、不 `git fetch --prune`、不卸载、不重启服务），按以下步骤推进：

1. **运行本机快速扫描与基线对比**：
   ```bash
   mkdir -p "${CLAUDE_SKILL_DIR}/state"
   python3 "${CLAUDE_SKILL_DIR}/scripts/full_stack_tool.py" scan-local \
     --config "${CLAUDE_SKILL_DIR}/config/topology.local.json" \
     --output "${CLAUDE_SKILL_DIR}/state/current-snapshot.json" --overwrite
   ```
   `scan-local` 会一次性完成：
   - **APP 盘点与双模式卸载候选（`app_uninstall` + `app_inventory`）**：统计每个已安装 APP 的本体体积、用户数据体积与缓存体积；「最近使用」取 Spotlight `kMDItemLastUsedDate`（最近打开）和应用及其数据、缓存目录最近文件改动中较晚的一个，取不到 Spotlight 值时只按文件改动估算，`last_used_basis` 与看板标题会写明是「N 天未打开」还是「N 天无文件改动」（默认 `>=90` 天且 `>=50 MiB` 才列为候选）。卸载默认 `keep_data`（只移走 `.app` 与缓存，保留用户数据）；`purge_all` 连用户数据和残留一起移走，必须由用户明确选择；
   - **卸载残留清理（`app_orphan`）**：扫描 `Application Support`、`Caches`、`Containers`、`Group Containers`、`Saved Application State`、`Logs`、`HTTPStorages`、`WebKit` 等目录，将同一已卸载应用散落在多处的残留自动聚合成单张多路径卡片；
   - **无用 Mac 启动项检查（`launch_item`）**：检查 `LaunchAgents` / `LaunchDaemons` 中可执行文件已丢失、父 APP 已卸载或 `launchctl` 异常退出的启动项；
   - **APP 更新（`app_update`）与开发环境依赖更新（`env_update`）**：检查 Homebrew Cask、Mac App Store (`mas`)、Homebrew Formulae、全局 `npm` 包与 `rustup` 工具链更新；
   - **外设与存储固件健康检查（`peripheral_firmware`）**：盘点内置/外置硬盘（SMART 状态与固件版本）、SD 卡读卡器、雷电/USB 扩展坞（排查高速设备降速到 USB 2.0 `480 Mb/s`）及蓝牙/USB 键鼠（固件与低电量告警）；
   - **全局纯缓存检查（`system_cache`）**：扫描 Xcode `DerivedData`、Homebrew 下载缓存、`npm`/`uv`/`pip` 缓存与 `ms-playwright` 旧浏览器内核，跳过日常热缓存。
   若存在上一次巡检留下的基线快照 `${CLAUDE_SKILL_DIR}/state/baseline-snapshot.json`，立即运行 `diff` 找出**「这几天是谁偷偷占了空间」**：
   ```bash
   python3 "${CLAUDE_SKILL_DIR}/scripts/full_stack_tool.py" diff \
     --before "${CLAUDE_SKILL_DIR}/state/baseline-snapshot.json" \
     --after "${CLAUDE_SKILL_DIR}/state/current-snapshot.json" \
     --output "${CLAUDE_SKILL_DIR}/state/diff.json" --overwrite
   ```
2. **派出 4 路并行只读子代理（Subagents）补齐深层证据**（不支持子代理的宿主按 [多 Agent 宿主适配指南](references/multi-agent-hosts.md) 顺序执行）：
   - **子代理 1（Mac 与备份管线）**：结合 `$mac-maintenance` 的 `audit.py --quick`，核查 `/System/Volumes/Data` 水位、Xcode `Unavailable` 模拟器（只盘点，列为 C 级，见第 5 节）、Time Machine 本地快照、以及 iCloud/NAS 自动备份脚本是否漏排了新建的构建或归档目录。
   - **子代理 2（本地项目群与 AI 工具）**：遍历 `workspace_roots` 下的仓库，用 `git worktree list` 汇总各仓库的 worktree（只盘点，退役交给 `$repo-cleanup`），检查未提交改动、`AGENTS.md`（没有再看 `CLAUDE.md`）是否有行首的「合入前检查：`命令`」或「合入前检查：无」、缺失的 `CLAUDE.md`、以及散落的编译产物。
   - **子代理 3（GitHub 全账号远端）**：用 `gh` 盘点全部公开/私有仓库，筛出长期废弃或已合并的远端分支（如 `checkpoints/*`）、近期失败的 GitHub Actions、以及已完成历史使命可 `gh repo archive` 的仓库（都是 C 级候选，盘点阶段不删不归档）。
   - **子代理 4（NAS 与 PVE Homelab）**：当 `homelab.enabled` 为 `true` 时，只读检查 PVE 节点 `/boot/efi`、`pve-root`、`local-lvm` 水位、各 VM/LXC 磁盘占用与 `unused` 卷、Docker 容器健康状态（`unhealthy` / 重启循环）与镜像缓存、以及 NAS 各存储池与 `#recycle` 回收站占用。
   - **仅 Google Antigravity**：它的子代理交卷后默认停在 `idle` 占内存，汇总完立即调用 `manage_subagents(Action: "kill_all")` 回收；Claude Code 的 `Agent` 子代理结束后自动释放，不需要这一步。
   - 四域详细排查清单与 6 大跨层存储陷阱见 [四域巡检与跨层陷阱参考](references/domains-and-traps.md)。

## 4. 生成交互式 HTML 水位模拟看板

将四路子代理汇总的候选项合并进快照 JSON（按风险分为 **A 级·安全立省**、**B 级·整理升级**、**C 级·需拍板确认**），调用 `scripts/render_dashboard.py` 生成单文件 HTML 看板：

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/render_dashboard.py" \
  --input "${CLAUDE_SKILL_DIR}/state/current-snapshot.json" \
  --diff "${CLAUDE_SKILL_DIR}/state/diff.json" \
  --output "${CLAUDE_SKILL_DIR}/state/inspection-dashboard.html" --overwrite
```

生成的 HTML 看板具备以下交互能力（详见 [看板与脚本契约](references/dashboard-and-cli.md)）：
- **存储圆环总览**：样式参考 Apple 健康活动圆环的圆头粗环，灰色为清理后已用、沿弧绿青渐变为勾选将释放、蓝色为悬停项占比，配右侧四格容量数据与药丸形分段「空间构成」条；上方存储卷小圆环可切换单盘或全部存储卷；勾选或取消时圆环、水位条和数字以带轻回弹的弹簧动效实时变化。
- **APP 卸载双模式切换器**：在久未使用 APP 卡片详情内切换「**保留用户数据（仅删 App 与缓存）**」（默认）与「**一键全部删除（含残留）**」，卡片体积、顶部存储圆环释放预计、影响路径列表与导出指令中的 `--uninstall-mode` 参数实时联动。
- **按领域分页的勾选工作台**：侧栏切换 Mac、本地项目、GitHub、NAS 与白名单，按风险等级分组、按动作类型筛选，顶部水位条实时显示对所在存储卷的影响。
- **距上次巡检基线变化**：直接展示各盘水位涨跌与增长最快的 Top 目录。
- **浏览器本地记忆（`localStorage`）与单项微调备注框**：刷新页面不丢勾选与卸载模式；每项旁均可填写微调说明（如「只留最近 2 个版本」「移到 NAS 别直接删」）。
- **一键复制执行指令**：将用户勾选的 ID、所选卸载模式（`--uninstall-mode keep-data` / `purge-all`）、填写的单项微调备注、以及新加入白名单的条目一键打包成结构化 Markdown 指令回传给代理。

## 5. 动手前「二次并发安全复查」、活跃整树豁免与分级外置隔离区

收到用户确认或从 HTML 看板复制回传的勾选清单后，按以下铁律执行：

1. **强制运行 `preflight` 二次并发安全复查**：
   用户在网页上勾选到真正执行之间，可能有别的 AI 会话刚启动了构建或正在使用某棵工作树里的构建/测试产物（如 `distribution/artifacts`、`target`），动手前必须运行（卸载模式默认 `keep-data`，只有用户在看板或对话里明确选了「一键全部删除」才传 `purge-all`）：
   ```bash
   python3 "${CLAUDE_SKILL_DIR}/scripts/full_stack_tool.py" preflight \
     --config "${CLAUDE_SKILL_DIR}/config/topology.local.json" \
     --targets "${CLAUDE_SKILL_DIR}/state/current-snapshot.json" \
     --ids "<用户勾选的逗号分隔ID>" \
     --uninstall-mode keep-data \
     --output "${CLAUDE_SKILL_DIR}/state/preflight.json" --overwrite
   ```
   - **活跃工作树整树豁免**：只要目标路径本身或其所在的父级 Git 工作树/仓库命中 `BLOCKED_WHITELISTED`、`BLOCKED_RECENT_ACTIVITY`（工作树在 `worktree_protect_minutes` 默认 48 小时内有文件或 `.git` 变动）、`BLOCKED_ACTIVE_WORKTREE`、`BLOCKED_OPEN_HANDLES` 或 `BLOCKED_DIRTY_WORKTREE`，**整棵工作树（含内部的 `target`、`node_modules`、`distribution/artifacts` 等忽略目录）一律跳过，绝不清理或移动**。
   - **会话识别的边界**：`BLOCKED_ACTIVE_WORKTREE` 只读 Codex 的会话记录（`~/.codex/sessions/**/*.jsonl` 里近期提到该工作树路径）；Claude Code、Antigravity、OpenCode 的会话目前不读，只能靠最近改动时间、未提交改动和 `lsof` 打开句柄间接保护。
   - 每条结果带 `quarantine_allowed`：为 `false` 时附 `quarantine_refusal` 说明为什么这一项永远不会被移进隔离区。
   - 用户在卡片备注框里写了微调要求的（如「留最新 3 个版本」），严格按备注缩小操作范围。
2. **分级处置**：
   - **A 级全局纯缓存**（Homebrew 下载缓存、pip/npm/uv 全局缓存、Xcode `DerivedData`）：通过 `preflight` 后可直接清理，第一时间释放 Mac 空间。
   - **C 级、不批量删除**：Xcode `Unavailable` 模拟器先按 `$temporary-environments`（`~/.agents/skills/temporary-environments/SKILL.md`）的所有权登记核对，只回收登记为本工具创建且已可回收的设备，其余逐台列给用户拍板，不跑 `xcrun simctl delete unavailable`；PVE 灾难恢复演练克隆机（如 `*-dr-test`）、旧内核、`docker image prune -a`、远端分支删除和仓库归档同样只列候选，等用户逐项确认。
   - **worktree 类候选**：一律转交 `$repo-cleanup` 按退役流程处理（先确认无未提交改动、无未合入提交、无活跃会话），不进隔离区。
   - **闲置项目构建产物、测试载荷、`artifacts`、卸载残留（`app_orphan`）与待卸载 APP（`app_uninstall`）**：**严禁直接 `rm -rf`，也不放入本机废纸篓**；用 `quarantine` 子命令移入隔离区。它必须带 `--ids`（不会默认全部），移动前对每个路径重跑与 `preflight` 相同的检查，只移判为安全的路径；Git pack 与 `.git` 内部路径、治理文档（`AGENTS.md` 一类）、启动项、各类更新检查、固件和 worktree 类候选一律拒绝，在 `refused_items` 里写明原因。先 `--dry-run` 看计划，再去掉 `--dry-run` 执行：
     ```bash
     python3 "${CLAUDE_SKILL_DIR}/scripts/full_stack_tool.py" quarantine \
       --config "${CLAUDE_SKILL_DIR}/config/topology.local.json" \
       --targets "${CLAUDE_SKILL_DIR}/state/current-snapshot.json" \
       --ids "<preflight 判为 SAFE_TO_EXECUTE 且 quarantine_allowed 的逗号分隔ID>" \
       --uninstall-mode keep-data \
       --dry-run \
       --output "${CLAUDE_SKILL_DIR}/state/quarantine-plan.json" --overwrite
     ```
   - **隔离区位置**：只用配置里 `quarantine.root_dirs` 列出的根，不自动遍历 `/Volumes`。`/Volumes/<盘名>` 下的根必须是真实挂载点（`os.path.ismount`）且可写；写了 `volume_uuid` 的还要和 `diskutil info -plist <挂载点>` 的 `VolumeUUID` 一致。都不满足时退回本机 `~/.Cleanup_Quarantine/`，输出里的 `quarantine_root_fallback_reason` 写明原因。每个批次自动生成 `manifest.json` 与一键还原脚本 `restore.sh`。
   - **清空隔离区**：脚本不提供删除命令。待用户确认各并行任务运行正常后，列出要清的批次、内容和体积，用户同意后再删批次目录。
3. **分域执行与物理释放核验**：
   - 删除或移入隔离区后必须核对 `df -h` 真实可用空间变化，不能只看 `du` 目录减少量。
   - 若可用空间没有明显上升，按 [四域巡检与跨层陷阱参考](references/domains-and-traps.md) 排查原因。**APFS 本地快照瘦身**（`tmutil thinlocalsnapshots`）、**PVE LXC 块回收**（`pct fstrim <ctid>`）、**NAS `#recycle` 清理**（只针对本批次删掉的条目）和**删除 iCloud 等备份的历史分块**都会动到备份或家里正在跑的服务，一律作为 C 级候选写进看板，等用户拍板，不在执行阶段自动做。
4. **收尾更新看板与基线**：
   - 仅 Google Antigravity：若执行阶段派出了子代理，收齐结果后调用 `manage_subagents(Action: "kill_all")` 回收；
   - 将已完成条目的 `status` 标为 `"done"`，重新渲染 HTML 看板供用户留存；
   - 若用户标记了新的「🛡️ 长期豁免」项，将其写入 `config/topology.local.json`；
   - 重新运行 `scan-local`，把清理后的快照保存为新的 `${CLAUDE_SKILL_DIR}/state/baseline-snapshot.json`，供下次巡检对比。
