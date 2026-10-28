---
name: full-stack-maintenance
description: Use when a user requests a full-stack or cross-domain health check, storage audit, optimization, or interactive HTML approval dashboard spanning Mac, local multi-repo workspaces, GitHub repositories, and NAS/PVE/Homelab servers. Coordinates with mac-maintenance, repo-cleanup, and codex-maintenance.
---

# 全栈多域巡检与交互式看板维护

面向「Mac 本机 + 本地多仓库工作区 + GitHub 全账号远端 + NAS/PVE/Docker 家庭服务器」的总管级维护技能。负责跨四域并行只读盘点、跨层连带病灶追查、前后基线快照对比、带水位模拟器的单文件 HTML 审批看板生成，以及动手前二次并发安全复核。

## 1. 定位与已有技能联动

本技能不重复实现单机或单仓库的底层细则，而是按职责编排联动：

| 领域 | 联动技能与工具 | 本技能补充的跨层职责 |
|---|---|---|
| **Mac 系统与空间** | 优先调用 `$mac-maintenance`（`audit.py` / `cleanup.py`） | 补充**孤儿应用残留交叉比对**（`~/Library` vs `/Applications`）、**云端/NAS 备份脚本排除规则穿透检测**、APFS 本地快照锁死排查 |
| **本地多仓库与 AI 组件** | 优先调用 `$repo-cleanup`（`survey.sh`）与 `$codex-maintenance` | 补充跨仓库批量扫描、**`.git/objects/pack` 膨胀体检**、多宿主 Worktree 汇总（`.claude/worktrees`、`.worktrees`、Orca、Codex）与 `AGENTS.md` 合入前检查核对 |
| **GitHub 全账号远端** | 使用 `gh repo list`、`gh run list`、`git ls-remote` | 全量盘点公开/私有仓库、陈旧分支与 `checkpoints/*`、CI 失败真因（区分偶发竞态与真实报错）、归档候选与本地未建远端项目 |
| **NAS 与 PVE 家庭服务器** | 只读 SSH / 挂载卷检查 | 存储池红线水位、PVE `/boot/efi` 旧内核、`local-lvm` 未挂载孤儿盘与演练克隆机、LXC `pct fstrim`、Docker 构建缓存与容器健康度、SMB/Btrfs `#recycle` 二次截留 |

## 2. 外置拓扑配置与持久化白名单（Fail-Closed）

1. **优先读取个人配置**：检查技能目录下是否存在 Git 忽略的 `config/topology.local.json`；存在则显式传给 `--config`，否则使用通用默认配置 [topology.json](config/topology.json)（可参考 [topology.example.json](config/topology.example.json) 创建）。
2. **Fail-Closed 保护**：配置文件语法错误、版本不匹配或 `whitelist` 结构非法时，脚本返回退出码 `2` 并立即中止，绝不降级成「无白名单全量放行」。
3. **持久化白名单免打扰**：命中 `whitelist.paths`、`whitelist.repos`、`whitelist.app_names` 或 `whitelist.guest_ids` 的条目，在 JSON 与 HTML 看板中自动归入折叠的「🛡️ 已豁免白名单」分页，默认不勾选，且在 `preflight` 阶段强制拦截删除。用户在看板中点击「🛡️ 长期豁免」并回传指令后，将新增豁免项写回 `config/topology.local.json`。

## 3. 四域并行只读巡检流程

收到全栈检查请求时，保持**100% 只读**（不删除、不 `git fetch --prune`、不卸载、不重启服务），按以下步骤推进：

1. **运行本机快速扫描与基线对比**：
   ```bash
   python3 scripts/full_stack_tool.py scan-local \
     --config config/topology.local.json \
     --output work/full-stack/current-snapshot.json --overwrite
   ```
   若存在上一次巡检留下的基线快照（如 `work/full-stack/baseline-snapshot.json`），立即运行 `diff` 找出**「这几天是谁偷偷占了空间」**：
   ```bash
   python3 scripts/full_stack_tool.py diff \
     --before work/full-stack/baseline-snapshot.json \
     --after work/full-stack/current-snapshot.json \
     --output work/full-stack/diff.json --overwrite
   ```
2. **派出 4 路并行只读子代理（Subagents）补齐深层证据**：
   - **子代理 1（Mac 与备份管线）**：结合 `$mac-maintenance` 的 `audit.py --quick`，核查 `/System/Volumes/Data` 水位、Xcode `DerivedData` 与 `Unavailable` 模拟器、Time Machine 本地快照、以及 iCloud/NAS 自动备份脚本是否漏排了新建的构建或归档目录。
   - **子代理 2（本地项目群与 AI 工具）**：遍历 `workspace_roots` 与 `worktree_hubs`，检查各仓库未提交改动、已合并可退役的 Worktree、缺少 `合入前检查：` 的 `AGENTS.md`、缺失的 `CLAUDE.md`、以及散落的编译产物。
   - **子代理 3（GitHub 全账号远端）**：用 `gh` 盘点全部公开/私有仓库，筛出长期废弃或已合并的远端分支（如 `checkpoints/*`）、近期失败的 GitHub Actions、以及已完成历史使命可 `gh repo archive` 的仓库。
   - **子代理 4（NAS 与 PVE Homelab）**：当 `homelab.enabled` 为 `true` 时，只读检查 PVE 节点 `/boot/efi`、`pve-root`、`local-lvm` 水位、各 VM/LXC 磁盘占用与 `unused` 卷、Docker 容器健康状态（`unhealthy` / 重启循环）与镜像缓存、以及 NAS 各存储池与 `#recycle` 回收站占用。
   - 四域详细排查清单与 5 大跨层存储陷阱见 [四域巡检与跨层陷阱参考](references/domains-and-traps.md)。

## 4. 生成交互式 HTML 水位模拟看板

将四路子代理汇总的候选项合并进快照 JSON（按风险分为 **A 级·安全立省**、**B 级·整理升级**、**C 级·需拍板确认**），调用 `scripts/render_dashboard.py` 生成单文件 HTML 看板：

```bash
python3 scripts/render_dashboard.py \
  --input work/full-stack/current-snapshot.json \
  --diff work/full-stack/diff.json \
  --output work/full-stack/inspection-dashboard.html --overwrite
```

生成的 HTML 看板具备以下交互能力（详见 [看板与脚本契约](references/dashboard-and-cli.md)）：
- **顶部红线水位实时模拟器**：勾选/取消下方卡片时，顶部各存储池进度条与预计可用空间实时动态增减。
- **距上次巡检变化横幅**：直接展示各盘水位涨跌与增长最快的 Top 目录。
- **浏览器本地记忆（`localStorage`）与单项微调备注框**：刷新页面不丢勾选；每项旁均可填写微调说明（如「只留最近 2 个版本」「移到 NAS 别直接删」）。
- **一键复制执行指令**：将用户勾选的 ID、填写的单项微调备注、以及新加入白名单的条目一键打包成结构化 Markdown 指令回传给代理。

## 5. 动手前「二次并发安全复查」与物理释放闭环

收到用户确认或从 HTML 看板复制回传的勾选清单后，按以下铁律执行：

1. **强制运行 `preflight` 二次并发安全复查**：
   由于用户在网页上勾选到真正执行之间，可能有其他 AI 会话（Claude Code / Codex / Antigravity）刚启动了构建或修改了工作区，动手前必须运行：
   ```bash
   python3 scripts/full_stack_tool.py preflight \
     --config config/topology.local.json \
     --targets work/full-stack/current-snapshot.json \
     --ids "<用户勾选的逗号分隔ID>" \
     --output work/full-stack/preflight.json --overwrite
   ```
   - 凡是状态为 `BLOCKED_WHITELISTED`、`BLOCKED_RECENT_ACTIVITY`（最近 60 分钟内有文件变动）、`BLOCKED_OPEN_HANDLES`（`lsof` 发现进程占用）或 `BLOCKED_DIRTY_WORKTREE` 的条目，**一律自动跳过并在汇报中单列保护原因**。
   - 用户在卡片备注框里写了微调要求的（如「留最新 3 个版本」），严格按备注缩小操作范围。
2. **分域安全执行与跨层物理释放核验**：
   - 删除后必须核对 `df -h` 真实可用空间变化，不能只看 `du` 目录减少量。
   - 若删除数十 GB 后可用空间未明显上升，按 [四域巡检与跨层陷阱参考](references/domains-and-traps.md) 依次排查并回收：**APFS 本地快照锁死**（`tmutil thinlocalsnapshots`）、**PVE LXC 未回收块**（`pct fstrim <ctid>`）、**NAS SMB `#recycle` 回收站截留**、以及**iCloud 备份冗余 `.cms` 分块**（注意只对本地驻留块运行校验，防止触发云端回下风暴）。
3. **收尾更新看板与基线**：
   - 将已完成条目的 `status` 标为 `"done"`，重新渲染 HTML 看板供用户留存；
   - 若用户标记了新的「🛡️ 长期豁免」项，将其写入 `config/topology.local.json`；
   - 采集清理后的干净快照保存为新的 `work/full-stack/baseline-snapshot.json`，供下次巡检对比。
