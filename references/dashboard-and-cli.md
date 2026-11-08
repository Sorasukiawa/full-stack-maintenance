# 看板与脚本契约（CLI & JSON Schema）

本技能附带两个仅依赖 Python 3.10+ 标准库的脚本：
- `scripts/full_stack_tool.py`：只读本地扫描（`scan-local`）、前后基线快照对比（`diff`）、以及动手前二次并发安全复查（`preflight`）。
- `scripts/render_dashboard.py`：将巡检快照与基线对比渲染成单文件交互式 HTML 看板。

两个脚本均**不执行任何删除、移动、卸载或远端修改操作**。

---

## 1. 拓扑与白名单配置（`config/topology.json` / `topology.local.json`）

脚本通过 `--config` 显式接收配置文件路径，采用 **Fail-Closed** 校验：只要文件缺失、JSON 语法损坏、`version != 1` 或 `whitelist` 字段类型不对，立即返回退出码 `2` 并拒绝继续。

```json
{
  "version": 1,
  "workspace_roots": ["~/Projects"],
  "worktree_hubs": ["~/orca/workspaces", "~/.codex/worktrees"],
  "app_roots": ["/Applications", "~/Applications"],
  "library_roots": [
    "~/Library/Application Support",
    "~/Library/Caches",
    "~/Library/Containers"
  ],
  "thresholds": {
    "min_orphan_mb": 50,
    "min_git_pack_mb": 100,
    "min_build_artifact_mb": 100,
    "recent_activity_minutes": 60
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
python3 scripts/full_stack_tool.py scan-local \
  --config config/topology.local.json \
  --output work/full-stack/current-snapshot.json --overwrite
```

- 自动采集 `storage_gauges` 的当前容量、已用、可用与使用率；
- 扫描 `app_roots` 并结合 Spotlight `kMDItemLastUsedDate` 与 `library_roots`，输出全量应用清单 `app_inventory`（区分 `frequent` 常用应用与 `infrequent` 久未使用应用，统计 `app_bytes`、`data_bytes`、`cache_bytes`、`total_bytes`），并对久未使用应用生成带 `uninstall_modes`（`purge_all` 一键全删含残留 / `keep_data` 保留用户数据）的卸载候选（`category: "app_uninstall"`，`tier: "C"`）；
- 扫描 `library_roots`（涵盖 `Application Support`、`Caches`、`Containers`、`Group Containers`、`Saved Application State`、`Logs`、`HTTPStorages`、`WebKit`）并与已安装应用的 `Info.plist` 做交叉比对，将同一已卸载应用的多处残留聚合成 `>= min_orphan_mb` 的多路径候选项（`category: "app_orphan"`，`tier: "C"`，含 `paths` 数组）；
- 扫描 `launch_agent_roots` 下失效或崩溃的 macOS 启动项（`category: "launch_item"`），检查 Mac 应用更新（`category: "app_update"`）、开发依赖与工具链更新（`category: "env_update"`，同时输出 `environments` 汇总）、外设与存储固件健康度（`category: "peripheral_firmware"`，同时输出 `peripherals` 汇总）以及大体积全局开发纯缓存（`category: "system_cache"`，`tier: "A"`）；
- 扫描 `workspace_roots` 下所有 Git 仓库的 `.git/objects/pack` 膨胀情况（`category: "git_pack_bloat"`，`tier: "B"`）及 `AGENTS.md` 合并前检查声明；
- 扫描大型可重建构建目录（`category: "build_artifact"`，`tier: "A"`），并对照 `backup_pipelines` 检测是否存在未被备份排除规则覆盖的目录（`category: "backup_exclusion_drift"`，`tier: "B"`）；
- 支持 `--skip-probes` 跳过宿主 CLI 探针（离线/单元测试场景），或通过配置中的 `probe_fixtures` 注入确定性测试夹具；
- 代理可将四路子代理查出的 GitHub 远端候选项（`domain: "github"`）与 NAS/PVE 候选项（`domain: "nas"`）追加到 `candidates` 与 `storage_gauges` 数组中，再交由 `render_dashboard.py` 统一渲染。

---

## 3. 前后基线快照对比（`diff`）

```bash
python3 scripts/full_stack_tool.py diff \
  --before work/full-stack/baseline-snapshot.json \
  --after work/full-stack/current-snapshot.json \
  --output work/full-stack/diff.json --overwrite
```

输出字段：
- `gauge_deltas`：各存储池在两次巡检之间的已用字节变化（`used_delta_bytes`）、可用字节变化（`free_delta_bytes`）与百分比变化；
- `top_growth_paths`：按体积增长量从大到小排序的目录列表（含 `before_bytes`、`after_bytes`、`delta_bytes`、`is_new`），直接回答「距离上次巡检是谁偷偷占了空间」；
- `new_candidate_ids` / `resolved_candidate_ids`：新增与已消除的候选项 ID。

---

## 4. 渲染单文件交互式 HTML 看板（`render_dashboard.py`）

```bash
python3 scripts/render_dashboard.py \
  --input work/full-stack/current-snapshot.json \
  --diff work/full-stack/diff.json \
  --title "全栈健康巡检与空间水位交互看板" \
  --output work/full-stack/inspection-dashboard.html --overwrite
```

看板内置特性（macOS 应用式布局：毛玻璃侧栏、大标题、内嵌分组列表、悬浮操作栏；默认跟随系统浅色／深色外观）：
1. **概览（默认视图 `ring-overview-page`）**：
   - 大圆环（画在 `ring-canvas` 上，样式参考 Apple 健康活动圆环：粗环、圆头、沿弧渐变）代表所选存储卷 100% 总容量：灰色段＝清理后保留的已用空间，绿青渐变段＝本次勾选将释放（或已执行释放）的空间，蓝色段＝悬停明细项的占比；圆心显示可释放字节数与水位变化；
   - 上方存储卷小圆环条（`mini-rings-strip`）切换单个存储卷或「全部存储卷」合计；右侧列出清理后已用、将释放、清理后可用与总容量，并用药丸形分段「空间构成」条展示 `breakdown`（缺省时按候选项中文分类标签汇总）；
   - 下方依次是「距上次巡检基线变化」与「将释放空间／优化与整理／受保护」三组明细，点击任意一行会跳到工作台对应条目并展开。
2. **勾选与微调工作台（`workbench-page`，侧栏切换领域）**：
   - 按 A／B／C 风险等级分组、按动作类型筛选与排序；每个候选项通过 `gauge_id` 关联存储卷，勾选、取消或悬停时顶部水位条实时显示对该卷的影响（未勾选项以斜纹预览）；
   - **APP 卸载双模式切换器（`.uninstall-mode-box`）**：当候选项包含 `uninstall_modes`（`purge_all` 与 `keep_data`）时，展开详情可直接切换「**一键全部删除（含残留）**」或「**保留用户数据（仅删 App 与缓存）**」，行内显示的体积、对应路径列表、顶部存储圆环释放预计以及导出的 `--uninstall-mode` 参数实时同步。
3. **动效**：圆环、数字、分段控件、侧栏选中块、行展开、快速选择菜单与导出面板均由可打断的弹簧驱动（参数为 Apple 的阻尼比 `damping` 与响应时间 `response`，预设见 `SPRING_PRESETS`）；CSS 过渡使用由同一组参数预计算的 `linear()` 弹簧曲线；遵循系统「减少动态效果」「降低透明度」「增强对比度」设置。
4. **`localStorage` 状态持久化**：勾选状态、APP 卸载模式（`uninstallModes`）、单项「微调备注」与「加入白名单」改动自动保存在浏览器 `localStorage`（键名 `full_stack_maint_<report_id>`），刷新不丢失；受限沙盒 `iframe` 中自动降级为仅本次会话有效。
5. **一键复制执行指令**：底部操作栏「生成执行指令」生成可直接粘贴回 AI 对话框的 Markdown 文本，包含勾选的 `id`、路径、卸载模式（`--uninstall-mode keep-data` / `purge-all`）、单项微调备注与新增白名单；格式保持稳定，便于代理解析。

---

## 5. 执行前二次并发安全复查（`preflight`）与隔离区暂存（`quarantine`）

在真正执行清理动作前，对用户勾选的条目运行 `preflight`（支持通过 `--uninstall-mode keep-data` 或 `purge-all` 指定 APP 卸载模式）：

```bash
python3 scripts/full_stack_tool.py preflight \
  --config config/topology.local.json \
  --targets work/full-stack/current-snapshot.json \
  --ids "build-app-target,orphan-oldapp,uninstall-oldgame" \
  --uninstall-mode keep-data \
  --output work/full-stack/preflight.json --overwrite
```

每个目标的 `status` 枚举含义：

| 状态值 | 含义与代理动作 |
|---|---|
| `SAFE_TO_EXECUTE` | 通过白名单、符号链接、最近修改时间、工作树整树豁免与打开句柄检查，可执行已授权操作 |
| `BLOCKED_WHITELISTED` | 命中 `whitelist.paths`（含父子路径或同设备 inode），强制跳过 |
| `BLOCKED_ACTIVE_WORKTREE` | 祖先 Git 工作树或 Codex 活跃会话在 `worktree_active_hours`（默认 48 小时）内有活动，**整棵工作树连带豁免**（含 `target`、`distribution/artifacts` 等全部子目录） |
| `BLOCKED_RECENT_ACTIVITY` | 目录内文件或 `.git` 索引/HEAD 存在最近 `recent_activity_minutes` 分钟内新增或修改，疑似其他 AI 会话正在并发使用，跳过保护 |
| `BLOCKED_OPEN_HANDLES` | `lsof` 检测到有活跃进程正打开该目录下的文件，跳过保护 |
| `BLOCKED_DIRTY_WORKTREE` | 目标含 `.git`（仓库或 worktree）且含有未提交改动或未跟踪文件，跳过保护 |
| `BLOCKED_SYMLINK_ROOT` | 目标根路径本身是符号链接，拒绝自动递归清理 |
| `MISSING_ALREADY_DONE` | 目标路径已不存在，无需重复操作 |

- **多子路径聚合卡片与卸载模式支持（`paths` / `uninstall_modes`）**：若候选项包含 `paths: ["/path/a", "/path/b"]` 或 `uninstall_modes`，`preflight` 会按生效的卸载模式（`purge_all` 或 `keep_data`）解析目标路径列表并逐一检查每个子路径，返回 `safe_paths`、`blocked_paths`、`uninstall_mode` 与 `sub_results`；调用方仅对 `safe_paths` 执行清理并自动跳过 `blocked_paths`。
- **闲置项目构建产物与待卸载应用移入隔离区暂存（`quarantine` / `purge-quarantine`）**：
  ```bash
  # 将通过 preflight 的闲置构建产物、卸载残留或待卸载应用移入外置盘隔离区并生成 restore.sh
  python3 scripts/full_stack_tool.py quarantine \
    --config config/topology.local.json \
    --preflight work/full-stack/preflight.json \
    --uninstall-mode keep-data \
    --output work/full-stack/quarantine-result.json --overwrite

  # 待用户确认无误或超过保留天数后彻底清空隔离区批次
  python3 scripts/full_stack_tool.py purge-quarantine \
    --config config/topology.local.json --all \
    --output work/full-stack/purge-result.json --overwrite
  ```
