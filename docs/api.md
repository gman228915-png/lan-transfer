# 局域网互传 API（v1.4）

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
  "version": "1.4.0",
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
    "discovery": true
  }
}
```

- `resume` / `http_range` / `if_range`：下载支持 `Range`、`If-Range` 与 `206`。
- `upload_session`：分片上传会话；`upload_chunk_size` 为建议分片大小（客户端可自定）。
- `discovery`：UDP 广播发现是否开启；beacon 只含版本、端口、空间数量等元信息，**不含口令**。

### `GET /api/version`

公开接口，无需口令，返回 `{"app","version","api_version"}`。

## 空间

### `GET /api/spaces`

需要口令。全局口令返回全部空间；空间独立口令只返回它有权访问的空间。

```json
{
  "default": "inbox",
  "version": "1.4.0",
  "spaces": [
    {
      "name": "inbox",
      "mode": "rw",
      "description": "手机或其他设备上传到这里",
      "path": "D:\\文件传输\\received-files",
      "files": 3,
      "size": 1024,
      "has_own_token": false
    }
  ]
}
```

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
| 500 | 服务端读写异常 |

## UDP 局域网发现（v1.4 新增）

- 端口：`8766`（UDP 广播），间隔 3 秒，关闭时完全不发送。
- 服务端向 `255.255.255.255:8766` 广播 JSON beacon，**不含口令**：

```json
{"app": "lan-transfer", "version": "1.4.0", "port": 8765, "scheme": "https",
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

CLI 退出码：`0` 成功、`1` 一般失败、`2` 参数错误、`3` 口令/权限、`4` 网络、`5` sha256 不一致、`6` 文件/空间不存在。

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

