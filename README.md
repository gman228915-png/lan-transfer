# 局域网互传（LAN Transfer）

电脑上双击一个 exe 就能在局域网里开一个文件共享服务：手机和电脑连同一个 Wi-Fi，
扫码或输入地址即可双向传文件，**不经过外网、不需要账号、不用数据线**。

**当前版本：v1.8.0（2026-11-14）** ｜ [更新记录](CHANGELOG.md) ｜ [HTTP 接口文档](docs/api.md) ｜ [MCP 文档](docs/mcp.md) ｜ [GitHub 仓库](https://github.com/gman228915-png/lan-transfer)

## 本次更新（v1.8.0）

- **分区（spaces）搬到界面上**：顶栏「分区管理」可新建 / 编辑 / 删除分区、指定文件夹、设默认，
  不用再手改 `spaces.yaml`；删除只移除配置，不删磁盘文件。
- **传输联动**：「发布文件」「接收文件」列表上方新增分区下拉，切换即切换目录；
  新建分区立即生效，不需要重启服务；网页端 / 安卓端自动读到新分区与只读标志。
- **安全校验**：分区名禁止路径分隔符与 Windows 保留名；路径禁止系统目录、盘符根目录、已有文件；
  只读分区上传仍返回 403 且界面禁用上传按钮。
- **容错**：配置文件写坏时回退到 `inbox` / `shared` 并提示，写入用临时文件 + 原子替换。

## 本次更新（v1.7.1）

- **折叠不再改变窗口尺寸**：收起任意卡片或「全部收起」后，窗口外框保持当前大小，
  空出的区域直接露出背景；窗口大小单独记录，重启恢复上次手动调整值。
- **四张卡收起样式统一**：以「服务信息」卡为基准，收起后保留一条标题行，
  右侧是 ⌃/⌄ 按钮，行内显示本卡关键摘要（服务地址 / 已发布 N 个文件 / 收到 N 个文件）。
- **卡片不透明度看得见**：顶栏常驻「卡片不透明度」滑块，默认 75%、范围 40%–100%，
  背景采样色权重随滑块线性变化（40/60/75/100% 采样点每通道相邻差 ≥8 色阶），
  网页端同步并实测 `rgba(17, 26, 46, α)`。

## 功能一览

| 模块 | 能做什么 | 能力边界 |
|---|---|---|
| 桌面 GUI（Windows） | 双击 exe 自动启动服务；展示 IP、端口、口令、链接和二维码；发布文件、查看手机上传的文件；「在局域网内可见」开关控制 UDP 广播发现（默认开启） | 只监听当前局域网 IP；每次启动重新生成 10 位随机口令；端口写死请求 8765，被占用会自动顺延 |
| 网页端（内置） | 用浏览器访问电脑，上传（点击或拖拽、多选、进度与速率、失败重试、并发 2 个上限）、下载、删除，右上角可切换空间；**下载断点存入 IndexedDB，刷新或关页后可续传** | 免安装；首次访问需手动信任自签名 HTTPS 证书；不能浏览空间目录下的子目录 |
| 安卓端（APK） | **连上 Wi-Fi 自动 UDP 扫描并列出设备，点选填入地址**；保留扫码与手输兜底；空间下拉切换；多文件上传（并发 2）；下载断点续传；已下载文件可打开/分享/另存/删除 | 需 Android 7.0 及以上；自动发现需与电脑同一 Wi-Fi 且允许 UDP 8766；只读空间禁用上传 |
| headless 服务 | 无界面常驻，适合放后台、当传输通道；PID 文件、按天结构化日志、优雅退出；`--no-discovery` 关闭广播；`--no-tls` 本机调试用 HTTP | 没有 UI，需配合浏览器 / App / CLI / MCP 使用；与服务端是同一个程序，加 `--headless` 即可 |
| 命令行客户端（CLI） | `health` / `spaces` / `ls` / `put` / `get` / `rm` / `discover` / `rotate-token`；支持 `--json`、sha256 校验、语义化退出码 | 需 Python 环境运行源码，或使用打包好的 cli exe |
| **MCP 服务器（v1.5 新增）** | 通过 stdio 把 `health` / `spaces` / `ls` / `put` / `get` / `rm` / `discover` 暴露给 AI（Claude Desktop、Cursor 等）；结构化结果 + 中文错误码，支持目录递归、断点续传、sha256 校验 | 需 Python 3.9+ 的 MCP 客户端；详细配置与示例见 [docs/mcp.md](docs/mcp.md) |
| 断点续传 | 下载 `.part` + `Range` + `If-Range`，完成后校验整文件 sha256 再改名；上传走分片会话（8MB/片），中断后从服务端偏移继续；**服务端重启后会话自动恢复** | 服务端需为 v1.4.0+（重启恢复需 v1.5.0+）；旧服务端自动回退一次性上传 |
| 局域网发现 | 服务端每 3 秒 UDP 广播（端口 8766，不含口令）；CLI `discover`、安卓端自动扫描列出同网段服务 | 需要同一网段且防火墙放行 UDP 8766；跨网段需 `--any-subnet` |
| 稳定性（v1.5） | 服务端上传并发上限（默认 4），超出返回 `429` + 中文提示；`rotate-token` 平滑轮换口令，旧口令宽限期内仍可用 | 并发上限当前写死为 4；口令宽限期最长 7 天 |
| **外观自定义（v1.6 新增）** | 电脑端 / 网页端 / 安卓端都可换本地背景图；电脑端和网页端支持铺满 / 适应 / 平铺 / 居中 + 遮罩浓度 0–100%；设置分别持久化到 `settings.json`、IndexedDB + localStorage、`SharedPreferences` | Tkinter 卡片不支持真 alpha 混合，用深色面板 + 暗化层实现可读性；安卓端仅构建验证 |
| **品牌图标（v1.6 新增）** | 统一「文件传输」图标：exe / 标题栏 / 任务栏 / 窗口左上角、网页 favicon 与标题 logo、安卓 launcher；源文件 `icon.svg` + `make_icons.py` 可随时重生成 | 16/24px 使用简化版图形，细节比大尺寸少 |
| **卡片折叠（v1.7 新增）** | 四张卡片可单独收起 / 展开，另有「全部收起 / 全部展开」；v1.7.1 起收起只留下与「服务信息」一致的标题行（右侧 ⌃/⌄ + 本卡关键摘要），**窗口外框尺寸不变**，收起空出的区域直接露出背景；窗口大小单独记录，重启恢复上次手动调整值。网页端同步支持，状态存 localStorage | 折叠只是隐藏内容，不改变任何传输行为，也不改变窗口尺寸 |
| **卡片不透明度（v1.7 新增）** | 电脑端顶栏常驻 **「卡片不透明度」滑块**（v1.7.1：40%–100%，默认 75%），带实时百分比；按背景图在卡片区域采样平均色并按滑块线性混合，空隙与窗口外露出背景原图。网页端用 CSS alpha + `backdrop-filter`，共用同一参数 | Tkinter 子控件没有真 alpha，电脑端是「采样混色」模拟半透明，非真透明；v1.7.1 已给像素采样证据（40/60/75/100% 相邻档位每通道 ≥8 色阶） |
| **多语言（v1.7 新增）** | 简体中文 / 繁體中文 / English / 日本語 / 한국어 / Español / Русский，电脑端、网页端、安卓端全覆盖；默认跟随系统语言，可手动切换并持久化；缺词回退英文 | 电脑端切换语言需重建窗口，程序会自动重启自身（约 1 秒） |
| 多空间隔离 | 通过 `spaces.yaml` 把不同目录分成独立空间，可设只读、独立口令；**v1.8.0 起可在电脑端「分区管理」里自助新建 / 编辑 / 删除分区，不必手改配置** | 只发布/列出单层文件，不展示子目录；越界路径一律拒绝 |

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

> v1.7 起每张卡片标题栏右侧有 **⌃ / ⌄** 折叠按钮，窗口右上角有「全部收起 / 全部展开」；
> v1.7.1 起收起**不改变窗口尺寸**，空出的区域直接露出背景，四张卡收起后仍是同样的标题行形态；
> 状态保存在 `settings.json`，详见下文「卡片折叠、卡片不透明度与多语言」。

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

## 外观自定义（v1.6 新增）

这一版只做界面与品牌，不改任何传输逻辑和 HTTP / MCP 接口。

### 电脑端：换背景

1. 点窗口右上角 **「更换背景」**，打开「外观 · 背景」面板。
2. 点 **「选择图片…」**，选一张本地图片即可立即生效。支持 **JPG / JPEG / PNG / WebP / BMP**。
3. **显示方式**四选一：
   | 方式 | 效果 |
   |---|---|
   | 铺满（默认） | 等比放大并居中裁切，铺满整个窗口，不留边 |
   | 适应 | 等比缩小到完整显示，四周留底色 |
   | 平铺 | 缩到约 480px 高后重复排列 |
   | 居中 | 保持原始大小居中；比窗口大时居中裁切 |
4. **背景遮罩浓度** 0–100%：越高背景越暗。卡片本身是深色面板，文字始终可读；默认 45%。
5. **恢复默认背景**：清空图片并把显示方式/浓度恢复为「铺满 / 45%」。

设置保存在数据目录（默认是 exe 同级的 `settings.json`）的 `background` 字段：

```json
{
  "background": { "path": "D:\\壁纸\\bg.jpg", "mode": "fill", "dim": 45 }
}
```

重启后自动生效。图片文件被删除或移动时，程序会安静地回退到默认背景，不会报错。

**性能**：背景图只在选择/启动时读盘一次，超过 2560px 的图片会先等比缩小再缓存；
窗口缩放时只重算尺寸，不会每次重绘都读盘，所以拖动窗口不会卡顿。

> Tkinter 的子控件不支持真正的 alpha 混合，所以卡片采用「深色面板 + 背景暗化层」来保证可读性；
> 背景图在卡片四周、卡片之间的间隙可见。

### 网页端：换背景

- 页面右上角 **图片图标** → 「外观 · 背景」。
- 点「选择图片…」选本地图片；图片以 dataURL 存进浏览器 **IndexedDB**（库名 `lan-transfer`，`media` 表），
  显示方式与浓度存在 `localStorage`（key `lan-transfer-bg`），刷新、关标签页后仍在。
- 显示方式与浓度含义和电脑端一致；「恢复默认背景」会同时清掉 IndexedDB 里的记录。
- 图片恢复是**异步**的（延迟 300ms 读取），不会阻塞已上传文件列表的加载；单张限制 8MB。
- 背景只存在本机浏览器，不上传、不写入服务端目录；换浏览器或清缓存即恢复默认。

### 安卓端：换背景

- 「连接服务器」卡片右上角 **「🖼 背景」** → 选择背景图片 / 恢复默认背景。
- 通过系统文件选择器（`ACTION_OPEN_DOCUMENT`）取图并申请持久读权限，Uri 存 `SharedPreferences`；
  大图按屏幕宽度降采样解码，避免 OOM；遮罩固定 45%。
- **限制**：本轮没有真机/模拟器，只做了 Gradle 构建与签名验证，未做运行时点击验证。

### 图标与品牌

- 源文件在 [`assets/branding/`](assets/branding/)：`icon.svg`（矢量设计稿）+ `make_icons.py`（生成脚本）。
- 重新生成（改配色只需改脚本顶部常量与 `icon.svg`）：

```bash
python assets/branding/make_icons.py
```

会输出 `icon-1024.png`、多尺寸 `icon.ico`（16/24/32/48/64/128/256，其中 16/24 用简化版保证小尺寸可辨）、
网页 favicon / logo、安卓 `mipmap-*` 各密度 launcher icon，并自动复制到 `desktop-client/`、`static/`、`android-app/`。
电脑端窗口图标、exe 图标、网页 favicon 与标题 logo、安卓桌面图标共用同一套图形。

## 卡片折叠、卡片不透明度与多语言（v1.7 新增）

这一版加三项界面能力：卡片折叠、卡片不透明度、7 语言切换。
传输逻辑、HTTP 接口、MCP 接口、上传下载协议都没有改。

### 卡片折叠

- 四张卡片（服务信息、扫码连接、发布文件、接收文件）标题栏右侧各有一个折叠按钮：
  **⌃ = 当前展开**（点击收起），**⌄ = 当前收起**（点击展开）。四张卡按钮的行高、位置、形状、
  颜色、标题字号与左边距完全一致（以「服务信息」卡为基准）。
- 收起后只保留一条标题行：左边标题，右边按钮，按钮左侧显示本卡关键摘要
  （服务信息 → 运行状态与端口；扫码连接 → 服务地址；发布文件 → 已发布 N 个文件；接收文件 → 收到 N 个文件）。
- **v1.7.1 起收起不改变窗口尺寸**（这是 v1.7.0「窗口高度自动收缩」的回退）：
  折叠任意卡片或点「全部收起」后，窗口外框保持当前大小，收起空出的区域直接露出背景，
  卡片仍按顺序堆叠在顶部，下方留白给背景，不会用空白面板填满。
- 窗口大小单独记录在 `settings.json` 的 `ui.window`（拖动窗口 500ms 后写入），重启恢复上次手动调整的尺寸；
  折叠状态变化不会写这个字段，因此不会因为折叠而改变窗口大小。
- 窗口右上角有 **「全部收起」/「全部展开」** 两个全局按钮。
- 折叠状态写入数据目录 `settings.json` 的 `ui.collapsed`，重启后保持：

```json
{
  "ui": {
    "collapsed": { "info": false, "qr": false, "pub": false, "recv": false },
    "card_opacity": 75,
    "card_opacity_set": true,
    "language": "zh-Hans",
    "window": { "w": 1120, "h": 688, "x": 123, "y": 60 }
  }
}
```

- 1366×768 屏幕下窗口约 1120×688，最小窗口 940×660；任何折叠组合都不出滚动条、内容不裁切。
- 网页端同步支持折叠：卡片标题右侧同样的 ⌃ / ⌄，状态存 `localStorage`（key `lan-transfer-collapse`），刷新后保持。

### 卡片不透明度（能透出背景，但不是真 alpha）

先把边界说清楚：**Tkinter 的子控件没有真正的 alpha 通道**，桌面端卡片不是真半透明，而是：

1. 从当前背景图在卡片所在矩形内采样，算出区域平均色；
2. 把采样色做一次固定提亮（`glass_target`：通道下限 46、再 +58），避免深色背景上低不透明度完全看不出差别；
3. 用「卡片不透明度」`alpha = opacity / 100` 作为**线性**权重混合卡片本色与提亮后的采样色，
   再对通道做上限限制（填充 150 / 斑马纹 168 / 描边 175），保证白字可读；
4. 得到这张卡片的实际填充色；背景图、显示方式、遮罩浓度、窗口尺寸、**滑块数值**任一变化都会重算。

效果：窗口四周和卡片之间的间隙露出背景原图，卡片色调随背景与滑块变化。
**v1.7.1 把默认值从 88% 降到 75%，范围从 60%–100% 放宽到 40%–100%**，
40% 与 100% 的卡片色差肉眼可辨（见下）。

- **v1.7.1 起顶栏常驻「卡片不透明度」滑块**（品牌区右侧，带实时百分比），不必进「更换背景」面板；
  「更换背景」面板里仍保留同一个滑块，两者双向同步。
- 值写入 `settings.json` 的 `ui.card_opacity`，同时写 `ui.card_opacity_set: true`；
  旧版本默认 88 且从未手动改过的配置会自动迁移到 75。
- 像素证据（同一张渐变背景、1120×688、遮罩 0%、采样点 `(350,104)` 服务信息卡标题行内）：

| 不透明度 | 采样 RGB | 相对上一档差值 |
|---|---|---|
| 40% | (71, 90, 150) | — |
| 60% | (63, 80, 134) | (−8, −10, −16) |
| 75% | (47, 61, 104) | (−16, −19, −30) |
| 100% | (22, 30, 54) | (−25, −31, −50) |

  单调递减，相邻档位每个通道至少差 8 个色阶（4 张截图在 `.screenshots/v171-opacity-*.png`）。
- 没有把卡片背景搬到 Canvas 自绘再叠控件：真实收益有限，却要重写整个布局，风险与「功能不回归」冲突，
  因此保留 Tkinter + 现有控件结构，只在配色层做混色。
- 网页端卡片原本就是 CSS 半透明深色 + `backdrop-filter`，滑块直接改 CSS 变量
  `--panel-a` / `--panel-2-a`（v1.7.1：40%–100%，默认 75%），与桌面端共用同一个不透明度参数，也存 `localStorage`；
  顶栏同样常驻一个「卡片不透明度」滑块。实测 `rgba(17, 26, 46, α)` 的 α 分别为 0.4 / 0.6 / 0.75 / 1.0。

### 语言切换

首批 7 种语言，三端文案都是完整的一套，不是补一半：

| 语言 | 代码 | 电脑端 | 网页端 | 安卓端 |
|---|---|---|---|---|
| 简体中文 | `zh-Hans` | ✅ | ✅ | ✅ |
| 繁體中文 | `zh-Hant` | ✅ | ✅ | ✅ |
| English | `en` | ✅ | ✅ | ✅ |
| 日本語 | `ja` | ✅ | ✅ | ✅ |
| 한국어 | `ko` | ✅ | ✅ | ✅ |
| Español | `es` | ✅ | ✅ | ✅ |
| Русский | `ru` | ✅ | ✅ | ✅ |

- 文案独立成资源文件，代码里不再有写死的中文字符串：
  - 电脑端 `desktop-client/i18n/*.json`（7 × 104 条）
  - 网页端 `desktop-client/static/i18n/*.json`（7 × 90 条）
  - 安卓端 `res/values*/strings.xml`（7 × 105 条，默认 `values/` 是英文）
- **缺词回退英文**：任何语言缺键都取英文文案（桌面端 `tr.py`、网页端 `LT.t`、安卓端 `values/` 兜底）。
- **默认跟随系统语言**：Windows 用 LANGID、浏览器用 `navigator.languages`、安卓用系统 Locale。
- **切换入口与生效方式**：
  - 电脑端：「更换背景」→「外观 · 背景」面板底部「语言」下拉。Tkinter 控件在创建时绑定文案，
    无法只换字符串不重建，所以选择后程序会**自动重启自身**（先停服务、再启动新进程，约 1 秒），
    并在面板里明确提示需要重启——这是技术限制，不是没做即时生效。
  - 网页端：同一面板里的语言下拉，**切换立即生效**；`<html lang>` 与页面标题同步更新；
    选择存 `localStorage`（key `lan-transfer-lang`）。
  - 安卓端：「🖼 外观」→「语言」，可选「跟随系统」或指定语言；
    通过 `AppCompatDelegate.setApplicationLocales` **立即生效并跨重启保存**。
- **长文案适配**：英文 / 俄文 / 西班牙文按钮明显更长，已做这些处理：
  桌面端按钮与表格列设最小宽度、窗口宽度小于 1020px 时隐藏卡片副标题、设备发现状态改为两行；
  安卓端按钮 `minWidth=0dp` + 单行 + 水平内边距；网页端按钮自动换行。
  四张卡片、按钮、表格表头、提示文字均已实测（zh / en / ja / ko / es / ru），无溢出、无裁切。

## 网页端与安卓端

### 网页端

- 打开链接时必须带口令：`https://IP:端口/?k=口令`；电脑上也可点「打开网页」。
- 支持点击选择或拖拽上传（多选、进度条、失败可单条重试）、下载、删除（删除前会确认）。
- 首次访问会提示「您的连接不是私密连接」——这是自签名证书，点「高级 → 继续前往」即可。
- 右上角可切换空间，默认「全部空间」；只读空间会标注「（只读）」，其中的文件只能下载。
- **下载断点持久化（v1.5）**：已收字节与 `ETag` 定期写入 IndexedDB（key 为「空间+文件名+大小」），
  下载中途刷新页面、关掉标签页后再点「下载」会提示「从上次断点继续：xxx」，从断点接着传；
  服务端文件变了（ETag 不同）会自动丢弃旧分片重新下载。浏览器不支持 IndexedDB 时回退为页面内存。

### 安卓端

- 安装 APK 后打开，**自动扫描局域网广播**（UDP 8766）并把发现的电脑列在地址栏下方；
  点选设备即填入地址（已有口令时直接连接）。扫码与手输地址仍然保留作为兜底。
- 自动发现失败时点「🔍 扫描」重试；仍无结果就检查手机与电脑是否同一 Wi-Fi、电脑是否在广播。
- 连接成功后会自动记住，下次打开自动重连。
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

# 9) 口令轮换：生成新口令并写回 token.txt，旧口令 30 分钟内仍可用
release\lan-transfer-cli.exe rotate-token --server https://10.0.0.5:8765 --token-file token.txt --grace 1800
```

- 公共参数：`--server`、`--space`、`--token-file`、`--token`、`--json`、`--verify-sha256` / `--no-verify-sha256`、`--resume`。
- `--resume` 行为：
  - `put`：按服务端返回的会话偏移继续发分片（每片 8MB），状态记录在 `<源文件>.ltupload.json`；
    服务端会话过期（30 分钟）会重建会话从头传，旧服务端则回退一次性 `POST /api/upload`。
  - `get`：用 `<文件名>.part` 记录已下载字节与 `ETag`，带 `Range` / `If-Range` 请求续传；
    完成后校验整文件 sha256 再改名为正式文件。服务端文件已变化时会丢弃 `.part` 从头下载。
- `discover` 参数：`--timeout`（默认 3 秒）、`--discovery-port`（默认 8766）、`--any-subnet`（不做同网段过滤）；
  默认只接受与本机同一 `/24` 网段的广播。
- `rotate-token`：`--grace` 指定旧口令宽限期（秒，默认 1800）；带 `--token-file` 时会把新口令写回该文件。
  服务端同时把新口令与宽限期写入 `settings.json`，重启后仍生效。
- 环境变量：`LAN_TRANSFER_SERVER`、`LAN_TRANSFER_TOKEN_FILE`、`LAN_TRANSFER_TOKEN`。
- `--json` 输出包含 `sha256`、`bytes`、`elapsed_ms` 等字段；续传时另有 `resumed`、`resumed_from`、`chunks`。
- 进度输出到 stderr：`已传/总量 百分比 速率(MB/s) 剩余时间`，不影响 `--json` 的 stdout。
- 退出码：`0` 成功、`1` 一般失败、`2` 参数错误、`3` 口令/权限错误、`4` 网络错误、`5` sha256 不一致、`6` 文件或空间不存在。
- CLI 对自签名证书不做主机名校验（与局域网使用场景一致），内容完整性依赖 sha256 校验。

## MCP 服务器（让 AI 直接调用，v1.5 新增）

`desktop-client/lan_transfer_mcp.py` 通过 **stdio** 暴露 7 个工具，AI（Claude Desktop、Cursor 等）
可以直接「发现服务 → 列空间 → 上传/下载/删除」，网络逻辑完全复用 CLI，不拼命令行。

| 工具 | 作用 |
|---|---|
| `health` | 服务是否在线、版本、能力（免口令） |
| `spaces` | 列出空间 |
| `ls` | 列出文件 |
| `put` | 上传（多文件、目录递归、续传、sha256 校验） |
| `get` | 下载（断点续传、sha256 校验） |
| `rm` | 删除文件 |
| `discover` | 局域网 UDP 发现（免口令） |

启动：

```powershell
python desktop-client\lan_transfer_mcp.py --server https://10.0.0.5:8765 --token-file token.txt --space inbox
```

Claude Desktop 配置（`%APPDATA%\Claude\claude_desktop_config.json`）：

```json
{
  "mcpServers": {
    "lan-transfer": {
      "command": "python",
      "args": [
        "D:\\文件传输\\desktop-client\\lan_transfer_mcp.py",
        "--server", "https://10.0.0.5:8765",
        "--token-file", "D:\\文件传输\\desktop-client\\token.txt",
        "--space", "inbox"
      ]
    }
  }
}
```

- 每个工具返回结构化 JSON（文件列表、字节数、sha256、耗时），失败时 `isError: true` +
  中文 `message` 与 `error_code`（`UNAUTHORIZED` / `NOT_FOUND` / `NETWORK_ERROR` / `CHECKSUM_MISMATCH` 等）。
- 完整工具参数、返回示例与「AI 从发现到上传下载」的示例对话脚本见 [docs/mcp.md](docs/mcp.md)。

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
| `--no-tls` | 关 | 以 HTTP 明文运行（仅本机调试，例如无头浏览器验证网页功能）；默认强制 HTTPS |

- **PID 文件**：`<数据目录>\lan-transfer.pid`，启动成功后写入，收到 SIGINT / SIGTERM / SIGBREAK 优雅退出时删除。
- 日志为 JSON Lines（每行一条），上传审计单独写入 `logs/upload-audit-YYYY-MM-DD.log`。
- **分片上传会话持久化（v1.5）**：会话元数据写入 `<数据目录>\upload-sessions.json`，服务重启后自动恢复，
  客户端续传不受影响；超过 30 分钟无更新的会话照旧清理。
- **上传并发上限（v1.5）**：同时写入的上传默认最多 4 个，超出的请求返回 `429` 与中文提示。
- 启动后可访问 `/api/health` 查看版本、实际端口、是否发生端口顺延、空间列表与能力标记。

## 多空间（目录隔离）

## 自定义分区（v1.8 新增）

从 v1.8 起**不需要手改 `spaces.yaml`**：在电脑端窗口右上角点 **「分区管理」**，
就能新建分区、指定文件夹、切换读写 / 只读、改描述、设默认、删除。

### 怎么建

1. 点顶栏「分区管理」→「新建分区」。
2. 填名称（字母、数字、`_`、`-`，1–40 字符，不能用 `CON` / `PRN` / `AUX` / `NUL` / `COM1-9` / `LPT1-9` 这些 Windows 保留名）。
3. 点「选择…」挑文件夹，也可以直接填路径——不存在的目录会自动创建。
4. 选「读写」或「只读」，可填描述，保存。

名称重复、路径是已有文件、路径不可写、指向系统目录（`C:\Windows`、`System32`、
`Program Files`、`ProgramData`、用户根目录）或盘符根目录时，表单下方直接给出中文原因，不写配置。

### 怎么切

- 「发布文件」和「接收文件」两张卡片，列表上方都有 **「分区」下拉**，切换后列表立刻换成该分区的目录内容；
  「添加文件 / 添加文件夹」也复制到当前选中的分区，「打开文件夹」打开对应目录。
- 网页端和安卓端的分区下拉会自动出现新分区；只读分区在界面上标注并禁用上传。
  网页端不支持新建分区，界面上直接写明「分区请在电脑端『分区管理』中维护」。
- 电脑端改动**立即生效，不需要重启服务**；已连接的客户端下一次请求即可看到新分区。

### 删除会不会删文件

**不会。** 删除只移除 `spaces.yaml` 里的一条配置，磁盘目录和里面的文件原样保留，
确认框里也写明了「只移除配置，磁盘上的文件不会被删除」。

### 默认分区规则

- 默认分区是「上传不指定分区时落到哪里」，列表里标「默认」。
- 默认分区**不允许删除**，要先在列表里选另一个分区点「设为默认」，再删旧的。
- 至少保留一个分区，删到只剩一个时会被拒绝。
- 没有 `spaces.yaml` 时仍按老行为自动生成 `inbox` / `shared` 两个分区，向后兼容。

### 配置文件写坏怎么办

`spaces.yaml` 解析失败时程序**不会崩**：启动时回退到 `inbox` / `shared` 两个最小可用分区，
「分区管理」窗口顶部会用红字提示原因，原文件不会被静默覆盖；你在界面里保存任意修改时才会重写它。
写入采用「先写临时文件再 `os.replace`」的原子替换，写到一半断电也不会留下半个坏文件。
写回时会保留文件头注释和每个分区上方的注释。

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
| `D:\lan-transfer\settings.json` | 桌面端设置（「在局域网内可见」开关、轮换后的口令与旧口令宽限期）；headless 默认仍开启广播，可用 `--no-discovery` 关闭 |
| `D:\lan-transfer\upload-sessions.json` | 未完成的分片上传会话（v1.5），服务重启后据此恢复；超时会自动清理 |
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
- **口令轮换**：`rotate-token` 生成新口令，旧口令在宽限期内（默认 30 分钟）仍可用，
  便于多设备平滑切换；宽限期记录在 `settings.json`，重启后仍然有效。
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
- 网页端：下载失败会自动重试 3 次；已收部分会定期写入 IndexedDB，**刷新页面或关掉标签页后再点「下载」也会从断点继续**；
  服务端文件变了（ETag 不同）会自动丢弃旧断点重下。
- 安卓端：下载中断会保留 `.part`，再次点该文件的下载按钮即从断点续传。
- 大文件建议靠近路由器，避免 Wi-Fi 信号弱导致中断。
- 服务端文件在中断期间被修改（mtime / 大小变化）时，续传会自动放弃断点、从头下载并使用新的 sha256 校验。

**9. 怎么让 AI 直接调用传输能力**

- 用 `desktop-client/lan_transfer_mcp.py`（stdio MCP 服务器），在 Claude Desktop / Cursor 的
  `mcpServers` 里配置 `command` + `args` 即可，7 个工具开箱可用。
- 配置片段、工具返回结构、错误码和完整示例对话见 [docs/mcp.md](docs/mcp.md)。

**10. `discover` 找不到局域网内的服务**

- 确认电脑端「在局域网内可见」开关是「已广播」（或 headless 未加 `--no-discovery`）。
- 确认防火墙允许 UDP 8766（Windows 首次运行只放行 TCP 时可能漏掉 UDP）。
- 手机/电脑在同一网段；跨网段或虚拟网卡较多时可加 `--any-subnet` 看是否能看到。
- 也可以在 `discover` 前先用 `health --server https://IP:8765` 验证网络连通。

## 项目结构

```
.
├── desktop-client/                  # 电脑端（Python 3 + Tkinter）
│   ├── main_gui.py                  # 桌面 GUI（含背景自定义）
│   ├── lan_transfer.py              # 核心服务（HTTP/HTTPS、空间、上传下载、口令、headless）
│   ├── lan_transfer_cli.py          # 命令行客户端
│   ├── lan_transfer_mcp.py          # MCP 服务器（stdio，供 AI 直接调用）
│   ├── icon.ico                     # exe / 窗口图标（由 assets/branding 生成）
│   ├── static/                      # 网页端（index.html / app.js / style.css / qrcode.js + favicon/logo）
│   ├── gen_mtls.py                  # 生成自签名证书（可选）
│   ├── run_desktop.bat              # 免打包，直接源码运行
│   ├── lan-transfer-desktop.spec    # 桌面端 PyInstaller 配置
│   └── lan-transfer-cli.spec        # 命令行 PyInstaller 配置
├── assets/branding/                 # 品牌图标源文件
│   ├── icon.svg                     # 矢量设计稿（改配色参考）
│   ├── make_icons.py                # 生成 ICO / PNG / favicon / 安卓 mipmap
│   ├── icon.ico                     # 多尺寸（16/24/32/48/64/128/256）
│   └── icon-1024.png                # 1024×1024 PNG 源图
├── docs/api.md                      # HTTP / API 契约文档
├── docs/mcp.md                      # MCP 工具、配置与示例对话
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
