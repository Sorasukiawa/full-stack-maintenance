# 全栈巡检与看板技能开发约定

技能入口为 `SKILL.md`，四域巡检与跨层陷阱见 `references/domains-and-traps.md`，脚本与 JSON 契约见 `references/dashboard-and-cli.md`。源码仓库与个人安装实例分别维护，个人拓扑与白名单放在 Git 忽略的 `config/topology.local.json` 中。

- Python 脚本仅使用 Python 3.10+ 标准库；回归测试使用隔离临时夹具，不扫描或修改真实宿主目录。
- `scripts/full_stack_tool.py` 与 `scripts/render_dashboard.py` 只做采集、对比、看板渲染与执行前并发安全复核（`preflight`），不直接删除文件、卸载软件或销毁容器。
- 配置文件解析采用 fail-closed 策略：显式传入或检测到的配置文件格式损坏、版本不兼容或白名单字段非法时立即报错退出（返回码 2），不降级成无白名单放行。
- 公开文件（`SKILL.md`、`README*`、`config/topology.json`、`config/topology.example.json`）不包含真实 IP、主机名、账号或个人目录；四语言介绍保持事实一致。
- 合入前检查：`python3 -B -m unittest discover -s tests -q`（使用隔离夹具验证孤儿检测、基线对比、并发安全复核与看板生成）。
