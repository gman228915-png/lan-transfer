# 局域网互传 API（v1.2）

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
  "version": "1.3.1",
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
  "capabilities": {
    "spaces": true,
    "per_space_token": true,
    "read_only_space": true,
    "sha256": true,
    "upload_audit": true,
    "http_range": true,
    "headless": true,
    "resume": false
  }
}
```

`capabilities` 中的 `resume` 目前为 `false`（断点续传未实现）。

### `GET /api/version`

公开接口，无需口令，返回 `{"app","version","api_version"}`。

## 空间

### `GET /api/spaces`

需要口令。全局口令返回全部空间；空间独立口令只返回它有权访问的空间。

```json
{
  "default": "inbox",
  "version": "1.3.1",
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
- 支持 `Range`（单段），返回 `206`；始终返回 `Accept-Ranges: bytes`。
- `checksum=1` 时额外返回 `X-File-Sha256`（会先完整读一遍文件，大文件慎用）。
- `inline=1` 时 `Content-Disposition` 为 `inline`（用于网页缩略图）。
- 响应头 `X-Space` 表示文件实际所在空间。

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

### `DELETE /api/files?name=<filename>&space=<name>`

- `space` 可选；不带时兼容旧前端，在默认空间和其他 `rw` 空间里查找（需要全局口令）。
- 只读空间返回 `403`。

### `GET /api/info`

需要全局口令。返回局域网地址、端口、证书指纹、空间目录、文件统计等，供网页端和桌面端展示。

## 错误码

| HTTP | 场景 |
|---|---|
| 400 | 缺少文件名、Content-Length 不合法 |
| 401/403 | 口令错误、无空间权限、只读空间写操作 |
| 404 | 文件 / 空间不存在 |
| 411 | 上传缺少 Content-Length |
| 416 | Range 越界 |
| 500 | 服务端读写异常 |

## CLI 与接口对照

| CLI | 接口 |
|---|---|
| `health` | `GET /api/health` |
| `spaces` | `GET /api/spaces` |
| `ls` | `GET /api/files` |
| `put` | `POST /api/upload` |
| `get` | `GET /download?checksum=1` |
| `rm` | `DELETE /api/files` |

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
