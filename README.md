# 局域网互传（LAN Transfer）

一款**局域网内**双向传文件的小工具：电脑上双击 exe 启动服务，手机和电脑连同一个 Wi-Fi，
手机扫码（或用浏览器打开链接）即可互传文件，不经过外网、不需要登录账号。

- 电脑 → 手机：在电脑端把文件加入「发布文件」，手机列表里点击「下载」
- 手机 → 电脑：手机端点「上传」选择文件，文件落到电脑的「接收文件」目录
- 手机 ↔ 手机：任意一台手机上传后，其他手机都能从列表里下载
- 免权限：手机端下载到 App 专属目录，不需要存储权限；需要导出时用系统的「另存/分享」

## 交付文件（release 目录）

| 文件 | 用途 |
|---|---|
| `release/lan-transfer-desktop.exe` | Windows 电脑端，单文件绿色版，双击运行 |
| `release/lan-transfer-cli.exe` | 命令行客户端（供脚本 / AI / 其他项目调用） |
| `release/lan-transfer-android.apk` | 安卓安装包（Android 7.0 及以上） |
| `release/shared-files/` | 电脑端共享目录（手机能看到、能下载的文件） |
| `release/received-files/` | 手机上传到电脑的文件 |

> `release/` 与构建产物已在 `.gitignore` 中排除，不提交到 GitHub；请从发布页或本地构建获取。

## 电脑端启动

1. 双击 `release/lan-transfer-desktop.exe`（Windows 可能提示「未知发布者」，选择「仍要运行」）。
2. 程序会自动启动服务，窗口里能看到这几样东西：
   - **局域网 IP** 和 **端口**（默认 8765，被占用会自动顺延，窗口会提示实际端口）
   - **访问口令**（10 位字母数字，每次启动随机生成）
   - **访问链接**（`https://IP:端口/?k=口令`）和二维码
3. 按钮说明：
   - `添加文件` / `添加文件夹`：把文件放进发布目录 `shared-files`，手机端才能下载
   - `打开发布文件夹` / `打开接收文件夹`：在资源管理器里查看 `shared-files` / `received-files`
   - `复制链接`：把带口令的完整链接复制到剪贴板
   - `打开网页`：在电脑浏览器里打开同一个网页端
   - `停止服务` / `启动服务`：手动控制服务

首次运行时 Windows 防火墙会弹窗，**必须选「允许访问」**（勾选专用网络即可）。

## 手机端 / 网页端连接

前提：**手机和电脑连接同一个 Wi-Fi / 同一局域网**。

### 手机 App（推荐）

1. 安装 `release/lan-transfer-android.apk`。
2. 打开 App，点「📷 扫码」扫描电脑窗口里的二维码，会自动填好地址和口令并连接。
3. 也可以手动填写：地址形如 `https://192.168.1.10:8765`，口令从电脑窗口的「访问口令」抄过来。
4. 连接成功后：
   - 文件列表 = 电脑 `shared-files`（发布）+ `received-files`（接收）里的所有文件，点右侧「下载」
   - 点「上传文件」可多选，上传进度和百分比会显示在状态栏
   - 点「📁 已下载」可以打开 / 分享 / 另存到公共目录 / 删除本地副本
5. App 会记住上次的地址和口令，下次打开自动重连。

### 网页端（免安装）

1. 手机浏览器扫描电脑二维码，或输入电脑窗口里显示的完整链接（**必须带 `?k=口令`**）。
2. 电脑浏览器可直接点「打开网页」。
3. 网页端支持点击/拖拽上传（带进度条、失败可重试）、下载、删除。
4. 网页端右上角可切换**空间**（inbox / shared / 自定义空间），默认「全部空间」。
5. 首次打开若提示证书不受信任（自签名证书），选择「继续访问 / 高级 → 继续前往」即可。

> 地址和口令从哪里看？都在**电脑端窗口**的「服务信息」和二维码区域里；网页端的连接卡片里也会显示当前链接。

## 给程序 / AI 当传输通道（Headless + CLI）

除了给人点界面，也可以把它当成本地局域网的「文件传输 API」：

### 无界面常驻

```powershell
# 源码运行
python desktop-client\lan_transfer.py --headless --port 8765 --token-file token.txt `
    --spaces-file spaces.yaml --data-dir D:\lan-transfer-data --log-file service.log

# 或用打包好的 exe
release\lan-transfer-desktop.exe --headless --port 8765 --token-file token.txt
```

- 首次运行若 `--token-file` 不存在，会**自动生成**并写入该文件。
- 写 PID 文件 `lan-transfer.pid`，结构化日志按天切分（`logs/service-YYYY-MM-DD.log`）。
- `Ctrl+C` / SIGINT / SIGTERM / SIGBREAK 触发优雅退出并清理 PID。
- 端口被占用时仍会自动顺延，`/api/health` 会返回 `port_fallback` 和实际端口。

### 命令行客户端

```powershell
# 查看服务状态（不需要口令）
release\lan-transfer-cli.exe health --server https://10.0.0.5:8765 --json

# 查看空间
release\lan-transfer-cli.exe spaces --server https://10.0.0.5:8765 --token-file token.txt

# 上传多个文件 + 整个目录（-r），上传后自动校验 sha256
release\lan-transfer-cli.exe put .\a.txt .\docs -r --space shared `
    --server https://10.0.0.5:8765 --token-file token.txt --json

# 下载到指定目录并校验 sha256
release\lan-transfer-cli.exe get report.pdf -o .\downloads `
    --server https://10.0.0.5:8765 --token-file token.txt --json

# 列出 / 删除
release\lan-transfer-cli.exe ls --space shared --server https://10.0.0.5:8765 --token-file token.txt
release\lan-transfer-cli.exe rm old.txt --server https://10.0.0.5:8765 --token-file token.txt
```

- 直接跑源码也行：`python desktop-client\lan_transfer_cli.py ...`
- `--json` 输出适合程序解析（含 `sha256`、`bytes`、`elapsed_ms`）。
- 退出码：`0` 成功、`1` 失败、`2` 参数错误、`3` 口令/权限、`4` 网络、`5` sha256 不一致、`6` 不存在。
- 断点续传参数 `--resume` 已预留，当前会完整下载。
- 完整接口见 [docs/api.md](docs/api.md)。

## 多空间（项目隔离）

`spaces.yaml` 用于把不同项目/用途的目录隔离开，每个空间可独立设置只读与口令：

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
    token: project-a-token      # 只允许持有该口令的调用方访问
    description: A 项目专用
  - name: archive
    path: D:\archive\transfer
    mode: ro                     # 只读：可下载，上传/删除返回 403
```

- 配置文件默认是程序目录下的 `spaces.yaml`，首次启动自动生成示例，老用户升级无感。
- 也支持 JSON：`--spaces-file spaces.json`。
- 服务端只允许访问配置里列出的目录，文件名只允许单层，越界路径直接拒绝。
- 全局口令可以访问所有空间；空间独立 `token` 只能访问自己的空间。
- 电脑端窗口会显示空间列表与各自目录；网页端右上角可切换空间；安卓端当前固定使用默认空间。

## 常见问题（FAQ）

**1. 手机连不上 / 一直转圈**
- 确认手机和电脑连的是同一个 Wi-Fi（同一路由器、同一网段），不要用手机流量。
- 确认电脑端窗口显示「● 运行中」，且 Windows 防火墙允许该程序访问**专用网络**。
- 手机 App 里的地址要和窗口里的「局域网 IP」一致；电脑换了 Wi-Fi 后 IP 会变，重新扫码即可。
- 公司/酒店 Wi-Fi 常开启「AP 隔离」，设备之间互不可见，这种网络下无法互传，改用手机热点。

**2. 防火墙怎么放行**
- 首次运行弹窗时选择「允许访问」，并勾选「专用网络」。
- 若之前误点了「取消」：`控制面板 → Windows Defender 防火墙 → 允许应用通过防火墙`，
  找到 `lan-transfer-desktop`（或点击「更改设置 → 允许其他应用」添加 exe），勾选专用网络。
- 不想改系统设置时，可临时把当前 Wi-Fi 网络类型设为「专用网络」。

**3. 端口被占用**
- 电脑端会自动在 8765～8776 之间找空闲端口，窗口会显示「默认端口 8765 被占用，已自动改用 XXXX」。
- 以新端口为准重新扫码/打开链接即可；无需手动处理。
- 想固定端口可在源码方式运行时加 `--port 9000`。

**4. 浏览器提示「您的连接不是私密连接」**
- 正常现象：电脑端使用的是自签名的本地 HTTPS 证书，只用于加密局域网流量。
- 点「高级 → 继续前往（不安全）」即可；手机 App 首次连接会自动记住证书指纹（TOFU）。

**5. 手机下载的文件在哪**
- 在 App 专属目录 `Android/data/com.lantransfer/files/Download/`，系统文件管理器可能看不到。
- 用 App 里的「📁 已下载 → 移动/另存到公共目录」导出到「下载」等公共位置，或直接用「分享」发到微信/QQ。

**6. 上传大文件卡住怎么办**
- 电脑端复制文件到发布目录是在后台线程执行的，不会卡住窗口；手机端有进度条，不会阻塞界面。
- 传输中断后，网页端点进度条右侧「重试」，手机端在失败弹窗里点「重试」即可，无需重新选文件。
- 超大文件建议电脑和手机都靠近路由器，避免 Wi-Fi 信号弱导致中断。

## 安全说明

- **只监听局域网**：电脑端默认只绑定当前局域网 IP，不监听公网网卡；也可用 `--bind` 指定地址。
- **口令校验**：所有页面/接口都要求 `?k=口令`、Cookie 或 `Authorization: Bearer`，比较使用常量时间算法。
- **多空间隔离**：只允许访问 `spaces.yaml` 里配置的目录；空间可设只读或独立口令，越权请求返回 403。
- **路径穿越防护**：上传/下载/删除的文件名都会清洗成单层文件名，并校验解析后的真实路径仍在空间目录内。
- **审计日志**：每次上传都记录时间、空间、文件名、字节数、来源 IP、成功/失败与 sha256，
  按天写入 `logs/upload-audit-YYYY-MM-DD.log`。
- **传输加密**：证书存在时启用 HTTPS（自签名）；手机端固定证书指纹，指纹变化会提示确认。
- **不做公网穿透**：本工具不连接任何云端服务器，数据只在局域网内直传。

## 项目结构

```
.
├── desktop-client/                  # 电脑端（Python 3 + Tkinter）
│   ├── main_gui.py                  # 桌面 GUI
│   ├── lan_transfer.py              # 核心服务（HTTP/HTTPS、空间、上传下载、口令校验、headless）
│   ├── lan_transfer_cli.py          # 命令行客户端（put/get/ls/rm/spaces/health）
│   ├── static/                      # 网页端（index.html / app.js / style.css / qrcode.js）
│   ├── gen_mtls.py                  # 生成自签名证书（可选）
│   ├── run_desktop.bat              # 免打包，直接源码运行
│   ├── lan-transfer-desktop.spec    # 桌面端 PyInstaller 配置
│   └── lan-transfer-cli.spec        # 命令行 PyInstaller 配置
├── docs/api.md                      # HTTP / API 契约文档
├── android-app/                     # 安卓端（Kotlin，Gradle 构建）
│   ├── app/src/main/…               # 源码、布局、资源
│   └── build_apk.ps1                # 一键编译 + 签名，输出到 release
├── web-client/                      # 旧版网页资源（已废弃，仅留档）
├── release/                         # 交付产物（不入库）
│   ├── lan-transfer-desktop.exe
│   ├── lan-transfer-android.apk
│   ├── shared-files/                # 电脑端「发布文件」目录（手机可下载）
│   └── received-files/              # 电脑端「接收文件」目录（手机上传落这里）
└── README.md
```

> exe 运行时在**自身所在目录**创建 `shared-files`（发布）和 `received-files`（接收）两个目录；
> 界面上仍叫「发布文件 / 接收文件」，只是目录名使用英文。旧版中文目录启动时会自动迁移，不会丢文件。

## 从源码构建

### 电脑端 exe（PyInstaller 单文件）

```powershell
cd desktop-client
python -m PyInstaller lan-transfer-desktop.spec --clean --noconfirm
copy dist\lan-transfer-desktop.exe ..\release\lan-transfer-desktop.exe

python -m PyInstaller lan-transfer-cli.spec --clean --noconfirm
copy dist\lan-transfer-cli.exe ..\release\lan-transfer-cli.exe
```

依赖：Python 3.9+、`pyinstaller`、`qrcode`、`Pillow`、`cryptography`（HTTPS 可选）。

### 安卓 APK

```powershell
cd android-app
.\build_apk.ps1
```

脚本使用工作区内的 `jdk17/`、`android-sdk/`、`gradle/`（均不提交到仓库），
编译后用 `lan-transfer.keystore` 签名，直接输出 `release/lan-transfer-android.apk`。

## License

MIT
