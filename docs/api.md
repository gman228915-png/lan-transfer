# 局域网互传 API（v1.5）

服务默认使用 **HTTPS**（自签名证书）与**局域网口令**。所有接口返回 JSON（`/` 与 `/download` 除外）。

- Base URL：`https://<电脑局域网IP>:<端口>`
- 鉴权（任选其一，推荐请求头）：
  - `Authorization: Bearer <token>`
  - Query：`?k=<token>`
  - Cookie：`k=<token>`
- 全局口令可以访问所有空间；若空间配置了独立 `token`，该 token 只能访问它自己的空间。
- 错误格式：`{"error": "中文错误信息"}`，含 `code` 字段的接口会额外返回机器可读错误码。

## 版本与健康检查

### `GET /api/health`

公开接口，无需口令。

```json
{
  "ok": true,
  "app": "lan-transfer",
  "version": "1.5.0",
  "api_version": 1,
  "listen": "10.113.107.46",
  "port": 8765,
  "port_requested": 8765,
  "port_fallback": false,
  "scheme": "https",
  "lan_only": true,
  "spaces": 2,
  "default_space": "inbox",
  "token_required": true,
  "discovery": {"enabled": true, "port": 8766, "interval": 3.0},
  "capabilities": {
    "spaces": true,
    "per_space_token": true,
    "read_only_space": true,
    "sha256": true,
    "upload_audit": true,
    "http_range": true,
    "if_range": true,
    "headless": true,
    "resume": true,
    "upload_session": true,
    "upload_chunk_size": 8388608,
    "discovery": true,
    "session_persist": true,
    "upload_limit": 4,
    "token_rotate": true
  }
}
```

- `resume` / `http_range` / `if_range`：下载支持 `Range`、`If-Range` 与 `206`。
- `upload_session`：分片上传会话；`upload_chunk_size` 为建议分片大小（客户端可自定）。
- `discovery`：UDP 广播发现是否开启；beacon 只含版本、端口、空间数量等元信息，**不含口令**。
- `session_persist`：分片上传会话会落盘（`upload-sessions.json`），服务重启后仍可续传。
- `upload_limit`：服务端同时写入的上传数上限，超出返回 `429`。
- `token_rotate`：支持 `POST /api/token/rotate` 平滑轮换口令。

> 调试：`python lan_transfer.py --headless --no-tls` 可用 HTTP 明文启动（仅建议本机调试，
> 例如让无头浏览器验证网页端功能）；默认仍强制 HTTPS。

### `GET /api/version`

公开接口，无需口令，返回 `{"app","version","api_version"}`。

## 空间

### `GET /api/spaces`

需要口令。全局口令返回全部空间；空间独立口令只返回它有权访问的空间。

```json
{
  "default": "inbox",
  "version": "1.8.1",
  "spaces": [
    {
      "name": "inbox",
      "mode": "rw",
      "description": "手机或其他设备上传到这里",
      "path": "D:\\文件传输\\received-files",
      "files": 3,
      "size": 1024,
      "default": true,
      "has_own_token": false
    }
  ]
}
```

### 分区管理接口（v1.8.0 新增）

四个写接口**都要求全局口令**（`?k=`、`Authorization: Bearer` 或 `Cookie: k=`），
请求体为 JSON（`Content-Type: application/json`）。改完立即重载配置，**不需要重启服务**。

| 方法 | 路径 | 作用 |
|---|---|---|
| `POST` | `/api/spaces` | 新建分区 |
| `PATCH` | `/api/spaces/<name>` | 编辑分区（改名 / 改路径 / 改权限 / 改描述） |
| `DELETE` | `/api/spaces/<name>` | 删除分区（只移除配置，不删磁盘文件） |
| `POST` | `/api/spaces/default` | 设置默认分区 |

新建请求体：

```json
{ "name": "media", "path": "D:\\共享\\media", "mode": "rw", "description": "电影与剧集" }
```

编辑请求体：字段均可选，只传要改的（`name` 表示改名）。

```json
{ "name": "movies", "path": "D:\\共享\\movies", "mode": "ro", "description": "只读归档" }
```

设置默认：

```json
{ "name": "media" }
```

成功统一返回当前全部分区：

```json
{ "ok": true, "default": "media", "spaces": [ { "name": "media", "mode": "rw", "files": 2, "size": 40960, "default": true, "path": "D:\\共享\\media" } ] }
```

校验失败返回 `400`，中文可读原因：

| `error` 示例 | 触发条件 |
|---|---|
| `分区名不能为空` | name 为空 |
| `分区名只能包含字母、数字、下划线、连字符（1–40 个字符）` | 含 `/`、空格、控制字符等 |
| `分区名 CON 是 Windows 保留名，请换一个` | CON / PRN / AUX / NUL / COM1-9 / LPT1-9 |
| `分区名已存在：media` | 重名 |
| `不能使用盘符根目录作为分区：C:\` | 路径为盘符根 |
| `不能把分区指向系统目录：C:\Windows` | Windows / System32 / Program Files / ProgramData / 用户根目录 |
| `该路径是一个文件，请选择文件夹：...` | 路径已存在且是文件 |
| `目录不可写：...` | 无写权限 |
| `默认分区不能删除，请先把其他分区设为默认` | 删除默认分区 |
| `至少需要保留一个分区` | 删除最后一个 |

没有全局口令时返回 `403 {"error": "需要全局口令才能管理分区", "code": "unauthorized"}`。
只读分区的上传仍返回 `403 {"code": "read_only"}`，与 v1.7 一致。

### `GET /api/fs/dirs?path=<绝对路径>`（v1.8.1 新增）

手机端新建分区时用来浏览**电脑上**的文件夹。只读接口：只列子目录，不读文件内容，
不返回文件列表以外的元数据。需要**全局口令**（与分区管理接口一致）。

- `path` 可省略，省略时返回当前用户主目录。
- 返回当前路径 `path`、父级 `parent`（盘符根或不可用时为空串）、当前目录是否可写 `writable`、
  子目录 `dirs`（每项只有 `name` 与 `writable`）、是否被截断 `truncated`、
  单次上限 `limit`（500）以及可切换的盘符根 `roots`。

```json
{
  "path": "C:\\Users\\me\\Desktop",
  "parent": "C:\\Users\\me",
  "writable": true,
  "truncated": false,
  "limit": 500,
  "roots": ["C:\\", "D:\\"],
  "dirs": [
    {"name": "项目", "writable": true},
    {"name": "只读资料", "writable": false}
  ]
}
```

安全限制（越界统一返回 `400 {"error": "<中文原因>", "code": "invalid"}`）：

| 限制 | `error` 示例 |
|---|---|
| 系统目录及其子目录 | `不允许浏览系统目录：C:\Windows` |
| 回收站 / 系统保留目录 | `该目录属于回收站或系统保留目录，不允许浏览` |
| 其他用户的用户目录 | `只允许浏览当前用户目录：C:\Users\me` |
| 路径不存在 | `路径不存在：C:\not-exist` |
| 路径不是文件夹 | `该路径不是文件夹：C:\a.txt` |

- 一次最多返回 500 条子目录（按名称排序），超出时 `truncated=true`。
- 禁止进入 `C:\Windows`、`System32`、`Program Files`、`ProgramData`、`$Recycle.Bin` 等位置，
  这些目录也不会出现在列表里。
- 无全局口令返回 `403 {"error": "需要全局口令才能浏览电脑目录", "code": "unauthorized"}`。
- 盘符根自身允许浏览（用于进入下面的文件夹），但**不能**直接作为分区根目录（见上）。

## 文件

### `GET /api/files?space=<name>`

- `space` 可选。
- 带 `space`：列出该空间文件（`space` 独立口令也可用）。
- 不带 `space`：兼容旧前端，列出**所有空间**文件，需要全局口令。
- 每项包含：`name`、`size`、`mtime`、`kind`、`source`、`space`。

```json
{"files": [{"name": "a.txt", "size": 12, "mtime": 1762300000,
            "kind": "doc", "source": "receive", "space": "inbox"}]}
```

### `GET /download?name=<filename>&space=<name>&checksum=1`

- `space` 可选；不带时兼容旧前端，在所有空间里查找（需要全局口令）。
- 支持单段 `Range: bytes=start-end` / `bytes=start-` / `bytes=-N`，返回 `206`；
  越界返回 `416` + `Content-Range: bytes */<size>`。
- 响应头：`Accept-Ranges: bytes`、`Content-Range`、`ETag`、`Last-Modified`、
  `X-File-Size`、`X-Space`。
- `If-Range`：把上次响应里的 `ETag`（或 `Last-Modified`）原样带回；
  文件未变化时返回 `206`，已变化时忽略 Range 返回 `200` 完整文件。
  客户端收到 `200` 应丢弃已有的 `.part`，从头写。
- `checksum=1` 时额外返回 `X-File-SHA256`（会先完整读一遍文件，大文件慎用）。
- `inline=1` 时 `Content-Disposition` 为 `inline`（用于网页缩略图）。

示例（断点续传）：

```bash
# 第一次：假设已收到 104857600 字节
curl -k -H "Authorization: Bearer <token>" -H "Range: bytes=104857600-" \
     -H 'If-Range: "104857600-1762300000"' \
     "https://10.0.0.5:8765/download?name=big.bin&space=shared&checksum=1" -o big.bin.part
```

### `GET /api/hash?name=<filename>&space=<name>`

返回 `{"name","space","size","sha256"}`，需要对应空间访问权。

### `POST /api/upload?name=<filename>&space=<name>`

- Body 为原始字节流，必须带 `Content-Length`（不支持 chunked）。
- `space` 可选，默认空间；`mode=ro` 返回 `403`。
- 同名文件自动改名（`a (1).txt`），响应：

```json
{"ok": true, "name": "a.txt", "size": 12, "space": "inbox",
 "sha256": "…"}
```

## 分片上传会话（v1.4 新增）

适合大文件与断点续传；旧客户端仍可继续用一次性 `POST /api/upload`。

### `POST /api/upload/session?name=<filename>&space=<name>&size=<bytes>&sha256=<hex>`

创建会话，返回上传 id 与初始偏移。

```json
{"ok": true, "upload_id": "9f1c2a7b3d5e6f8a1b", "offset": 0, "size": 209715200,
 "chunk_size": 8388608, "space": "shared", "name": "big.bin", "expires_in": 1800}
```

- `size` 必填（字节）；`sha256` 可选，commit 时校验整文件。
- 只读空间返回 `403`。
- 服务端把数据写到对应空间目录下的 `.lt-upload-<id>.part`，该文件不会出现在文件列表。

### `PUT /api/upload/session/<id>?offset=<bytes>[&resumed=1]`

- Body 为分片原始字节，必须带 `Content-Length`，且 `offset` 必须等于服务端当前偏移。
- 成功返回 `{"ok":true,"upload_id":"…","offset":8388608,"size":…,"received":8388608}`。
- 偏移不匹配返回 `409` + 当前 `offset`（客户端应从该偏移继续）。
- `resumed=1` 表示本次是从断点续传，会写入审计日志的 `resumed` 字段。

### `GET /api/upload/session/<id>`

查询会话状态，用于客户端崩溃/重启后恢复：

```json
{"ok": true, "upload_id": "…", "offset": 104857600, "size": 209715200,
 "chunk_size": 8388608, "space": "shared", "name": "big.bin",
 "chunks": 13, "resumed": true, "expires_in": 1620}
```

### `POST /api/upload/session/<id>/commit`

- 校验 `offset == size`（必要时校验 sha256），把临时文件改名为最终文件名（同名自动改名）。
- 失败：`409`（未传完）、`422`（sha256 不一致，会话被清理）。
- 成功：

```json
{"ok": true, "name": "big.bin", "size": 209715200, "space": "shared",
 "sha256": "…", "chunks": 25, "resumed": true, "upload_id": "…"}
```

### `DELETE /api/upload/session/<id>`

放弃上传并删除临时文件。

- 会话超过 **30 分钟**没有任何分片会被自动清理；服务启动时也会清理过期的 `.lt-upload-*.part`。
- 审计日志 `logs/upload-audit-*.log` 增加 `resumed`、`chunked`、`session` 字段。

### 会话持久化与跨重启续传（v1.5 新增）

- 会话元数据（`id`、文件名、`size`、`space`、已确认 `offset`、`sha256`、分片数）
  在每次创建/写入后原子写入 `<数据目录>/upload-sessions.json`。
- 服务启动时自动恢复：校验临时文件存在且不小于 `offset`（多出的半截写入会被截断），
  记录日志 `upload_sessions_restored`。
- 客户端在服务重启后直接 `GET /api/upload/session/<id>` 拿到 `offset` 再继续 `PUT` 即可，
  CLI/MCP 的 `--resume` / `resume: true` 已自动做这一步。
- 超过 30 分钟没有更新的会话仍按原规则清理，并同步更新持久化文件。
- 数据目录里的 `upload-sessions.json` 可安全删除（只是会失去未完成会话的恢复能力）。

### 服务端上传并发上限（v1.5 新增）

- 服务端同时进行「写入」的上传（一次性 `POST /api/upload` 与分片 `PUT` 共用）默认上限 **4**。
- 超出时立即返回 `429`：

```json
{"error": "当前上传任务过多，请稍后重试（服务端并发上限 4）", "code": "busy"}
```

- 客户端应等待 1~3 秒后重试；`put` 批量上传时建议并发不超过 2。

### `POST /api/token/rotate?grace=<seconds>`（v1.5 新增）

轮换全局口令（需要当前有效口令）。返回新口令，旧口令在宽限期内仍然可用，
便于多设备平滑切换。

```json
{"ok": true, "token": "8kf3m2xz9q", "grace_seconds": 1800,
 "previous_valid_until": 1762303600,
 "message": "口令已轮换，旧口令在 1800 秒内仍然可用"}
```

- `grace` 默认 `1800`（30 分钟），范围 60 秒 ~ 7 天。
- 新口令与旧口令宽限期会写入 `settings.json`（`token`、`token_history`），重启后仍生效。
- 若启动时带了 `--token-file`，新口令会同时写回该文件；文件优先于 `settings.json`。
- 空间独立 `token` 仍然有效；轮换的是全局口令。
- 旧口令宽限期结束后自动失效（每次校验时按时间过滤）。

### `DELETE /api/files?name=<filename>&space=<name>`

- `space` 可选；不带时兼容旧前端，在默认空间和其他 `rw` 空间里查找（需要全局口令）。
- 只读空间返回 `403`。

### `GET /api/info`

需要全局口令。返回局域网地址、端口、证书指纹、空间目录、文件统计等，供网页端和桌面端展示。

## 错误码

| HTTP | 场景 |
|---|---|
| 400 | 缺少文件名、Content-Length / size 不合法 |
| 401/403 | 口令错误、无空间权限、只读空间写操作 |
| 404 | 文件 / 空间不存在、上传会话不存在或已过期 |
| 409 | 分片 `offset` 与服务端不一致；commit 时尚未传完 |
| 411 | 上传缺少 Content-Length |
| 416 | Range 越界（响应带 `Content-Range: bytes */size`） |
| 422 | commit 时 sha256 校验不一致（会话被清理） |
| 429 | 服务端上传并发已满（`code: "busy"`，稍后重试） |
| 500 | 服务端读写异常 |

## UDP 局域网发现（v1.4 新增）

- 端口：`8766`（UDP 广播），间隔 3 秒，关闭时完全不发送。
- 服务端向 `255.255.255.255:8766` 广播 JSON beacon，**不含口令**：

```json
{"app": "lan-transfer", "version": "1.5.0", "port": 8765, "scheme": "https",
 "spaces": 2, "default_space": "inbox", "fallback": false,
 "host": "DESKTOP-ABC", "ip": "10.0.0.5", "discovery_port": 8766}
```

- CLI `discover` 监听该端口；默认只采纳与监听者本机同一 `/24` 网段的 `ip`，
  避免跨网段干扰，`--any-subnet` 可关闭过滤。
- 可用 `GET /api/health` 的 `discovery.enabled` 判断服务端是否在广播；
  headless 用 `--no-discovery`、GUI 用「在局域网内可见」开关关闭。

## CLI 与接口对照

| CLI | 接口 |
|---|---|
| `health` | `GET /api/health` |
| `spaces` | `GET /api/spaces` |
| `ls` | `GET /api/files` |
| `put` | `POST /api/upload/session` → `PUT` 分片 → `commit`（`--resume` 时先 `GET` 会话恢复偏移；旧服务端自动回退 `POST /api/upload`） |
| `get` | `GET /download?checksum=1`（`--resume` 时带 `Range` / `If-Range`） |
| `rm` | `DELETE /api/files` |
| `discover` | 监听 UDP `8766` 广播（不走 HTTP） |
| `rotate-token` | `POST /api/token/rotate` |

CLI 退出码：`0` 成功、`1` 一般失败、`2` 参数错误、`3` 口令/权限、`4` 网络、`5` sha256 不一致、`6` 文件/空间不存在。

### MCP 工具与接口对照（v1.5 新增）

`lan_transfer_mcp.py` 直接复用上表逻辑（不拼接命令行），工具与接口一一对应：

| MCP 工具 | 接口 |
|---|---|
| `health` | `GET /api/health` |
| `spaces` | `GET /api/spaces` |
| `ls` | `GET /api/files` |
| `put` | `POST /api/upload/session` → `PUT` → `commit`（`resume` 时先 `GET` 会话） |
| `get` | `GET /download`（`resume` 时带 `Range` / `If-Range`） |
| `rm` | `DELETE /api/files` |
| `discover` | 监听 UDP `8766` 广播 |

细节（工具参数、返回结构、Claude Desktop 配置、完整示例对话）见 [docs/mcp.md](mcp.md)。

## 示例

```bash
# 健康检查（无需口令）
curl -k https://10.0.0.5:8765/api/health

# 列出空间（全局口令）
curl -k -H "Authorization: Bearer <token>" https://10.0.0.5:8765/api/spaces

# 上传到 shared 空间
curl -k -H "Authorization: Bearer <token>" -H "Content-Type: application/octet-stream" \
     -X POST --data-binary @report.pdf \
     "https://10.0.0.5:8765/api/upload?name=report.pdf&space=shared"

# 下载并校验
curl -k -H "Authorization: Bearer <token>" \
     "https://10.0.0.5:8765/download?name=report.pdf&space=shared&checksum=1" -o report.pdf
```

### 分片上传会话示例

```bash
# 1) 创建会话（200MB 文件，最后一片可能不足 8MB）
curl -k -H "Authorization: Bearer <token>" -X POST \
     "https://10.0.0.5:8765/api/upload/session?name=big.bin&space=shared&size=209715200"

# 2) 每片 8MB 依次 PUT（offset 必须与上一次返回一致）
curl -k -H "Authorization: Bearer <token>" -X PUT \
     --data-binary @part-000000000 \
     "https://10.0.0.5:8765/api/upload/session/<upload_id>?offset=0"
curl -k -H "Authorization: Bearer <token>" -X PUT \
     --data-binary @part-00008388608 \
     "https://10.0.0.5:8765/api/upload/session/<upload_id>?offset=8388608"

# 3) 查询偏移（断点续传时用）
curl -k -H "Authorization: Bearer <token>" \
     "https://10.0.0.5:8765/api/upload/session/<upload_id>"

# 4) 提交（服务端校验 offset == size，可选 sha256）
curl -k -H "Authorization: Bearer <token>" -X POST \
     "https://10.0.0.5:8765/api/upload/session/<upload_id>/commit"
```

### 局域网发现

```bash
# CLI 监听广播 3 秒，输出可直接用于 --server
lan-transfer-cli discover --timeout 3
lan-transfer-cli discover --timeout 3 --json
```

### 口令轮换

```bash
# 生成新口令；旧口令 30 分钟内仍可用；token.txt 会被写回新口令
lan-transfer-cli rotate-token --server https://10.0.0.5:8765 \
    --token-file token.txt --grace 1800

# 等价的 HTTP 调用
curl -k -H "Authorization: Bearer <旧token>" -X POST \
     "https://10.0.0.5:8765/api/token/rotate?grace=1800"
```

