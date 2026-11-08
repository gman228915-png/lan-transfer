# 更新记录

## v1.6.0（2026-11-08）

这一版**只做界面与品牌视觉**：不改传输逻辑、不改 HTTP / MCP 接口、不动上传下载协议。
旧版客户端仍可与新版服务端互操作，`settings.json` 只新增 `background` 字段。

### 新增

- **电脑端自定义背景**：窗口右上角「更换背景」→「外观 · 背景」面板。
  支持本地 JPG / JPEG / PNG / WebP / BMP；显示方式四选一（铺满 / 适应 / 平铺 / 居中）；
  遮罩浓度 0–100%（默认 45%）；「恢复默认背景」一键还原。
  配置写入数据目录 `settings.json` 的 `background` 字段（`path` / `mode` / `dim`），重启后生效。
  背景原图只在选择/启动时读盘，超过 2560px 先等比缩小并缓存，缩放窗口不重复读盘。
- **网页端自定义背景**：右上角图片按钮 →「外观 · 背景」；本地选图后以 dataURL 存入
  IndexedDB（库 `lan-transfer` 新增 `media` 表，DB 版本 1→2 平滑升级），显示方式与浓度存
  `localStorage`（key `lan-transfer-bg`）；异步恢复，不阻塞文件列表首屏；单张限 8MB；
  「恢复默认背景」同步清 IndexedDB。背景不会上传到服务端。
- **安卓端自定义背景**：连接卡片右上角「🖼 背景」→ 选择图片 / 恢复默认；
  `ACTION_OPEN_DOCUMENT` + 持久读权限，Uri 存 `SharedPreferences`，大图按宽度降采样解码。
- **品牌图标**：新增 `assets/branding/icon.svg`（矢量设计稿）与 `make_icons.py`（生成脚本）。
  输出多尺寸 `icon.ico`（16/24/32/48/64/128/256）与 `icon-1024.png`；16/24px 使用简化图形
  （去掉设备、加粗箭头）保证小尺寸可辨。exe（PyInstaller `icon=`）、窗口左上角（`iconbitmap`）、
  任务栏（`iconphoto`）、网页 favicon / 标题 logo、安卓 launcher（mipmap 各密度）统一使用同一图形。

### 改进

- 桌面端布局改为画布承载：背景图铺在底层，四张卡片浮在上面，四周与卡片间隙可见背景；
  窗口按屏幕自适应（1366×768 下窗口约 1120×688，不出滚动条、内容不裁切）。
- 统一卡片内边距、标题字号与描边；服务信息卡按内容行数自适应高度，长路径不再被截断。
- 二维码按窗口可用高度自动选择模块尺寸，窗口变矮时二维码缩小而下方访问链接始终完整可见；
  文件列表滚动条改为深色，与整体配色一致。
- 所有按钮补 hover / 按下态（桌面端 `Enter/Leave` 变色 + 激活色，网页端 `:hover/:active`）。
- 网页端卡片、上传项、文件行改为半透明深色 + `backdrop-filter` 模糊，任意背景上都能读；
  网页端与桌面端统一主色 `#5B8CFF` / 强调色 `#31D6B0`。
- 版本号升到 1.6.0（服务端 / CLI / MCP / GUI / APK，Android `versionCode` 4 → 5）。

### 验证

- 桌面端：默认背景、自定义背景、1366×768 窗口三种状态实拍截图（`.screenshots/`）；
  重启后背景与显示方式仍生效；恢复默认可回到无背景；大图缩放缓存不重复读盘（`_bg_src_key` 命中）。
- 网页端：浏览器实测 logo 加载、favicon 为 `image/svg+xml`、「外观 · 背景」面板打开、
  显示方式改为「适应」（`data-mode="fit"`）、浓度 70%（`--bg-dim=0.644`）、文件列表正常、无 JS 报错。
- 安卓端：Gradle 构建 + 签名通过；**无真机/模拟器，背景入口仅构建验证**。
- 图标：打包版 exe 用 `[System.Drawing.Icon]::ExtractAssociatedIcon` 导出 32×32 目检为新图标；
  打包版窗口实拍确认标题栏左上角与任务栏使用同一图标（`iconbitmap` + `iconphoto`）。
- 未改接口：`/api/health` capabilities 与 CLI / MCP 参数均未变化。

### 已知限制

- Tkinter 子控件不支持真正的 alpha 混合：桌面端卡片用不透明深色面板 + 背景暗化层模拟「半透明」，
  背景在卡片间隙与窗口四周可见。
- 安卓端背景未做真机点击验证；背景图 Uri 失效时自动回退默认。
- 桌面端没有上传进度条（上传在网页端/安卓端进行），因此「进度条配色统一」仅适用于网页端现有进度条。

## v1.5.0（2026-11-06）

### 新增

- **MCP 服务器（`desktop-client/lan_transfer_mcp.py`）**：stdio 传输、零第三方依赖，
  直接复用 CLI 的 HTTP 逻辑（不拼接命令行）。提供 7 个工具：
  `health` / `spaces` / `ls` / `put`（多文件 + 目录递归 + 续传）/ `get`（断点续传 + sha256）/
  `rm` / `discover`。参数支持 `server`、`space`、`token`、`token_file`、`path`、`dest`、`verify_sha256`；
  返回结构化 JSON（文件列表、字节数、sha256、耗时）；失败时 `isError: true` + 中文 `message` 与
  `error_code`（`UNAUTHORIZED` / `NOT_FOUND` / `NETWORK_ERROR` / `CHECKSUM_MISMATCH` / `TOO_MANY_UPLOADS` 等）。
  文档：新增 [docs/mcp.md](docs/mcp.md)（Claude Desktop / 通用客户端配置 + 完整示例对话）。
- **网页端下载断点持久化**：已收字节与 `ETag` 定期写入 IndexedDB，key 为「空间+文件名+大小」；
  刷新页面或关闭标签页后再点下载可从断点继续；服务端文件变了（ETag 不同）自动丢弃旧分片重下；
  启动时清理 3 天前的旧记录；IndexedDB 不可用时回退页面内存。
- **分片上传会话跨服务重启恢复**：会话元数据（id、文件名、size、space、已确认 offset、sha256、
  分片数）原子写入 `<数据目录>/upload-sessions.json`；启动时校验临时文件并截断多余半截写入后恢复；
  30 分钟超时清理规则不变，并同步更新持久化文件。
- **安卓端 UDP 自动发现**：连接 Wi-Fi 后自动监听 UDP 8766 广播，发现的设备填入地址列表（Spinner），
  点选即填入地址（已有口令时直接连接）；保留扫码与手输作为兜底；新增「🔍 扫描」按钮。
  新增 `LanDiscovery.kt` 与 `CHANGE_WIFI_MULTICAST_STATE` 权限；APK 版本 1.5.0（versionCode 4）。
- **口令轮换（平滑失效）**：新增 `POST /api/token/rotate?grace=<秒>` 与 CLI `rotate-token`。
  生成新口令，旧口令在宽限期内（默认 30 分钟，最长 7 天）仍可用；新口令与宽限期写入 `settings.json`
  并在重启后生效；带 `--token-file` 时自动写回新口令。
- **服务端上传并发上限**：一次性上传与分片 `PUT` 共用 4 个写入槽位，超出返回
  `429 {"error": "当前上传任务过多，请稍后重试（服务端并发上限 4）", "code": "busy"}`。
- **`--no-tls`**：headless / 源码启动可选 HTTP 明文（仅本机调试），默认仍强制 HTTPS。
- `/api/health` capabilities 新增 `session_persist`、`upload_limit`、`token_rotate`。

### 实测（v1.5.0）

- MCP：14 个 JSON-RPC 请求（initialize / tools/list / 7 个工具 + 批量上传 + 错误分支）全部返回结构化结果；
  错误分支返回 `NETWORK_ERROR` + 中文 message；`discover` 实测 4.0s 找到 1 台设备并给出 `suggested_server`。
- 网页刷新续传：1GB 文件下载到 102.0 MB 时刷新页面，再点下载提示「从上次断点继续：102.0 MB」，从 17% 继续。
- 重启续传：256MB 上传到 184MB（192,937,984 B）中断 → 停止服务 → 重启（日志 `upload_sessions_restored`）
  → `put --resume` 继续剩余 64MB，耗时 1.1s，sha256 校验通过。
- 429：4 个慢速上传占用槽位后，第 5 个请求返回 `429` + 中文提示。
- 口令轮换：`rotate-token` 生成新口令并写回文件，旧口令在宽限期内调用 `ls` 仍成功，新口令同样成功。
- 安卓端：Gradle 构建通过并签名，APK 7,092,602 字节；**无真机，自动发现仅完成代码与构建验证**。

### 兼容

- 所有 HTTP 接口向后兼容；旧客户端不传新参数时行为与 v1.4.0 一致。
- 新增文件 `desktop-client/lan_transfer_mcp.py` 与 `docs/mcp.md`，不影响旧部署。
- `settings.json` 新增 `token` / `token_history` 字段；旧的 `discovery` 字段继续有效。
- `upload-sessions.json` 可安全删除（仅失去未完成会话的恢复能力）。

### 已知限制 / 计划中

- MCP 服务器以源码运行，需要 Python 3.9+；暂未单独打包 exe。
- MCP `discover` 会等满 `timeout` 才返回（beacon 每 3 秒一发），默认 3 秒。
- 服务端上传并发上限固定为 4，暂未做成配置项。
- 口令轮换针对全局口令；各空间独立 `token` 仍需手工修改配置。
- 安卓自动发现未在真机验证（无设备），仅通过编译与签名验证。

## v1.4.0（2026-11-06）

### 新增

- **下载断点续传**：`get --resume` 正式实装。客户端使用 `<文件名>.part` + `Range` / `If-Range` 从断点继续，
  完成后校验整文件 sha256 再改名为正式文件；服务端返回 `206` + 正确的 `Content-Range`、
  `Accept-Ranges`、`ETag`、`Last-Modified`，越界返回 `416`；文件已变化时忽略 Range 返回 `200`。
- **上传断点续传（分片会话）**：新增 `POST /api/upload/session`（申请上传 id）、
  `PUT /api/upload/session/<id>?offset=`（按片追加，偏移不符返回 `409`）、
  `GET /api/upload/session/<id>`（查询偏移，供崩溃后恢复）、`POST …/commit`（校验 offset/sha256 后落盘）、
  `DELETE …`（放弃上传）。默认分片 8MB；会话 30 分钟无分片自动清理，启动时清理遗留 `.lt-upload-*.part`。
- **网页端**：下载改为 `fetch` + `ReadableStream` 分片接收，失败自动重试 3 次，仍失败可再点下载从断点继续；
  上传显示速率（MB/s）与剩余时间，并发上限 2。
- **安卓端**：连接后拉取 `/api/spaces` 显示空间下拉（「全部空间」保持旧行为）；
  选中空间后列表/上传/下载均带 `space`；只读空间禁用上传并提示；
  下载使用 `.part` + `Range` 断点续传；上传并发上限 2，进度显示速率与剩余时间。
- **局域网自动发现**：服务端每 3 秒向 UDP 8766 广播 beacon（版本、端口、空间数量、
  是否端口顺延，不含口令）；CLI 新增 `discover`（默认只接受同 `/24` 网段广播，`--any-subnet` 关闭过滤）；
  GUI 新增「在局域网内可见（UDP 广播发现）」开关，默认开启，状态持久化到 `settings.json`；
  headless 新增 `--no-discovery`。
- **上传审计**：`logs/upload-audit-*.log` 增加 `resumed`、`chunked`、`session` 字段。
- **CLI 进度**：上传/下载在 stderr 显示已传/总量、百分比、速率（MB/s）与剩余时间；
  异常中断给出中文提示并说明如何续传。

### 兼容

- 旧客户端不传新参数仍可用：一次性 `POST /api/upload` 保留；不带 `space` 的列表/上传/下载仍走原有行为。
- 旧服务端（无会话接口）时，CLI `put --resume` 自动回退一次性上传。
- `/api/health` 新增 `discovery` 与 capabilities（`http_range`、`if_range`、`resume`、
  `upload_session`、`upload_chunk_size`、`discovery`），老字段不变。

### 已知限制 / 计划中

- 网页端下载断点保存在页面内存，**刷新页面后需重新下载**（后续计划改用 IndexedDB 持久化分片）。
- 安卓端暂无「局域网发现」入口，仍需扫码或手输地址（后续计划接入 UDP 发现）。
- 分片上传会话不会跨服务重启恢复（重启后请重新开始上传）。
- Android APK 的「空间下拉」依赖应用重新编译；未重新构建 APK 的旧安装包仍是固定默认空间。

## v1.3.1（2026-11-05）

### 修复

- **区分两个选择器**：「添加文件」对话框标题为「选择要发布的文件（可多选）」，
  「添加文件夹」为「选择要发布的文件夹（整个目录一起发布）」，不再让人误以为要选文件却弹出文件夹框。
  文件对话框第一项固定为「所有文件 (*.*)」并默认选中，`.pptx`、`.mp4`、`.zip`、无后缀文件都能选。
- **「添加文件夹」现在真的把整个目录发布出去**：递归读取目录内全部文件（不按后缀过滤、含子目录），
  平铺复制到发布目录，完成后提示「已发布 N 个文件」。此前是整目录拷成子文件夹，
  列表不展示子目录内容，看起来像“检测不到文件”。
- 发布列表上方新增说明：文件用「添加文件」，整目录用「添加文件夹」，两者都支持任意格式。
- 发布列表高度调整为 6 行，避免文件较多时首末行被裁掉。

### 已知限制

- 文件夹选择器仍是 Windows 文件夹对话框，只能选目录（系统行为）。
- 文件夹发布采用平铺方式，不保留原始目录层级；同名文件自动改名（`a (1).txt`）。

## v1.3.0（2026-11-05）

### 新增

- **文件类型不再受限**：电脑端「添加文件」默认使用「所有文件」，可选择 `.pptx`、`.mp4`、`.zip`、无后缀文件等任意类型；
  仍保留「常用文档 / 图片 / 音视频 / 压缩包」作为快捷筛选项。
- 电脑端窗口标题与顶栏显示版本号（v1.3.0）。

### 修复

- 电脑端文件列表（发布 / 接收）文字统一为白色，交替行保留斑马纹，选中行为蓝色背景 + 白字，
  表头同套配色，不再出现灰底浅字看不清的情况。
- 修正窗口底部被裁切、右下角提示文字被右边界截断、按钮文字溢出等问题：
  窗口调整为 1080×780，列宽自适应（无横向滚动条），提示文字支持自动换行。
- 二维码卡片内的访问链接不再被窗口底部截断。
- 文件夹选择器旁新增说明：只能选目录，零散文件请用「添加文件」，避免误操作。

### 已知限制

- 文件夹选择器为 Windows 文件夹对话框，只能选择目录（这是系统行为，未改动）。
- 安卓端仍固定使用默认空间（inbox），暂不支持切换空间。
- 命令行客户端 `--resume` 参数仅预留，尚未实现断点续传。
- 单个文件（含文件夹内文件）平铺展示，不保留原始目录层级。

## v1.2.0（2026-11-05）

### 新增

- **无界面服务模式** `--headless`：支持 `--port`、`--bind`、`--token-file`、`--spaces-file`、
  `--data-dir`、`--log-file`；写 PID 文件、结构化日志，SIGINT/SIGTERM 优雅退出。
- **健康检查接口**：`/api/health`（版本、监听地址、端口、是否改端口、空间数量、能力标记）与 `/api/version`。
- **命令行客户端** `lan-transfer-cli`：`put`（多文件/目录 `-r`）、`get`、`ls`、`rm`、`spaces`、`health`；
  支持 `--json`、`--verify-sha256`、退出码区分失败原因，上传/下载后校验 sha256。
- **多空间隔离**：`spaces.yaml` / `spaces.json` 配置 `name`、`path`、`mode`（ro/rw）、独立 `token`、
  `description`；越界路径拒绝，只读空间写操作返回 403；首次启动自动生成示例配置。
- **网页端空间切换**：右上角下拉选择空间；电脑端窗口显示空间列表与目录。
- **接口文档** `docs/api.md` 与上传审计日志 `logs/upload-audit-YYYY-MM-DD.log`
  （时间、空间、文件名、字节数、来源 IP、成功/失败、sha256）。

### 兼容

- 不带 `space` 参数的旧网页端 / 安卓端仍可正常使用，上传默认落到 inbox 空间。
