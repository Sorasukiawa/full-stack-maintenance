# 更新记录 (Changelog)

版本号写在 `SKILL.md` 的 `metadata.version` 和 `.claude-plugin/plugin.json` 的 `version`，与本文件最上面的条目保持一致（`tests/check-skill.sh` 会自动检查）。

## 1.4.0 — 2026-11-12

按一次安全体检的结论收紧执行阶段，并让文档和脚本行为对齐。

- **`quarantine` 不再默认全量搬移**：`--ids` 改为必填；移动前对每个路径重跑与 `preflight` 相同的检查，只移判为安全的路径。Git pack 与 `.git` 内部路径、治理文档（`AGENTS.md` 一类）、启动项、各类更新检查、固件、备份规则漂移和 worktree 类候选一律拒绝，写进 `refused_items` 并说明原因；`preflight` 结果新增 `quarantine_allowed` / `quarantine_refusal`，两边用同一套判断。文档里不存在的 `--preflight` 参数和 `purge-quarantine` 子命令已删掉，示例改成真实可运行的命令。
- **隔离区外置盘核对真实挂载**：不再自动遍历 `/Volumes`（会把 Time Machine 备份卷、NAS 共享当外置盘）。配置的 `/Volumes/<盘名>` 根必须 `os.path.ismount` 为真且可写；`quarantine.root_dirs` 条目可写成 `{"path": ..., "volume_uuid": ...}`，用 `diskutil info -plist` 比对 `VolumeUUID`。不满足时退回 `~/.Cleanup_Quarantine/`，并在 `quarantine_root_fallback_reason` 写明原因。默认配置只保留本机隔离区。
- **APP 卸载默认 `keep_data`**：命令行、候选项（`selected_uninstall_mode`）和看板默认都只移走 `.app` 与缓存，`purge_all` 需明确选择。「最近使用」真正读取 Spotlight `kMDItemLastUsedDate`（`mdls`），取不到时退回文件修改时间，`last_used_basis` 与标题写明「N 天未打开」或「N 天无文件改动」。
- **合入前检查按 git-ship 规则识别**：`scan-local` 用与 `git-ship` 相同的行首正则判断「合入前检查：`命令`」/「合入前检查：无」，`AGENTS.md` 没写时再看 `CLAUDE.md`，写法认不出时单独提示；文档里的「合并前检查」改正。
- **会动备份和家里服务的回收改为 C 级**：`tmutil thinlocalsnapshots`、`pct fstrim`、NAS `#recycle` 清理（只针对本批次删除的条目，不整目录清空）、删除备份历史分块、`docker image prune -a`、卸载旧内核、PVE 演练克隆机和 Xcode `Unavailable` 模拟器（先按 `temporary-environments` 所有权登记核对）都只进看板等用户拍板。Git pack 建议改为普通 `git gc`；删掉 `git worktree move` 收拢建议；远端分支没合入的先推到 `archive/<原分支名>` 再删。
- **文档如实描述**：活跃会话保护只读 Codex 会话记录；worktree 类候选转交 `repo-cleanup`；`manage_subagents` 限定 Antigravity，Claude Code 的子代理工具写作 `Agent`；补充与 `mac-maintenance`、`git-ship`、`repo-cleanup`、`temporary-environments` 的分工；修正多宿主安装路径；删掉脚本不读的 `worktree_hubs`、`retention_days`、`worktree_active_hours`。
- **巡检产物统一放 `state/`**：快照、基线、看板和执行结果写在技能目录下的 `state/`（已加入 `.gitignore`）；公开文件里的 NAS 共享名和本机工作树路径换成占位名。

## 1.3.0 — 2026-11-07

- **Mac 全量 APP 体积/常用度统计与双模式智能卸载（`app_uninstall` + `app_inventory`）**：扫描 `/Applications` 与 `~/Applications`，结合 Spotlight `kMDItemLastUsedDate` 区分常用 APP 与久未使用 APP（默认 `>= 90` 天未用且 `>= 50 MiB`），分别统计 `.app` 本体体积（`app_bytes`）、用户数据体积（`data_bytes`）与缓存体积（`cache_bytes`）。在交互看板与 `preflight` / `quarantine` CLI 中支持「**一键全部删除（`purge_all`，含卸载残留）**」与「**保留用户数据（`keep_data`，仅删 App 本体与缓存）**」双模式实时切换与隔离暂存。
- **跨 8 类 Library 目录的深度卸载残留扫描与同源聚合（`app_orphan` 升级）**：将卸载残留排查范围扩展至 `Application Support`、`Caches`、`Containers`、`Group Containers`、`Saved Application State`、`Logs`、`HTTPStorages`、`WebKit`，自动剔除 UUID/Group 前缀与容器后缀，将同一已卸载应用散落在多处的残留目录合并为单张多路径（`paths`）候选卡片。
- **无用 Mac 启动项检查（`launch_item`）**：扫描 `~/Library/LaunchAgents`、`/Library/LaunchAgents`、`/Library/LaunchDaemons`，自动识别可执行文件缺失（`broken_executable`）、所属父应用已卸载（`orphan_app`）以及 `launchctl list` 非零退出码崩溃循环（`crash_loop`）的启动项。
- **Mac 全部 APP 更新与全栈开发依赖更新检查（`app_update` & `env_update`）**：整合 Homebrew Cask（`brew outdated --cask`）、Mac App Store（`mas outdated`）、Homebrew Formulae（`brew outdated --formula`）、全局 `npm` 包（`npm outdated -g`）与 `rustup` 工具链更新检查，并支持 `--skip-probes` 与 `probe_fixtures` 离线确定性测试。
- **外设与存储固件及健康度检查（`peripheral_firmware`）**：盘点内置/外置硬盘（NVMe/USB 存储固件版本与 SMART 健康状态）、SD 卡读卡器、雷电/USB 扩展坞（自动检测高速外设降速到 USB 2.0 `480 Mb/s`）与蓝牙/USB 键鼠（固件版本与 `<= 20%` 低电量提醒）。
- **全局可重建纯缓存检查（`system_cache`）**：扫描 `Xcode/DerivedData`、`Caches/Homebrew`、`~/.npm/_cacache`、`~/.cache/uv`、`~/.cache/pip`、`Caches/ms-playwright` 等大体积纯缓存（默认 `>= 200 MiB`），严格避开日常应用热缓存与登录态。

## 1.2.7 — 2026-11-06

- **SwiftUI `.contentTransition(.numericText())` 还原度升级与 KPI 单位防跳动**：逐位数字翻转采用单列 CSS Grid 原地叠放与字符基线锚点缩放（`transform-origin: 50% 82%` / `50% 18%`），配合退场位水平宽度弹簧坍缩、左对齐 KPI 尾部对齐（`padEnd`）与 `.nt-slot` `min-width: 0` 约束，消除右侧单位（`GiB` / `TiB`）在位数增减时的水平跳动。
- **侧栏「☀️ 浅色｜🌙 深色｜◐ 自动」胶囊外观切换器**：重构左下角外观切换控件，配备金黄色太阳、靛蓝弯月与半明半暗自动图标，激活滑块升格为圆润胶囊并随当前外观模式切换专属图标着色与光晕。
- **底部「快速选择」分类独立勾选与全选面板**：将底部悬浮栏弹出菜单升级为可独立开关的四分类勾选面板（`A 级 · 可安全重建`、`B 级 · 建议整理`、`C 级 · 需要你拍板`、`维护 · 优化、整理和更新`），支持全选绿勾 `✓`、部分选中蓝横杠 `−` 与未选空心圈三态动画、实时计数与体积汇总，并新增并排「全选全部 / 清空选择」快捷按钮。

## 1.2.6 — 2026-11-06

- **Apple `.contentTransition(.numericText())` 同款逐位数字变换动画**：新增 `setNumericText` 逐位比对滚轮引擎，数字变化时未变位（如相同数字与小数点）保持静止锐利，仅变化位按数值增减方向做纵向位移、高斯动态模糊（`blur`）与微缩淡入淡出过渡，并应用于概览圆环中心主数字、右侧四格 KPI、顶部存储卷小圆环百分比与底部 Dock 统计。
- **全站自定义毛玻璃弹簧下拉菜单**：新增 `setupPopupSelect` 组件，将工作台「切换存储卷」与「排序方式」的系统原生 `<select>` 菜单替换为与网站同款的毛玻璃弹出菜单（`.popup-menu`，带选中 `✓`、锚点缩放弹簧与键盘支持）；圆环分段起点圆头阴影严格裁剪在轨道环带内，消除深色模式外侧暗圈。

## 1.2.5 — 2026-11-05

- **全页面视觉网格对齐**：修复工作台列表行勾选框（`.check`）偏高问题，改为与 `.row-main` 内彩色图标、右侧体积与展开箭头严格垂直居中对齐；行分隔线（`.row + .row::before`）从 `50px` 移至正文与展开详情起点 `90px`；统一 `.row-main` 右内边距（`16px`）、`.group-head` 左右内边距（`4px`）、侧栏底部外观分段条与「全选本页」胶囊按钮右缘；修复概览「空间构成」图例空 `<em>` 占位缩进与「距上次巡检基线变化」双栏卡片行高差；工作台顶部存储卷影响卡片图例改为单行流式排布，去除底部多余空行。
- **UI 动画与圆环渲染打磨**：修复工作台动作筛选滑块在切换时被同步 `refresh()` 打断瞬移的问题，恢复 `SPRING.glide` 平滑滑动；为锥形渐变圆头留出纯色极角缓冲段并按弧长比例淡入圆头阴影，消除大圆环分段起点圆帽上的极角接缝线；为列表筛选/排序切换与展开详情内层（`.row-detail-inner`）补充可打断弹簧过渡与速度继承；补全概览白名单保护项悬停圆环高亮与工作台悬停跨存储卷自动联动。

## 1.2.4 — 2026-11-05

- 统一概览页存储卷切换时的下方布局与高度：`#hero` 内的挂载点行、说明行改为固定单行高度，「空间构成」图例改为固定的 2 列 × 3 行六槽网格并原地更新（不销毁重建 DOM），下方三组明细（「将释放空间／优化与整理／受保护」）固定结构与单行空状态占位，切换存储卷时页面与圆环不再上下跳动。

## 1.2.3 — 2026-11-05

- 修复切换存储卷小圆环卡片后卡片底部出现横向亮条的问题：去掉静态卡片上多余的 `backdrop-filter`（避免下方大卡片 30px 毛玻璃采样框越界切入上方存储卷卡片底部），并在 `popScale` 与入场动画结束后清理残留 `transform` 图层；去掉卡片顶部重复叠加的 `inset 0 1px 0` 内高光。

## 1.2.2 — 2026-11-05

- 「空间构成」条与工作台水位条改为药丸形：每一段都是独立胶囊，段间留空隙，宽度为零的段自动隐藏，避免多出空隙。

## 1.2.1 — 2026-11-05

- 概览圆环改回单环分段显示（灰色清理后已用、绿青渐变将释放、蓝色悬停项，圆心显示可释放大小），只保留活动圆环的外观：canvas 绘制的圆头粗环、沿弧渐变、后一段的圆头压在前一段上并带柔和阴影；存储卷小圆环同款。右侧恢复四格数据。

## 1.2.0 — 2026-11-05

- **活动圆环**：概览大圆环改为参考 Apple 健康的三层同心环（外圈清理前水位、中圈清理后水位、内圈已选可回收比例），用 canvas 绘制圆头与沿弧渐变，接近满圈时末端带阴影；存储卷切换条的小圆环同步改为三环。
- 右侧指标改为三格大号圆体数字，与三个圆环一一对应；悬停明细时内圈叠白线标出该项占比，圆环下方显示名称与大小。
- 大标题、卷标题与分区标题加粗（苹方最粗只有 Semibold，额外用细描边加重字形）。

## 1.1.0 — 2026-11-05

### 看板重新设计

- **macOS 应用式界面**：`scripts/render_dashboard.py` 重写为毛玻璃侧栏、系统设置风格图标块、大标题、内嵌分组列表与悬浮玻璃操作栏的布局，默认跟随系统浅色／深色外观，可手动切换「自动／浅色／深色」。
- **存储环形总览**：参考 CleanMyMac 与 macOS「存储空间」，首屏大圆环显示清理后已用、将释放与悬停项占比，配存储卷小圆环切换条与分段「空间构成」条。
- **弹簧动效**：所有交互动效改为可打断、保留速度的弹簧物理（Apple 阻尼比 + 响应时间参数），CSS 过渡使用 Python 预计算的 `linear()` 弹簧曲线；遵循减少动态效果、降低透明度与增强对比度设置。
- **工作台**：侧栏按领域分页，按风险等级分组、按动作类型筛选；行内展开查看依据、填写微调备注或加入白名单；快速选择菜单的「恢复初始勾选」改为二次确认。
- 去掉看板里写死的个人存储卷名称与路径分类规则，标题统一从快照 `label` 推导；导出指令格式、`localStorage` 键名与数据结构保持不变。
- README 预览图换成用虚构演示数据渲染的新看板截图（`assets/dashboard-preview.png`）。

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
