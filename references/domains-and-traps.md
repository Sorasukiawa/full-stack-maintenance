# 四域深度巡检清单与五大跨层存储陷阱

本参考文档总结了跨 **Mac 本机、本地多仓库工作区、GitHub 全账号远端、NAS/PVE 家庭服务器** 联合巡检时的检查要点，以及实战中最容易踩坑的 5 个跨层连带病灶。

---

## 一、四域并行只读巡检清单

### 1. 💻 Mac 本机与自动备份管线（配合 `$mac-maintenance`）
- **核心水位**：`/System/Volumes/Data` 总量、已用、可用及 APFS 容器 `Purgeable`（可清除）空间。
- **开发缓存重灾区**：
  - `~/Library/Developer/Xcode/DerivedData`（含空壳目录与过期工程构建缓存）；
  - `xcrun simctl list devices unavailable`（已淘汰 iOS 版本的 `Unavailable` 模拟器运行时与设备沙盒）；
  - Rust `target/`、Swift `.build*`、前端 `node_modules/.cache`、Playwright `ms-playwright` 旧内核、桌面端应用 Updater 缓存（如 `*-updater`、旧版本 `claude-code` 二进制）。
- **孤儿应用残留交叉比对（Orphan Cross-Referencing）**：
  - 将 `~/Library/Application Support`、`~/Library/Caches`、`~/Library/Containers` 与当前已安装的 `/Applications`、`~/Applications` 及 Homebrew 软件包做 Bundle ID / 规范化名称交叉比对；
  - 筛出「主程序已卸载、但残留 ≥50 MiB 数据」的目录，统一归为 **C 级（需用户拍板确认）**，不自动当作普通缓存删除。
- **软件更新**：运行 `brew outdated` 检查过期 CLI 工具与库，升级后执行 `brew cleanup`。

### 2. 📂 本地项目群与多宿主 Worktree（配合 `$repo-cleanup` & `$codex-maintenance`）
- **多宿主 Worktree 盘点**：
  - 统一扫描仓库内嵌的 `.claude/worktrees/*`、`.worktrees/*`、以及集中式工作区 `~/orca/workspaces/*`、`~/.codex/worktrees/*`；
  - 退役门槛：无未提交修改、无未跟踪文件、无唯一未合入提交（分支已合入 `main`）、且无活跃进程占用；平级散落的活跃 worktree 通过 `git worktree move` 收拢进主仓库 `.worktrees/` 下。
- **`.git/objects/pack` 膨胀体检**：
  - 检查各仓库 `.git/objects/pack` 体积；若超过阈值（默认 100 MiB）且远大于源码体积，标记为 `git_pack_bloat`（B 级），在无并发写入时用 `git gc --prune=now` 压缩。
- **工程规范对齐**：
  - 检查每个 Git 仓库是否具备 `AGENTS.md`（且包含符合 `git-ship` 解析要求的 `合入前检查：\`命令\`` 或 `合入前检查：无`）以及 `CLAUDE.md`（`@AGENTS.md`）；
  - 检查工作区根目录的索引 `README.md` 是否与实际目录树一致。

### 3. 🐙 GitHub 全账号远端治理
- **全量仓库枚举**：使用 `gh repo list --limit 100 --json name,isPrivate,isArchived,pushedAt,defaultBranchRef` 盘点所有公开与私有仓库。
- **远端陈旧分支与 Checkpoint 清理**：
  - 识别自动化工具留下的海量 `checkpoints/*` 分支或早已合入主干的陈旧 PR 分支；
  - 删除远端分支前记录 `branch_name -> commit_sha` 映射，便于随时恢复。
- **CI 状态甄别**：
  - 使用 `gh run list` 查看近期失败的工作流；区分「已被后续提交修复的旧报错 / 浏览器 E2E 偶发竞态」与「当前 `main` 分支真实断裂」。
- **仓库归档与纳管**：
  - 对已完全合入新项目、不再独立维护的旧仓库，列为 C 级候选建议 `gh repo archive`；
  - 对本地已在积极开发但尚未初始化 Git 或未关联 GitHub 远端的工具目录，建议补齐 `.gitignore`、`AGENTS.md` 并推送私有仓库备份。

### 4. 🏠 NAS 与 PVE 家庭服务器（Homelab）
- **Proxmox VE 节点与存储池**：
  - `/boot/efi`（ESP 分区）：检查是否被旧版 `proxmox-kernel-*` 镜像或误放的主板 BIOS `.CAP` 文件塞满（水位 >80% 会导致下次内核升级失敗）；卸载旧内核时**永远保留当前运行内核与最新 2 个版本**；
  - `pve-root`：排查 `/root/` 或 `/var/tmp/` 下的一次性维护备份、旧归档包；
  - `local-lvm` (`pve/data` thin-pool)：检查 `lvs` 的 `Data%` 与 `Meta%`；排查各 VM/CT 配置中长期未挂载的 `unused0`/`unused1` 孤儿卷，以及停机数月的一次性灾难恢复演练克隆机（如 `*-dr-test`）。
- **VM 与 LXC 容器内部**：
  - Docker 宿主 VM：检查 `docker system df`，清理悬空镜像与膨胀的 `Build Cache`（`docker builder prune -af` + `docker image prune -af`），同时检查是否存在 `unhealthy` 或 `Restarting` 循环的容器；
  - LXC 容器：清理内部旧升级备份后，必须在宿主机执行 `pct fstrim <ctid>` 将释放的块交还给 thin-pool。
- **NAS 存储池与备份策略**：
  - 检查各卷（如 Btrfs `/volume1`、NVMe `/volume3`）可用空间是否满足安全红线（如高速缓存池保留 ≥450 GiB）；
  - 检查 `vzdump` 虚拟机每日全量备份的保留策略（`--prune-backups keep-daily=...,keep-weekly=...`），防止高频大体积 VM 备份撑爆 NVMe 池。

---

## 二、五大跨层连带存储陷阱（实战必查）

### 陷阱 1：本地目录新建/改名 ➔ 穿透备份排除规则 ➔ 撑爆 iCloud / NAS
- **现象**：在本地新建了构建归档目录（如 `*-builds`、`*-preservation`、`Artifacts`）或改了目录名，结果后台定时运行的云端加密备份脚本或 NAS 增量同步脚本因为只写了固定路径排除，把几十 GB 的二进制构建包和 `.o`/`.rlib` 编译中间产物全量切片上传到了 iCloud Drive 或 NAS。
- **治本对策**：
  1. 用 `full_stack_tool.py scan-local` 自动比对本地大体积构建目录与 `backup_pipelines` 的排除通配符；
  2. 将备份脚本升级为**「目录通配模式 + 二进制产物后缀拦截」双层防呆**（例如在项目根下拦截 `DerivedData*`、`.build*`、`*-builds`、`*-preservation`、`Artifacts`、`distribution`，并拦截 `.app`、`.xcarchive`、`.dSYM`、`.framework`、`.ipa`、`.dmg`、`.pkg`、`.o`、`.rlib`）；
  3. 生成最新干净快照后，删除包含膨胀目录的历史快照并回收未被引用的孤儿数据块。

### 陷阱 2：Mac 删了几十 GB 文件，但 `df -h` 可用空间几乎没涨（APFS 本地快照锁死）
- **现象**：清理完大量缓存、模拟器与构建目录后，`du` 统计确实少了 50+ GiB，但 `df -h /System/Volumes/Data` 显示可用空间只多了不到 1 GiB。
- **根因与对策**：
  - macOS 的 Time Machine 本地快照（`com.apple.TimeMachine.*.local`）或系统更新前的 APFS 快照仍引用着被删文件的数据块，物理空间不会释放，直到快照过期或被瘦身；
  - 先用 `tmutil listlocalsnapshots /` 查验，确认存在大容量历史本地快照后，执行：
    ```bash
    tmutil thinlocalsnapshots /System/Volumes/Data 100000000000 4
    ```
    即可立即回收被快照锁住的数十至上百 GiB 物理空间。

### 陷阱 3：校验或统计 iCloud 目录时触发「云端回下风暴」或 Spotlight IPC 超时
- **现象**：
  1. 对 iCloud Drive（`Mobile Documents`）下的几十万个备份分块逐一运行解密校验或读取文件内容，结果把原本已经 Evict（仅保留云端、不占本机空间）的历史分块重新下载回本机，导致磁盘瞬间爆满；
  2. 对 20 万+ 文件的 iCloud 目录全量查询 `NSMetadataItem` 上传进度，导致 Spotlight IPC 600 秒超时失败。
- **根因与对策**：
  - 检查 iCloud 分块完整性时，先通过 `os.stat(path).st_blocks > 0`（或 `NSURLUbiquitousItemDownloadingStatusKey`）判断是否驻留本机，避免无差别打开仅在云端的占位文件；
  - 在 `cloud_state.swift` 等状态探针中，仅对 `ubiquitousItemIsUploading == true` 的极少数活跃文件查询上传百分比，避免全量 IPC 阻塞。

### 陷阱 4：LXC 容器内删了文件，但 PVE `local-lvm` 水位分毫未降
- **现象**：进入 PVE LXC 容器内部删除了十几个 GB 的旧备份，容器内 `df -h` 已经降到 25%，但宿主机 `lvs` 查看 `local-lvm` 的 `Data%` 依然维持高位。
- **根因与对策**：LVM-thin 存储池不会在容器内 `unlink` 时自动感知空闲块，必须在 PVE 宿主机上对该容器执行：
  ```bash
  pct fstrim <ctid>
  ```
  下发 Discard 指令后，thin-pool 水位才会真正回落。

### 陷阱 5：通过 SMB 删除 NAS 文件，结果被 `#recycle` 回收站二次截留
- **现象**：在 Mac 挂载的 `/Volumes/Data`、`/Volumes/Scratch` 等共享卷上删除了几十 GB 的旧构建包或缓存，但 NAS 存储池可用空间纹丝不动。
- **根因与对策**：群晖/NAS 开启了 SMB 共享文件夹回收站（`#recycle`），通过挂载卷删除的文件只是被移入了同卷的 `#recycle/` 目录。清理 NAS 挂载卷时，必须同步检查并清理各共享根目录下的 `#recycle`（经用户授权后清空）。
