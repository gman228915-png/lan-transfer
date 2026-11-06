# 局域网互传（LAN Transfer）

电脑上双击一个 exe 就能在局域网里开一个文件共享服务：手机和电脑连同一个 Wi-Fi，
扫码或输入地址即可双向传文件，**不经过外网、不需要账号、不用数据线**。

**当前版本：v1.4.0（2026-11-06）** ｜ [更新记录](CHANGELOG.md) ｜ [HTTP 接口文档](docs/api.md) ｜ [GitHub 仓库](https://github.com/gman228915-png/lan-transfer)

## 功能一览

| 模块 | 能做什么 | 能力边界 |
|---|---|---|
| 桌面 GUI（Windows） | 双击 exe 自动启动服务；展示 IP、端口、口令、链接和二维码；发布文件、查看手机上传的文件；「在局域网内可见」开关控制 UDP 广播发现（默认开启） | 只监听当前局域网 IP；每次启动重新生成 10 位随机口令；端口写死请求 8765，被占用会自动顺延 |
| 网页端（内置） | 用浏览器访问电脑，上传（点击或拖拽、多选、进度与速率、失败重试、并发 2 个上限）、下载、删除，右上角可切换空间 | 免安装；首次访问需手动信任自签名 HTTPS 证书；不能浏览空间目录下的子目录；下载断点保存在页面内存，刷新后需重新下载 |
| 安卓端（APK） | 扫码或手输地址+口令连接；**空间下拉切换**；多文件上传（并发 2）；下载到 App 专属目录；下载中断可断点续传；已下载文件可打开/分享/另存/删除 | 需 Android 7.0 及以上；只读空间禁用上传 |
| headless 服务 | 无界面常驻，适合放后台、当传输通道；PID 文件、按天结构化日志、优雅退出；`--no-discovery` 关闭广播 | 没有 UI，需配合浏览器 / App / CLI 使用；与服务端是同一个程序，加 `--headless` 即可 |
| 命令行客户端（CLI） | `health` / `spaces` / `ls` / `put` / `get` / `rm` / `discover`；支持 `--json`、sha256 校验、语义化退出码 | 需 Python 环境运行源码，或使用打包好的 cli exe |
| 断点续传 | 下载 `.part` + `Range` + `If-Range`，完成后校验整文件 sha256 再改名；上传走分片会话（8MB/片），中断后从服务端偏移继续 | 服务端需为 v1.4.0+；旧服务端自动回退一次性上传 |
| 局域网发现 | 服务端每 3 秒 UDP 广播（端口 8766，不含口令）；CLI `discover` 列出同网段服务并给出可直接使用的 `--server` | 需要同一网段且防火墙放行 UDP 8766；跨网段需 `--any-subnet` |
| 多空间隔离 | 通过 `spaces.yaml` 把不同目录分成独立空间，可设只读、独立口令 | 只发布/列出单层文件，不展示子目录；越界路径一律拒绝 |

## 交付文件

| 文件 | 用途 |
|---|---|
| `release/lan-transfer-desktop.exe` | Windows 电脑端（带界面），单文件绿色版，双击运行 |
| `release/lan-transfer-android.apk` | 安卓安装包 |
| `release/lan-transfer-cli.exe` | 命令行客户端，给脚本 / 其他程序调用 |
| `release/shared-files/` | 电脑端「发布文件」目录（手机可下载） |
| `release/received-files/` | 电脑端「接收文件」目录（手机上传落这里） |

> `release/`、`*.exe`、`*.apk` 已被 `.gitignore` 排除，不会提交到仓库；
> 请从本仓库的 [Releases 页面](https://github.com/gman228915-png/lan-transfer/releases) 下载，
> 或按文末「从源码运行 / 自行打包」自行构建。

## 快速开始

### 1. 下载哪个文件

- 只在 Windows 电脑上收发：下载 `lan-transfer-desktop.exe` 就够了。
- 手机端想要更顺手的体验：再安装 `lan-transfer-android.apk`（不装也能用手机浏览器）。
- 需要脚本调用：下载 `lan-transfer-cli.exe`。

### 2. 双击 exe 后发生什么

1. Windows 可能提示「未知发布者 / 已保护你的电脑」，选择「更多信息 → 仍要运行」。
2. 程序**自动启动服务**，窗口顶部状态变为「● 运行中」，同时生成一个 10 位随机访问口令。
3. 窗口里可以直接看到：局域网 IP、端口、口令、完整访问链接
   （形如 `https://192.168.1.10:8765/?k=abcd1234ef`）和二维码。
4. 首次运行 Windows 防火墙会弹窗，**勾选「专用网络」并选择「允许访问」**，否则手机连不上。

### 3. 同 Wi-Fi 手机怎么连

前提：手机和电脑连同一个路由器 / 同一个 Wi-Fi，不要用手机流量。

- **扫码（安卓 App）**：打开 App 点「扫码」，对准电脑窗口里的二维码，地址和口令会自动填入并连接。
- **手输（安卓 App）**：地址填 `https://192.168.1.10:8765`（以窗口里显示的 IP 为准），
  口令填窗口里「访问口令」那一串，点连接。
- **手机浏览器**：直接打开电脑窗口里的完整链接（**必须带 `?k=口令`**），
  或先访问 `https://IP:端口` 再在页面里输入口令。

> 公司、酒店等公共 Wi-Fi 常开启「AP 隔离」，设备之间互相不可见，这种网络无法互传；
> 可以改用手机热点，让电脑连手机热点，再重新扫码。

## 电脑端界面说明

窗口分左右两栏，共四张卡片：

1. **服务信息**：主机名、局域网 IP、端口、访问口令、是否启用 HTTPS、监听说明、发布目录、接收目录；若配置了多空间还会列出每个空间的名称、模式（rw/ro）和路径。
   按钮：`启动服务 / 停止服务`、`打开网页`、`复制链接`、`打开文件夹`（打开发布目录）。
   另有一行「在局域网内可见（UDP 广播发现）」开关，默认开启，右侧显示「已广播 / 已关闭」；
   关闭后会写入数据目录的 `settings.json`，下次启动保持该设置。
2. **手机扫码连接**：显示二维码和完整访问链接；启动服务后才能看到。
3. **发布文件（电脑 → 手机）**：列出 `shared-files` 里的文件；按钮 `添加文件`、`添加文件夹`、`删除所选`、`打开文件夹`。
   列表上方有一行说明：「文件用『添加文件』，整目录用『添加文件夹』，两者都支持任意格式」。
4. **接收文件（手机 → 电脑）**：列出 `received-files` 里的文件，手机上传后自动出现在这里（每 2 秒自动刷新）；
   按钮 `刷新`、`打开接收文件夹`。

### 「添加文件」和「添加文件夹」的区别

| | 添加文件 | 添加文件夹 |
|---|---|---|
| 对话框标题 | 选择要发布的文件（可多选） | 选择要发布的文件夹（整个目录一起发布） |
| 选择方式 | Windows 文件对话框，可多选文件 | Windows 文件夹对话框，只能选目录 |
| 格式限制 | 无；第一项是「所有文件 (*.*)」，另有常用文档 / 图片 / 音视频 / 压缩包作为快捷筛选 | 无；目录内任意后缀的文件都会发布 |
| 结果 | 选中的文件复制到 `shared-files` | 递归读取目录下所有文件（含子目录），**平铺**复制到 `shared-files`，完成后提示「已发布 N 个文件」 |
| 已知限制 | — | 不保留原始目录层级；同名文件自动改名（`a (1).txt`） |

> 文件夹对话框是 Windows 系统自带的选择器，本来就只列目录、不列文件，这是正常现象；
> 想选单个文件请点「添加文件」。复制在后台线程执行，大文件夹不会卡住界面。

### 端口被占用时

程序请求 8765，若被占用会在 **8765 ~ 8776** 之间自动找空闲端口，
「服务信息」卡片会多出一行提示：`端口提示：默认端口 8765 被占用，已自动改用 XXXX`。
以窗口里显示的端口为准重新扫码 / 打开链接即可，不需要手动处理。

## 网页端与安卓端

### 网页端

- 打开链接时必须带口令：`https://IP:端口/?k=口令`；电脑上也可点「打开网页」。
- 支持点击选择或拖拽上传（多选、进度条、失败可单条重试）、下载、删除（删除前会确认）。
- 首次访问会提示「您的连接不是私密连接」——这是自签名证书，点「高级 → 继续前往」即可。
- 右上角可切换空间，默认「全部空间」；只读空间会标注「（只读）」，其中的文件只能下载。

### 安卓端

- 安装 APK 后打开，扫码或手输地址 + 口令；连接成功后会自动记住，下次打开自动重连。
- 连接成功后会自动拉取空间列表，信息条右侧的下拉可选「全部空间」或某个具体空间；
  选中后列表、上传、下载都只针对该空间；只读空间会标注「（只读）」并禁用「上传文件」。
- 文件列表显示名称、大小与所属空间；点条目右侧即可下载；上传时会显示进度、速率与百分比，多文件并发上限 2。
- 下载中断后再次点下载会从断点续传（`.part` 临时文件 + `Range`），完成后自动改名为正式文件。
- 下载的文件保存在 App 专属目录（`Android/data/com.lantransfer/files/Download/`），
  Windows 文件管理器或系统文件管理器可能看不到；在 App 内点「已下载」可打开、分享、移动/另存到公共目录或删除，不需要存储权限。
- 电脑端证书重建后，App 会因证书指纹变化而拒绝连接，按提示点「清除并重连」即可。

## 命令行客户端（CLI）

`release/lan-transfer-cli.exe` 也可用 `python desktop-client\lan_transfer_cli.py` 直接运行。

```powershell
# 1) 健康检查（不需要口令）
release\lan-transfer-cli.exe health --server https://10.0.0.5:8765 --json

# 2) 列出空间（需要口令）
release\lan-transfer-cli.exe spaces --server https://10.0.0.5:8765 --token-file token.txt --json

# 3) 列出文件名
release\lan-transfer-cli.exe ls --space shared --server https://10.0.0.5:8765 --token-file token.txt --json

# 4) 上传多个文件，并递归上传整个目录（默认校验 sha256）
release\lan-transfer-cli.exe put .\a.txt .\docs -r --space shared --server https://10.0.0.5:8765 --token-file token.txt --verify-sha256 --json

# 5) 下载到指定目录（默认校验 sha256）
release\lan-transfer-cli.exe get report.pdf -o .\downloads --space shared --server https://10.0.0.5:8765 --token-file token.txt --verify-sha256 --json

# 6) 删除文件
release\lan-transfer-cli.exe rm old.txt --space shared --server https://10.0.0.5:8765 --token-file token.txt --json

# 7) 断点续传：大文件中途 Ctrl+C / 被杀掉后，重新执行并加 --resume
release\lan-transfer-cli.exe put .\big.bin --space shared --server https://10.0.0.5:8765 --token-file token.txt --resume
release\lan-transfer-cli.exe get big.bin -o .\downloads --space shared --server https://10.0.0.5:8765 --token-file token.txt --resume

# 8) 局域网发现：监听 UDP 广播，输出可直接用于 --server 的地址
release\lan-transfer-cli.exe discover --timeout 3
release\lan-transfer-cli.exe discover --timeout 3 --json
```

- 公共参数：`--server`、`--space`、`--token-file`、`--token`、`--json`、`--verify-sha256` / `--no-verify-sha256`、`--resume`。
- `--resume` 行为：
  - `put`：按服务端返回的会话偏移继续发分片（每片 8MB），状态记录在 `<源文件>.ltupload.json`；
    服务端会话过期（30 分钟）会重建会话从头传，旧服务端则回退一次性 `POST /api/upload`。
  - `get`：用 `<文件名>.part` 记录已下载字节与 `ETag`，带 `Range` / `If-Range` 请求续传；
    完成后校验整文件 sha256 再改名为正式文件。服务端文件已变化时会丢弃 `.part` 从头下载。
- `discover` 参数：`--timeout`（默认 3 秒）、`--discovery-port`（默认 8766）、`--any-subnet`（不做同网段过滤）；
  默认只接受与本机同一 `/24` 网段的广播。
- 环境变量：`LAN_TRANSFER_SERVER`、`LAN_TRANSFER_TOKEN_FILE`、`LAN_TRANSFER_TOKEN`。
- `--json` 输出包含 `sha256`、`bytes`、`elapsed_ms` 等字段；续传时另有 `resumed`、`resumed_from`、`chunks`。
- 进度输出到 stderr：`已传/总量 百分比 速率(MB/s) 剩余时间`，不影响 `--json` 的 stdout。
- 退出码：`0` 成功、`1` 一般失败、`2` 参数错误、`3` 口令/权限错误、`4` 网络错误、`5` sha256 不一致、`6` 文件或空间不存在。
- CLI 对自签名证书不做主机名校验（与局域网使用场景一致），内容完整性依赖 sha256 校验。

## headless 模式（无界面常驻）

```powershell
# 用打包好的 exe
release\lan-transfer-desktop.exe --headless --port 8765 --token-file token.txt `
    --spaces-file spaces.yaml --data-dir D:\lan-transfer-data --log-file service.log

# 或直接跑源码
python desktop-client\lan_transfer.py --headless --port 8765 --token-file token.txt
```

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--headless` | 关 | 无界面常驻模式，必须带上 |
| `--port` | `8765` | 起始端口，被占用时在 `port ~ port+11` 内顺延 |
| `--bind` | `lan` | 监听地址；`lan` 表示只绑当前局域网 IP，也可填具体 IP（如 `127.0.0.1`）或 `0.0.0.0` |
| `--token-file` | 空 | 口令文件：存在则读取，不存在则自动生成 10 位口令并写入 |
| `--spaces-file` | 空 | 空间配置，支持 `.yaml` / `.json`；默认用数据目录下的 `spaces.yaml` |
| `--log-file` | 空 | 指定固定服务日志文件；不填则按天写到 `logs/service-YYYY-MM-DD.log` |
| `--data-dir` | 空 | 数据目录：文件、证书、配置、PID、日志都放这里；默认是 exe / 脚本所在目录 |
| `--no-discovery` | 关 | 关闭 UDP 广播发现（默认开启，端口 8766） |

- **PID 文件**：`<数据目录>\lan-transfer.pid`，启动成功后写入，收到 SIGINT / SIGTERM / SIGBREAK 优雅退出时删除。
- 日志为 JSON Lines（每行一条），上传审计单独写入 `logs/upload-audit-YYYY-MM-DD.log`。
- 启动后可访问 `/api/health` 查看版本、实际端口、是否发生端口顺延、空间列表与能力标记。

## 多空间（目录隔离）

数据目录下首次启动会自动生成 `spaces.yaml`，可以按项目/用途拆分目录：

```yaml
default_space: inbox

spaces:
  - name: inbox
    path: received-files
    mode: rw
    description: 手机或其他设备上传到这里
  - name: shared
    path: shared-files
    mode: rw
    description: 电脑端发布、供其他设备下载
  - name: project-a
    path: D:\project-a\transfer
    mode: rw
    token: project-a-token      # 该空间独立口令
    description: A 项目专用
  - name: archive
    path: D:\archive\transfer
    mode: ro                     # 只读：只能下载
```

- **全局口令**（窗口里显示的那一串 / `--token-file` 的内容）可以访问所有空间；
  空间里单独写了 `token` 的，该口令只能访问这个空间。
- **只读空间**（`mode: ro`）：允许列出和下载，上传、删除返回 403。
  网页端会标注「（只读）」；CLI 对只读空间执行 `put` / `rm` 会得到权限错误。
- 不带 `space` 参数时：列出全部空间（需要全局口令），上传落到 `default_space`（默认 `inbox`）。
- 服务端只允许访问配置里写明的目录，文件名一律清洗成单层，越界路径直接拒绝。
- 电脑端窗口会显示空间名称、模式与路径；网页端、安卓端均可切换空间。
- 支持 JSON 配置（`--spaces-file spaces.json`）。

## 数据与目录

以 exe 放在 `D:\lan-transfer\` 为例：

| 路径 | 内容 |
|---|---|
| `D:\lan-transfer\received-files\` | 「接收文件」目录（默认空间 `inbox`）；手机/网页/CLI 上传默认落这里 |
| `D:\lan-transfer\shared-files\` | 「发布文件」目录（默认空间 `shared`）；GUI 里「添加文件 / 添加文件夹」复制到这里 |
| `D:\lan-transfer\logs\service-YYYY-MM-DD.log` | 服务日志（JSON Lines，按天切分），启动信息、端口顺延、错误等都在这 |
| `D:\lan-transfer\logs\upload-audit-YYYY-MM-DD.log` | 上传审计：时间、空间、文件名、字节数、来源 IP、成功/失败、sha256，以及 `resumed`（是否断点续传）、`chunked`（是否分片上传）、`session`（会话 id） |
| `D:\lan-transfer\lan-transfer.pid` | headless 模式的进程号（正常退出会删除） |
| `D:\lan-transfer\settings.json` | 桌面端设置（目前保存「在局域网内可见」开关）；headless 同样会读取 |
| `D:\lan-transfer\spaces.yaml` | 空间配置（首次自动生成） |
| `D:\lan-transfer\.lan_transfer_cert.pem` / `.lan_transfer_key.pem` | 自签名 HTTPS 证书与私钥（首次启动自动生成） |

> 分片上传进行中的临时文件为空间目录下的 `.lt-upload-<id>.part`，不会出现在文件列表；
> 会话 30 分钟没有新分片会被自动清理，服务启动时也会清理遗留的过期临时文件。

> **没有 `error.log`**。错误信息写入 `logs/service-YYYY-MM-DD.log`（`level` 为 `error` 的记录），
> 桌面端同时会弹窗提示；排查问题请看服务日志。

**怎么改路径：**

- 桌面端：数据目录跟随 exe 所在目录，把 exe 移到别处即可（会在新目录重新生成证书与目录）。
- headless：用 `--data-dir` 一次性改变文件、证书、配置、PID、日志的位置；
  日志还可用 `--log-file` 固定到单个文件，空间配置可用 `--spaces-file` 指定。
- 空间目录：直接改 `spaces.yaml` 里各空间的 `path`（支持绝对路径），改完重启服务生效。
- 源码运行：默认以 `desktop-client\` 为数据目录，也可在 `lan_transfer.py` 命令上加 `--data-dir`。

## 安全说明

- **只监听局域网**：默认只绑定当前局域网 IP，不监听公网网卡；headless 可用 `--bind` 指定地址。
  如果取不到局域网 IP，会退回监听所有网卡，此时建议手动指定 `--bind`。
- **HTTPS 自签名**：首次启动自动生成本地证书；浏览器首次访问需手动信任（「高级 → 继续前往」）。
  安卓端采用 TOFU（首次连接记录证书 SHA-256 指纹），指纹变化会拒绝连接并提示确认。
- **口令校验**：页面与接口要求 `?k=口令`、Cookie 或 `Authorization: Bearer`；
  比较使用 `hmac.compare_digest`（常量时间），避免计时侧信道。
- **路径穿越防护**：上传/下载/删除的文件名都会清洗成单层文件名，
  并校验解析后的真实路径仍在对应空间目录内。
- **多空间隔离**：只允许访问 `spaces.yaml` 里列出的目录；只读空间拒绝写操作；空间独立口令只能访问自身。
- **审计日志**：所有上传记录到 `logs/upload-audit-*.log`，便于事后核对。
- **不做公网穿透**：不连接任何云端服务器，数据只在局域网内直传。

## 常见问题（FAQ）

**1. 手机连不上 / 一直转圈**

- 确认手机和电脑在同一个 Wi-Fi、同一网段，不要用手机流量。
- 确认电脑端状态是「● 运行中」，且防火墙已允许该程序访问专用网络。
- 电脑换了 Wi-Fi 后 IP 会变，重新扫码或按窗口里的新 IP 输入。
- 公共 Wi-Fi 的「AP 隔离」会让设备互相不可见，改用手机热点。

**2. 首次打开网页提示证书不安全**

- 正常现象：用的是本地自签名证书，只用于加密局域网流量。
- 点「高级 → 继续前往（不安全）」即可；不需要安装任何证书。

**3. 端口被占用**

- 程序会自动在 8765~8776 间顺延，窗口会提示实际端口，`/api/health` 里也有 `port_fallback`。
- 用新端口重新扫码即可；想固定端口请用 headless 或源码方式加 `--port`。

**4. 防火墙怎么放行**

- 首次运行弹窗时选「允许访问」，勾选「专用网络」。
- 若之前误点了取消：`控制面板 → Windows Defender 防火墙 → 允许应用通过防火墙`，
  找到 `lan-transfer-desktop`，勾选专用网络；找不到就点「允许其他应用」手动添加 exe。

**5. 点了「添加文件夹」却看不到文件选择**

- 这是 Windows 文件夹选择器，按设计只列目录、不列文件（标题也写着「整个目录一起发布」）。
- 要选零散文件请点「添加文件」，它的默认筛选项是「所有文件 (*.*)」。
- 「添加文件夹」会把目录内所有格式的文件平铺发布，完成后提示「已发布 N 个文件」。

**6. 如何只给本机程序用（不暴露给局域网）**

- 用 headless 模式并绑定本机回环地址：
  `lan-transfer-desktop.exe --headless --bind 127.0.0.1 --port 8765 --token-file token.txt`
- 之后只有本机进程能访问 `https://127.0.0.1:8765`，其他设备扫不到也连不上。
- 桌面 GUI 固定监听局域网 IP，若需要仅本机使用，请用上面的 headless 方式。

**7. 手机下载的文件在哪**

- 在 App 专属目录 `Android/data/com.lantransfer/files/Download/`，系统文件管理器可能看不到。
- 在 App 内「已下载」里点「移动/另存到公共目录」或「分享」即可导出。

**8. 传大文件中断了怎么办**

- CLI：文件没传完时保留断点，重新执行同一条命令并加 `--resume` 即可从断点继续（上传按 8MB 分片、下载按 `.part` 字节数）。
- 网页端：下载失败会自动重试 3 次；仍失败时已收到的部分保留在页面内存，再点一次「下载」即从断点继续（刷新页面后需重新下载）。
- 安卓端：下载中断会保留 `.part`，再次点该文件的下载按钮即从断点续传。
- 大文件建议靠近路由器，避免 Wi-Fi 信号弱导致中断。
- 服务端文件在中断期间被修改（mtime / 大小变化）时，续传会自动放弃断点、从头下载并使用新的 sha256 校验。

**9. `discover` 找不到局域网内的服务**

- 确认电脑端「在局域网内可见」开关是「已广播」（或 headless 未加 `--no-discovery`）。
- 确认防火墙允许 UDP 8766（Windows 首次运行只放行 TCP 时可能漏掉 UDP）。
- 手机/电脑在同一网段；跨网段或虚拟网卡较多时可加 `--any-subnet` 看是否能看到。
- 也可以在 `discover` 前先用 `health --server https://IP:8765` 验证网络连通。

## 项目结构

```
.
├── desktop-client/                  # 电脑端（Python 3 + Tkinter）
│   ├── main_gui.py                  # 桌面 GUI
│   ├── lan_transfer.py              # 核心服务（HTTP/HTTPS、空间、上传下载、口令、headless）
│   ├── lan_transfer_cli.py          # 命令行客户端
│   ├── static/                      # 网页端（index.html / app.js / style.css / qrcode.js）
│   ├── gen_mtls.py                  # 生成自签名证书（可选）
│   ├── run_desktop.bat              # 免打包，直接源码运行
│   ├── lan-transfer-desktop.spec    # 桌面端 PyInstaller 配置
│   └── lan-transfer-cli.spec        # 命令行 PyInstaller 配置
├── docs/api.md                      # HTTP / API 契约文档
├── android-app/                     # 安卓端（Kotlin + Gradle）
│   ├── app/src/main/...             # 源码、布局、资源
│   └── build_apk.ps1                # 一键编译 + 签名，输出到 release
├── web-client/                      # 旧版网页资源（已废弃，仅留档）
├── release/                         # 交付产物（不入库）
├── CHANGELOG.md                     # 更新记录
└── README.md
```

## 从源码运行 / 自行打包

### 源码直接运行（不打包）

```powershell
cd desktop-client
python -m pip install qrcode pillow cryptography
python main_gui.py
```

- `qrcode`、`pillow` 用于二维码显示；`cryptography` 可选，装了就启用 HTTPS，没装则以 HTTP 明文运行。
- 也可双击 `run_desktop.bat`。
- 无界面模式：`python lan_transfer.py --headless --port 8765 --token-file token.txt`。

### 打包电脑端 exe（PyInstaller 单文件）

```powershell
cd desktop-client
python -m pip install pyinstaller
python -m PyInstaller lan-transfer-desktop.spec --clean --noconfirm
copy dist\lan-transfer-desktop.exe ..\release\lan-transfer-desktop.exe

python -m PyInstaller lan-transfer-cli.spec --clean --noconfirm
copy dist\lan-transfer-cli.exe ..\release\lan-transfer-cli.exe
```

### 打包安卓 APK

```powershell
cd android-app
.\build_apk.ps1
```

脚本使用工作区内的 `jdk17/`、`android-sdk/`、`gradle/`（均不提交到仓库），
编译后用 `lan-transfer.keystore` 签名，输出 `release/lan-transfer-android.apk`。

## License 与来源说明

- 许可：MIT。
- 来源：本项目为局域网自用工具，全部代码在本仓库内，无第三方云端依赖。
- 仓库当前未单独放置 `LICENSE` 文件；如需正式授权文件可自行补充。
- 相关文档：[更新记录](CHANGELOG.md) ｜ [HTTP 接口文档](docs/api.md)
</content>
