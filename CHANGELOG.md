# 更新记录 (Changelog)

版本号写在 `SKILL.md` 的 `metadata.version` 和 `.claude-plugin/plugin.json` 的 `version`，与本文件最上面的条目保持一致（`tests/check-skill.sh` 会自动检查）。

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
