# 看板与脚本契约（CLI & JSON Schema）

本技能附带两个仅依赖 Python 3.10+ 标准库的脚本：
- `scripts/full_stack_tool.py`：只读本地扫描（`scan-local`）、前后基线快照对比（`diff`）、动手前二次并发安全复查（`preflight`），以及把用户点名且复查通过的路径移入隔离区（`quarantine`）。
- `scripts/render_dashboard.py`：将巡检快照与基线对比渲染成单文件交互式 HTML 看板。

除 `quarantine` 会**移动**文件（只移动、不删除，并生成还原脚本）外，两个脚本都不执行删除、移动、卸载或远端修改操作。所有输出都写到 `--output` 指定的位置；下文示例统一写在技能目录下被 Git 忽略的 `state/`，基线快照固定为 `state/baseline-snapshot.json`。

---

## 1. 拓扑与白名单配置（`config/topology.json` / `topology.local.json`）

脚本通过 `--config` 显式接收配置文件路径，采用 **Fail-Closed** 校验：只要文件缺失、JSON 语法损坏、`version != 1`、`whitelist` 字段类型不对，或 `quarantine.root_dirs` 条目写错，立即返回退出码 `2` 并拒绝继续。脚本不读的键会被忽略，下面只列脚本实际读取的键。

```json
{
  "version": 1,
  "workspace_roots": ["~/Projects"],
  "app_roots": ["/Applications", "~/Applications"],
  "library_roots": [
    "~/Library/Application Support",
    "~/Library/Caches",
    "~/Library/Containers"
  ],
  "thresholds": {
    "min_orphan_mb": 50,
    "min_app_size_mb": 50,
    "unused_app_days": 90,
    "min_cache_mb": 200,
    "min_git_pack_mb": 100,
    "min_build_artifact_mb": 100,
    "recent_activity_minutes": 60,
    "worktree_protect_minutes": 2880
  },
  "quarantine": {
    "root_dirs": [
      {"path": "/Volumes/ExternalSSD/.Cleanup_Quarantine", "volume_uuid": "00000000-0000-0000-0000-000000000000"},
      "~/.Cleanup_Quarantine"
    ]
  },
  "storage_gauges": [
    {
      "id": "mac-data",
      "label": "Mac Data Volume",
      "mount_path": "/System/Volumes/Data",
      "warn_used_pct": 80.0
    }
  ],
  "backup_pipelines": [
    {
      "id": "cloud-backup",
      "label": "Cloud Incremental Backup",
      "script_path": "~/.local/share/example-backup/backup.py",
      "watched_roots": ["~/Projects"],
      "expected_exclude_globs": ["target", "build", ".build*", "DerivedData*", "*-builds", "*-preservation"]
    }
  ],
  "homelab": {
    "enabled": false,
    "pve_ssh_host": "",
    "nas_mounts": [],
    "docker_hosts": []
  },
  "whitelist": {
    "paths": ["~/Projects/keep-forever"],
    "repos": ["owner/pinned-repo"],
    "app_names": ["LegacyAudioPlugin"],
    "guest_ids": ["111"],
    "notes": {
      "~/Projects/keep-forever": "保留验收报告，不自动清理"
    }
  }
}
```

---

## 2. 本机只读扫描（`scan-local`）

```bash
mkdir -p state
python3 scripts/full_stack_tool.py scan-local \
  --config config/topology.local.json \
  --output state/current-snapshot.json --overwrite
```

- 自动采集 `storage_gauges` 的当前容量、已用、可用与使用率；
- 扫描 `app_roots` 与 `library_roots`，输出全量应用清单 `app_inventory`（区分 `frequent` 常用应用与 `infrequent` 久未使用应用，统计 `app_bytes`、`data_bytes`、`cache_bytes`、`total_bytes`）。「最近使用」取 Spotlight `kMDItemLastUsedDate`（`mdls`，只在扫描真实宿主时调用）与文件最近改动中较晚的一个，字段 `last_opened_ts`、`last_modified_ts`、`last_used_basis`（`spotlight` / `file_mtime`）说明依据；测试可用 `probe_fixtures.app_last_used`（`{应用路径: 时间}`）注入。对久未使用应用生成带 `uninstall_modes`（默认 `keep_data` 保留用户数据 / `purge_all` 一键全删含残留）的卸载候选（`category: "app_uninstall"`，`tier: "C"`，`selected_uninstall_mode: "keep_data"`），标题写「N 天未打开」或「N 天无文件改动」；
- 扫描 `library_roots`（涵盖 `Application Support`、`Caches`、`Containers`、`Group Containers`、`Saved Application State`、`Logs`、`HTTPStorages`、`WebKit`）并与已安装应用的 `Info.plist` 做交叉比对，将同一已卸载应用的多处残留聚合成 `>= min_orphan_mb` 的多路径候选项（`category: "app_orphan"`，`tier: "C"`，含 `paths` 数组）；
- 扫描 `launch_agent_roots` 下失效或崩溃的 macOS 启动项（`category: "launch_item"`），检查 Mac 应用更新（`category: "app_update"`）、开发依赖与工具链更新（`category: "env_update"`，同时输出 `environments` 汇总）、外设与存储固件健康度（`category: "peripheral_firmware"`，同时输出 `peripherals` 汇总）以及大体积全局开发纯缓存（`category: "system_cache"`，`tier: "A"`）；
- 扫描 `workspace_roots` 下所有 Git 仓库的 `.git/objects/pack` 膨胀情况（`category: "git_pack_bloat"`，`tier: "B"`，建议普通 `git gc`）及合入前检查声明：先读 `AGENTS.md`，没写再读 `CLAUDE.md`，用与 `git-ship` 相同的行首正则判断 `合入前检查：\`命令\`` / `合入前检查：无`，缺失或写法认不出时生成 `governance_doc` 候选；
- 扫描大型可重建构建目录（`category: "build_artifact"`，`tier: "A"`），并对照 `backup_pipelines` 检测是否存在未被备份排除规则覆盖的目录（`category: "backup_exclusion_drift"`，`tier: "B"`）；
- 支持 `--skip-probes` 跳过宿主 CLI 探针（离线/单元测试场景），或通过配置中的 `probe_fixtures` 注入确定性测试夹具；
- 代理可将四路子代理查出的 GitHub 远端候选项（`domain: "github"`）与 NAS/PVE 候选项（`domain: "nas"`）追加到 `candidates` 与 `storage_gauges` 数组中，再交由 `render_dashboard.py` 统一渲染。

---

## 3. 前后基线快照对比（`diff`）

```bash
python3 scripts/full_stack_tool.py diff \
  --before state/baseline-snapshot.json \
  --after state/current-snapshot.json \
  --output state/diff.json --overwrite
```

输出字段：
- `gauge_deltas`：各存储池在两次巡检之间的已用字节变化（`used_delta_bytes`）、可用字节变化（`free_delta_bytes`）与百分比变化；
- `top_growth_paths`：按体积增长量从大到小排序的目录列表（含 `before_bytes`、`after_bytes`、`delta_bytes`、`is_new`），直接回答「距离上次巡检是谁偷偷占了空间」；
- `new_candidate_ids` / `resolved_candidate_ids`：新增与已消除的候选项 ID。

---

## 4. 渲染单文件交互式 HTML 看板（`render_dashboard.py`）

```bash
python3 scripts/render_dashboard.py \
  --input state/current-snapshot.json \
  --diff state/diff.json \
  --title "全栈健康巡检与空间水位交互看板" \
  --output state/inspection-dashboard.html --overwrite
```

看板内置特性（macOS 应用式布局：毛玻璃侧栏、大标题、内嵌分组列表、悬浮操作栏；默认跟随系统浅色／深色外观）：
1. **概览（默认视图 `ring-overview-page`）**：
   - 大圆环（画在 `ring-canvas` 上，样式参考 Apple 健康活动圆环：粗环、圆头、沿弧渐变）代表所选存储卷 100% 总容量：灰色段＝清理后保留的已用空间，绿青渐变段＝本次勾选将释放（或已执行释放）的空间，蓝色段＝悬停明细项的占比；圆心显示可释放字节数与水位变化；
   - 上方存储卷小圆环条（`mini-rings-strip`）切换单个存储卷或「全部存储卷」合计；右侧列出清理后已用、将释放、清理后可用与总容量，并用药丸形分段「空间构成」条展示 `breakdown`（缺省时按候选项中文分类标签汇总）；
   - 下方依次是「距上次巡检基线变化」与「将释放空间／优化与整理／受保护」三组明细，点击任意一行会跳到工作台对应条目并展开。
2. **勾选与微调工作台（`workbench-page`，侧栏切换领域）**：
   - 按 A／B／C 风险等级分组、按动作类型筛选与排序；每个候选项通过 `gauge_id` 关联存储卷，勾选、取消或悬停时顶部水位条实时显示对该卷的影响（未勾选项以斜纹预览）；
   - **APP 卸载双模式切换器（`.uninstall-mode-box`）**：当候选项包含 `uninstall_modes`（`purge_all` 与 `keep_data`）时，展开详情可直接切换「**保留用户数据（仅删 App 与缓存）**」（默认）或「**一键全部删除（含残留）**」，行内显示的体积、对应路径列表、顶部存储圆环释放预计以及导出的 `--uninstall-mode` 参数实时同步。
3. **动效**：圆环、数字、分段控件、侧栏选中块、行展开、快速选择菜单与导出面板均由可打断的弹簧驱动（参数为 Apple 的阻尼比 `damping` 与响应时间 `response`，预设见 `SPRING_PRESETS`）；CSS 过渡使用由同一组参数预计算的 `linear()` 弹簧曲线；遵循系统「减少动态效果」「降低透明度」「增强对比度」设置。
4. **`localStorage` 状态持久化**：勾选状态、APP 卸载模式（`uninstallModes`）、单项「微调备注」与「加入白名单」改动自动保存在浏览器 `localStorage`（键名 `full_stack_maint_<report_id>`），刷新不丢失；受限沙盒 `iframe` 中自动降级为仅本次会话有效。
5. **一键复制执行指令**：底部操作栏「生成执行指令」生成可直接粘贴回 AI 对话框的 Markdown 文本，包含勾选的 `id`、路径、卸载模式（`--uninstall-mode keep-data` / `purge-all`）、单项微调备注与新增白名单；格式保持稳定，便于代理解析。

---

## 5. 执行前二次并发安全复查（`preflight`）与隔离区暂存（`quarantine`）

在真正执行清理动作前，对用户勾选的条目运行 `preflight`（APP 卸载模式默认 `keep-data`，只有用户明确选择时才传 `--uninstall-mode purge-all`）：

```bash
python3 scripts/full_stack_tool.py preflight \
  --config config/topology.local.json \
  --targets state/current-snapshot.json \
  --ids "build-app-target,orphan-oldapp,app-uninstall-com-example-oldgame" \
  --uninstall-mode keep-data \
  --output state/preflight.json --overwrite
```

每个目标的 `status` 枚举含义：

| 状态值 | 含义与代理动作 |
|---|---|
| `SAFE_TO_EXECUTE` | 通过白名单、符号链接、最近修改时间、工作树整树豁免与打开句柄检查，可执行已授权操作 |
| `BLOCKED_WHITELISTED` | 命中 `whitelist.paths`（含父子路径或同设备 inode），强制跳过 |
| `BLOCKED_ACTIVE_WORKTREE` | 祖先 Git 工作树在 `worktree_protect_minutes`（默认 2880 分钟，即 48 小时）内被 Codex 会话记录（`~/.codex/sessions/**/*.jsonl`）提到，**整棵工作树连带豁免**（含 `target`、`distribution/artifacts` 等全部子目录）。只读 Codex 会话；Claude Code、Antigravity、OpenCode 的会话不读，靠下面几项间接保护 |
| `BLOCKED_RECENT_ACTIVITY` | 目标目录内文件或 `.git` 索引/HEAD 在 `recent_activity_minutes` 分钟内有改动，或所在工作树在 `worktree_protect_minutes` 内有改动，疑似其他 AI 会话正在并发使用，跳过保护 |
| `BLOCKED_OPEN_HANDLES` | `lsof` 检测到有活跃进程正打开该目录下的文件，跳过保护 |
| `BLOCKED_DIRTY_WORKTREE` | 目标所在仓库或 worktree 含有未提交改动或未跟踪文件，跳过保护 |
| `BLOCKED_SYMLINK_ROOT` | 目标根路径本身是符号链接，拒绝自动递归清理 |
| `MISSING_ALREADY_DONE` | 目标路径已不存在，无需重复操作 |

- **多子路径聚合卡片与卸载模式支持（`paths` / `uninstall_modes`）**：若候选项包含 `paths: ["/path/a", "/path/b"]` 或 `uninstall_modes`，`preflight` 会按生效的卸载模式（命令行 `--uninstall-mode` > 候选项 `selected_uninstall_mode` > 默认 `keep_data`）解析目标路径列表并逐一检查每个子路径，返回 `safe_paths`、`blocked_paths`、`uninstall_mode` 与 `sub_results`；调用方仅对 `safe_paths` 执行清理并自动跳过 `blocked_paths`。
- **能不能进隔离区（`quarantine_allowed` / `quarantine_refusal`）**：每条结果都带 `quarantine_allowed`，判断规则和 `quarantine` 完全相同；为 `false` 时 `quarantine_refusal` 写明原因。

### 隔离区暂存（`quarantine`）

`quarantine` 是两个脚本里唯一会移动文件的子命令，只移动、不删除：

```bash
# 先预演：看会移哪些路径、拒绝哪些、隔离根选在哪
python3 scripts/full_stack_tool.py quarantine \
  --config config/topology.local.json \
  --targets state/current-snapshot.json \
  --ids "build-app-target,app-uninstall-com-example-oldgame" \
  --uninstall-mode keep-data \
  --dry-run \
  --output state/quarantine-plan.json --overwrite

# 核对无误后去掉 --dry-run 执行（输出另存一份结果）
python3 scripts/full_stack_tool.py quarantine \
  --config config/topology.local.json \
  --targets state/current-snapshot.json \
  --ids "build-app-target,app-uninstall-com-example-oldgame" \
  --uninstall-mode keep-data \
  --output state/quarantine-result.json --overwrite
```

- **`--ids` 必填**：不给或只给空白时报错（argparse 返回 2），不会默认处理全部候选；计划里找不到的 ID 列在 `unknown_ids`。
- **移动前复查**：对每个路径重跑与 `preflight` 相同的检查（`evaluate_single_target`），只移 `SAFE_TO_EXECUTE` 的路径；被拦的进 `blocked_items`，已不存在的进 `skipped_items`。
- **永远拒绝（`refused_items`，状态 `REFUSED_NOT_MOVABLE`）**：`git_pack_bloat`、`governance_doc`、`launch_item`、`app_update`、`env_update`、`peripheral_firmware`、`backup_exclusion_drift` 类候选；`action_type` 为 `optimize` / `update` 的候选；分类或 `kind` 带 `worktree` 的候选（交给 `repo-cleanup`）；以及路径层面的 `.git` 内部路径、`AGENTS.md` / `CLAUDE.md` / `GEMINI.md` / `README.md` / `REVIEW.md` / `CONTRIBUTING.md`、`LaunchAgents` / `LaunchDaemons` 下的 `.plist`、本身就是仓库或 worktree 根的目录、远端或非文件系统路径。一条候选里只要有一个路径被拒绝，整条都不移。
- **隔离根选择**：按 `quarantine.root_dirs` 的顺序取第一个可用的根，不自动遍历 `/Volumes`。`/Volumes/<盘名>/...` 形式的根要求 `/Volumes/<盘名>` 是真实挂载点（`os.path.ismount`）且可写；条目写成 `{"path": ..., "volume_uuid": ...}` 时还会用 `diskutil info -plist <挂载点>` 比对 `VolumeUUID`。都不可用或没配置时退回 `~/.Cleanup_Quarantine/`。输出里的 `quarantine_root_checks` 记录每个根的检查结果，`quarantine_root_fallback_reason` 写明为什么没用外置盘。`--quarantine-dir` 显式指定的 `/Volumes` 路径没挂载时直接报错（返回 2）。
- **产物**：非预演且有路径被移动时，在 `<隔离根>/<批次>/` 下写 `manifest.json` 和 `restore.sh`（按原路径逐条移回）。脚本不提供清空隔离区的命令；用户确认各任务正常后，由代理列出批次内容和体积，用户同意后再删批次目录。
