# 四域深度巡检清单与六大跨层存储陷阱

本参考文档总结了跨 **Mac 本机、本地多仓库工作区、GitHub 全账号远端、NAS/PVE 家庭服务器** 联合巡检时的检查要点，以及实战中最容易踩坑的 6 个跨层连带病灶。

**风险分级约定**：会动到备份（Time Machine 快照、iCloud/NAS 备份历史、回收站）、家里正在跑的服务（PVE、Docker、NAS）、别的会话的工作副本或远端仓库的动作，一律是 **C 级·需用户拍板** 的看板候选，不在执行阶段自动做。

---

## 一、四域并行只读巡检清单

### 1. 💻 Mac 本机、应用、启动项、外设固件与备份管线（配合 `$mac-maintenance`）
- **核心水位**：`/System/Volumes/Data` 总量、已用、可用及 APFS 容器 `Purgeable`（可清除）空间。
- **全局纯缓存重灾区（`system_cache`，A 级安全立省）**：
  - `~/Library/Developer/Xcode/DerivedData`（含空壳目录与过期工程构建缓存）、`~/Library/Caches/Homebrew`、`~/.npm/_cacache`、`~/.cache/uv`、`~/.cache/pip`、`~/Library/Caches/ms-playwright` 旧浏览器内核；
  - 区分原则：只清体积 `>= min_cache_mb`（默认 200 MiB）且 100% 可自动重建的开发者/包管理器缓存，**绝不清理浏览器登录态、日常通讯软件热缓存或 Spotlight 索引**。
- **Xcode `Unavailable` 模拟器（C 级，不是纯缓存）**：
  - 用 `xcrun simctl list devices unavailable` 只盘点；模拟器里可能有别的会话的测试数据，按 `$temporary-environments`（`~/.agents/skills/temporary-environments/SKILL.md`）的所有权登记核对，只回收登记为本工具创建且已可回收的设备，其余逐台列给用户拍板，不跑 `xcrun simctl delete unavailable` 批量删除。
- **全量 APP 体积/常用度统计与双模式卸载（`app_uninstall` + `app_inventory`）**：
  - 扫描 `/Applications` 与 `~/Applications` 下所有 `.app`，区分**常用 APP**与**久未使用 APP**（默认 `>= unused_app_days` 90 天未用且总占用 `>= min_app_size_mb` 50 MiB）。「最近使用」取 Spotlight `kMDItemLastUsedDate`（`mdls -raw -name kMDItemLastUsedDate`，最近打开）和应用本体及其数据、缓存目录最近文件改动中较晚的一个；取不到 Spotlight 值（未索引、索引关闭或非 macOS）时只按文件改动估算。`last_used_basis` 记为 `spotlight` 或 `file_mtime`，看板标题对应写「N 天未打开」或「N 天无文件改动」。分别统计：
    - `app_bytes`（`.app` 本体体积）
    - `data_bytes`（`Application Support`、`Containers`、`Group Containers`、`Preferences` 等用户数据与配置）
    - `cache_bytes`（`Caches`、`Saved Application State`、`Logs`、`HTTPStorages`、`WebKit` 等缓存与运行状态）
  - 支持在看板与 CLI 中切换两种卸载模式：
    - **保留用户数据（`keep_data`，默认）**：仅移走 `.app` 本体与缓存目录，完整保留 `Application Support`、`Containers` 与偏好设置，方便日后重装无缝恢复；
    - **一键全部删除（`purge_all`）**：连同 `.app` 本体、用户数据与所有关联残留一并移入隔离区，只在用户明确选择时使用。
- **深度卸载残留清理（`app_orphan` 跨目录同源聚合）**：
  - 扫描 `~/Library` 下 8 类目录（`Application Support`、`Caches`、`Containers`、`Group Containers`、`Saved Application State`、`Logs`、`HTTPStorages`、`WebKit`），与已安装 APP 的 `CFBundleIdentifier`、`CFBundleName` 及 Homebrew 软件包做交叉比对；
  - 同一已卸载应用散落在多处的残留目录按归一化主键自动聚合成单张多路径（`paths`）候选卡片（`tier: "C"`，需用户拍板确认）。
- **无用 Mac 启动项检查（`launch_item`）**：
  - 扫描 `~/Library/LaunchAgents`、`/Library/LaunchAgents`、`/Library/LaunchDaemons` 下的 `.plist`；
  - 精准识别三类病灶：**目标可执行文件已丢失（`broken_executable`）**、**所属父级 `.app` 已卸载（`orphan_app`）**、以及 **`launchctl list` 退出码非零处于崩溃循环（`crash_loop`）**。
- **全部 APP 更新与全栈开发依赖更新检查（`app_update` & `env_update`）**：
  - 应用更新：结合 `brew outdated --cask --json=v2` 与 `mas outdated` 检查全部 Mac 应用可用更新；
  - 环境与依赖更新：检查 Homebrew Formulae（`brew outdated --formula`）、Node.js 全局包（`npm outdated -g`）与 Rust 工具链（`rustup check`）版本落后情况。
- **外设与存储固件及健康度检查（`peripheral_firmware`）**：
  - 扫描内置/外置硬盘（NVMe、USB 移动硬盘的固件版本与 `SMART Status`）、SD 卡读卡器（`SPCardReaderDataType`）、雷电/USB 扩展坞（`SPThunderboltDataType` / `SPUSBDataType`，自动捕获支持 `5 Gb/s`+ 的高速外设因线材或接口协商降速到 USB 2.0 `480 Mb/s` 的异常）、以及蓝牙/USB 鼠标键盘（固件版本与 `<= 20%` 低电量告警）。

### 2. 📂 本地项目群与多宿主 Worktree（配合 `$repo-cleanup` & `$codex-maintenance`）
- **多宿主 Worktree 盘点**：
  - 在 `workspace_roots` 下每个仓库运行 `git worktree list`，汇总仓库内嵌的 `.claude/worktrees/*`、`.worktrees/*` 以及挂在别处的 worktree（如 Orca、Codex、Antigravity 的集中目录）；
  - 只盘点不动手：退役门槛（无未提交修改、无未跟踪文件、无未合入提交、无活跃会话或进程占用）由 `$repo-cleanup` 核对并执行；别的对话正在用的工作副本不搬、不收拢。
- **`.git/objects/pack` 膨胀体检**：
  - 检查各仓库 `.git/objects/pack` 体积；若超过阈值（默认 100 MiB）且远大于源码体积，标记为 `git_pack_bloat`（B 级），在无并发写入时用普通 `git gc` 压缩（不加 `--prune=now`，保留别的会话可能还在用的松散对象）。pack 目录永远不进隔离区。
- **工程规范对齐**：
  - 检查每个 Git 仓库是否具备 `AGENTS.md`（没有再看 `CLAUDE.md`）且有一行按 `git-ship` 规则写在行首的 `合入前检查：\`命令\`` 或 `合入前检查：无`（`scan-local` 用同一正则判断，写法认不出时单独提示），以及 `CLAUDE.md`（`@AGENTS.md`）；
  - 检查工作区根目录的索引 `README.md` 是否与实际目录树一致。

### 3. 🐙 GitHub 全账号远端治理
- **全量仓库枚举**：使用 `gh repo list --limit 100 --json name,isPrivate,isArchived,pushedAt,defaultBranchRef` 盘点所有公开与私有仓库。
- **远端陈旧分支与 Checkpoint 清理**：
  - 识别自动化工具留下的海量 `checkpoints/*` 分支或早已合入主干的陈旧 PR 分支（C 级，用户逐项确认后才删）；
  - 删除前用 `git merge-base --is-ancestor` 核对是否已合入默认分支；没合入的先推到 `archive/<原分支名>` 留底，读回确认后再删原分支。
- **CI 状态甄别**：
  - 使用 `gh run list` 查看近期失败的工作流；区分「已被后续提交修复的旧报错 / 浏览器 E2E 偶发竞态」与「当前 `main` 分支真实断裂」。
- **仓库归档与纳管**：
  - 对已完全合入新项目、不再独立维护的旧仓库，列为 C 级候选建议 `gh repo archive`；
  - 对本地已在积极开发但尚未初始化 Git 或未关联 GitHub 远端的工具目录，建议补齐 `.gitignore`、`AGENTS.md` 并推送私有仓库备份。

### 4. 🏠 NAS 与 PVE 家庭服务器（Homelab）
- **Proxmox VE 节点与存储池**：
  - `/boot/efi`（ESP 分区）：检查是否被旧版 `proxmox-kernel-*` 镜像或误放的主板 BIOS `.CAP` 文件塞满（水位 >80% 会导致下次内核升级失敗）；卸载旧内核是 **C 级**（用户拍板后才做），**永远保留当前运行内核与最新 2 个版本**；
  - `pve-root`：排查 `/root/` 或 `/var/tmp/` 下的一次性维护备份、旧归档包；
  - `local-lvm` (`pve/data` thin-pool)：检查 `lvs` 的 `Data%` 与 `Meta%`；排查各 VM/CT 配置中长期未挂载的 `unused0`/`unused1` 孤儿卷，以及停机数月的一次性灾难恢复演练克隆机（如 `*-dr-test`）。孤儿卷和演练克隆机固定为 **C 级**，只列候选，用户逐台确认后才删。
- **VM 与 LXC 容器内部**：
  - Docker 宿主 VM：检查 `docker system df` 与是否存在 `unhealthy` 或 `Restarting` 循环的容器。清理构建缓存（`docker builder prune`）和悬空镜像（`docker image prune`，不带 `-a`）是 B 级；`docker image prune -a` 会删除**所有没被容器使用的镜像**（包括暂时停掉的服务要用的镜像），标 **C 级**，用户确认后才做；
  - LXC 容器：容器内清理后，宿主机上的 `pct fstrim <ctid>` 会对正在跑的容器下发 discard，列为 **C 级**候选，用户确认后执行。
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
  3. 生成最新干净快照后，**删除包含膨胀目录的历史快照、回收未被引用的数据块会永久改写备份历史**，列为 C 级候选写清要删哪些快照和体积，用户拍板后才做。

### 陷阱 2：Mac 删了几十 GB 文件，但 `df -h` 可用空间几乎没涨（APFS 本地快照锁死）
- **现象**：清理完大量缓存、模拟器与构建目录后，`du` 统计确实少了 50+ GiB，但 `df -h /System/Volumes/Data` 显示可用空间只多了不到 1 GiB。
- **根因与对策**：
  - macOS 的 Time Machine 本地快照（`com.apple.TimeMachine.*.local`）或系统更新前的 APFS 快照仍引用着被删文件的数据块，物理空间不会释放，直到快照过期或被瘦身；
  - 先用 `tmutil listlocalsnapshots /` 只读查验。确认存在大容量历史本地快照后，把瘦身作为 **C 级**候选写进看板（说明会丢掉哪些时间点的本地备份），用户拍板后才执行：
    ```bash
    tmutil thinlocalsnapshots /System/Volumes/Data 100000000000 4
    ```
    执行后才会回收被快照锁住的物理空间。

### 陷阱 3：校验或统计 iCloud 目录时触发「云端回下风暴」或 Spotlight IPC 超时
- **现象**：
  1. 对 iCloud Drive（`Mobile Documents`）下的几十万个备份分块逐一运行解密校验或读取文件内容，结果把原本已经 Evict（仅保留云端、不占本机空间）的历史分块重新下载回本机，导致磁盘瞬间爆满；
  2. 对 20 万+ 文件的 iCloud 目录全量查询 `NSMetadataItem` 上传进度，导致 Spotlight IPC 600 秒超时失败。
- **根因与对策**：
  - 检查 iCloud 分块完整性时，先通过 `os.stat(path).st_blocks > 0`（或 `NSURLUbiquitousItemDownloadingStatusKey`）判断是否驻留本机，避免无差别打开仅在云端的占位文件；
  - 在 `cloud_state.swift` 等状态探针中，仅对 `ubiquitousItemIsUploading == true` 的极少数活跃文件查询上传百分比，避免全量 IPC 阻塞。

### 陷阱 4：LXC 容器内删了文件，但 PVE `local-lvm` 水位分毫未降
- **现象**：进入 PVE LXC 容器内部删除了十几个 GB 的旧备份，容器内 `df -h` 已经降到 25%，但宿主机 `lvs` 查看 `local-lvm` 的 `Data%` 依然维持高位。
- **根因与对策**：LVM-thin 存储池不会在容器内 `unlink` 时自动感知空闲块，需要在 PVE 宿主机上对该容器执行下面的命令（**C 级**，会对正在跑的容器下发 discard，用户确认后才做）：
  ```bash
  pct fstrim <ctid>
  ```
  下发 Discard 指令后，thin-pool 水位才会真正回落。

### 陷阱 5：通过 SMB 删除 NAS 文件，结果被 `#recycle` 回收站二次截留（且并发 `rm -rf` 锁死 macOS `smbfs`）
- **现象**：在 Mac 挂载的 `/Volumes/<NAS共享A>`、`/Volumes/<NAS共享B>` 等共享卷上删除了几十 GB 的旧构建包或缓存，但 NAS 存储池可用空间纹丝不动；若在 Mac 侧启动多个并发 `rm -rf /Volumes/<NAS共享A>/#recycle/...`，会导致 macOS `smbfs` 内核挂载锁死（进程卡在 `U` 状态）。
- **根因与对策**：群晖/NAS 开启了 SMB 共享文件夹回收站（`#recycle`），通过挂载卷删除的文件只是被移入了同卷的 `#recycle/` 目录。`#recycle` 里还有家人和别的任务删掉、可能要找回的文件，**不整目录清空**：
  1. 清理 `#recycle` 是 **C 级**候选，只针对本批次删掉的条目（按本批次删除清单里的相对路径逐条定位），用户确认后才做；
  2. **严禁在 macOS SMB 挂载点上跑多路并发 `rm -rf`**，单进程串行逐条删除，或通过 SSH 在 NAS 本机删除。

### 陷阱 6：只保护了活跃 Worktree 的源码，却清理或挪走了其内部的 `artifacts` / `target`，打断正在跨轮验证的并发 AI 会话
- **现象**：另一个 Codex / Claude 会话正在某棵工作树（如 `~/.codex/worktrees/<任务名>/<项目名>`）里做跨轮回归测试，依赖被 `.gitignore` 忽略的 `distribution/artifacts/` 下的 CSV 样本、便携版 `LibreOffice.app` 或编译产物。巡检时虽然保留了该 worktree 的 Git 源码，但把里面的 `distribution/artifacts/` 或 `target/` 当成「可重建产物」删掉（或移去回收站），导致隔壁会话下一秒读取原路径直接报错中断。
- **治本对策**：
  1. **活跃工作树整树连带豁免**：`full_stack_tool.py preflight` 会向上追溯目标的父级 Git 工作树/仓库根目录，结合 `.git` 元数据修改时间、工作树文件修改时间（默认 48 小时保护窗 `worktree_protect_minutes: 2880`）、`git status --porcelain`、`lsof` 打开句柄以及 Codex 会话记录（`~/.codex/sessions/**/*.jsonl` 里近期提到该路径；Claude Code、Antigravity、OpenCode 的会话目前不读）。只要父级工作树处于活跃或未收尾状态，**整棵工作树（含内部所有 `target`、`node_modules`、`distribution/artifacts`）全部锁定跳过，连移动都不允许**。
  2. **闲置项目产物先移入「外置盘隔离区（Quarantine）」**：对已超过保护窗确认闲置的项目产物或测试载荷，使用 `full_stack_tool.py quarantine --ids <ID>` 移入配置的隔离根（如 `/Volumes/<外置盘>/.Cleanup_Quarantine/<批次>/`，必须是真实挂载且 `volume_uuid` 相符，否则退回 `~/.Cleanup_Quarantine/`），自动生成 `manifest.json` 与一键恢复脚本 `restore.sh`。旧 worktree 不进隔离区，交给 `$repo-cleanup` 退役。等用户确认所有任务无异常后，再列出批次请用户同意清除。
