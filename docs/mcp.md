# 局域网互传 MCP 服务器（v1.5.0）

`lan_transfer_mcp.py` 把局域网互传的 HTTP 能力包装成 **MCP（Model Context Protocol）** 工具，
通过 **stdio** 与 AI 客户端通信。AI 可以直接「发现服务 → 查看空间 → 上传/下载/删除」，
不需要拼接命令行，也不需要知道 HTTP 细节。

- 传输层：stdio（一行一个 JSON-RPC 2.0 消息）
- 语言 / 依赖：Python 3.9+，**零第三方依赖**
- 网络逻辑：完全复用 `lan_transfer_cli.py`（同一个 `http_call` / `upload_one` / `download_one`）
- 服务端：仍需先启动 `lan_transfer.py`（或桌面版 exe）

## 1. 先启动服务端

```bash
# 电脑端（HTTPS，推荐）
python lan_transfer.py

# 无界面 / 调试（HTTP 明文，仅本机或可信局域网）
python lan_transfer.py --headless --no-tls --token-file token.txt
```

记下启动时打印的地址（如 `https://10.0.0.5:8765`）和口令。

## 2. 启动 MCP 服务器

```bash
python lan_transfer_mcp.py \
  --server https://10.0.0.5:8765 \
  --token-file token.txt \
  --space inbox
```

| 参数 | 环境变量 | 说明 |
| --- | --- | --- |
| `--server` | `LAN_TRANSFER_SERVER` | 默认服务地址；工具参数可覆盖 |
| `--token` | `LAN_TRANSFER_TOKEN` | 默认口令（不推荐写进配置） |
| `--token-file` | `LAN_TRANSFER_TOKEN_FILE` | 默认口令文件（推荐，一行口令） |
| `--space` | `LAN_TRANSFER_SPACE` | 默认空间 |

> 每个工具调用都会重新读取口令文件，所以用 `rotate-token --token-file ...` 轮换口令后，
> MCP 无需重启即可用上新口令；旧口令在宽限期内也仍可用。

## 3. Claude Desktop 配置

配置文件位置：

- Windows：`%APPDATA%\Claude\claude_desktop_config.json`
- macOS：`~/Library/Application Support/Claude/claude_desktop_config.json`

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

用环境变量传默认值（适合多台电脑/多套配置）：

```json
{
  "mcpServers": {
    "lan-transfer": {
      "command": "python",
      "args": ["D:\\文件传输\\desktop-client\\lan_transfer_mcp.py"],
      "env": {
        "LAN_TRANSFER_SERVER": "https://10.0.0.5:8765",
        "LAN_TRANSFER_TOKEN_FILE": "D:\\文件传输\\desktop-client\\token.txt",
        "LAN_TRANSFER_SPACE": "inbox"
      }
    }
  }
}
```

macOS / Linux 把 `command` 改成 `python3`，路径改成 `/Users/you/lan-transfer-cli/...` 即可。

保存后完全退出并重启 Claude Desktop，在工具列表里应能看到 `health`、`spaces`、`ls`、
`put`、`get`、`rm`、`discover`。

## 4. 通用 MCP 客户端

任何支持 stdio 的 MCP 客户端（Cursor、Continue、自研 Agent）都可按同样方式拉起进程。
进程启动后先发 `initialize`，再发 `notifications/initialized`：

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"my-agent","version":"1.0"}}}
{"jsonrpc":"2.0","method":"notifications/initialized"}
{"jsonrpc":"2.0","id":2,"method":"tools/list"}
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"health","arguments":{}}}
```

- 服务端在 stdout 只输出 JSON-RPC；日志全部走 stderr（可安全地收集到日志文件）。
- 支持 `ping`、`tools/list`、`tools/call`，以及 `resources/list`、`prompts/list`（返回空列表）。

## 5. 工具清单

| 工具 | 作用 | 关键参数 | 需要口令 |
| --- | --- | --- | --- |
| `health` | 查看服务是否在线、版本、端口、能力 | `server` | 否 |
| `spaces` | 列出空间（名称、读写、文件数、大小） | `server`、`space`? | 是 |
| `ls` | 列出文件（名称、大小、修改时间） | `server`、`space` | 是 |
| `put` | 上传文件/目录，支持多文件、目录递归、续传 | `paths`、`space`、`recursive`、`verify_sha256`、`resume` | 是 |
| `get` | 下载文件，支持断点续传与 sha256 校验 | `name`/`names`、`dest`、`space`、`resume`、`verify_sha256` | 是 |
| `rm` | 删除文件 | `name`/`names`、`space` | 是 |
| `discover` | UDP 广播发现局域网服务 | `timeout`、`discovery_port`、`any_subnet` | 否 |

所有工具都接受 `token` 或 `token_file` 覆盖进程默认口令。

### 5.1 `health`

```json
{"name": "health", "arguments": {"server": "https://10.0.0.5:8765"}}
```

返回节选：

```json
{
  "ok": true,
  "tool": "health",
  "server": "https://10.0.0.5:8765",
  "version": "1.5.0",
  "api_version": 1,
  "listen": "10.0.0.5",
  "port": 8765,
  "scheme": "https",
  "spaces": 2,
  "default_space": "inbox",
  "capabilities": {"resume": true, "upload_session": true, "discovery": true,
                  "session_persist": true, "token_rotate": true, "upload_limit": 4},
  "elapsed_ms": 12
}
```

### 5.2 `spaces`

```json
{"name": "spaces", "arguments": {}}
```

```json
{
  "ok": true, "tool": "spaces", "count": 2, "default_space": "inbox",
  "spaces": [
    {"name": "inbox", "mode": "rw", "description": "手机或其他设备上传到这里",
     "files": 3, "size": 1024, "has_own_token": false},
    {"name": "shared", "mode": "ro", "description": "电脑端发布", "files": 1,
     "size": 209715200, "has_own_token": false}
  ]
}
```

### 5.3 `ls`

```json
{"name": "ls", "arguments": {"space": "inbox"}}
```

```json
{
  "ok": true, "tool": "ls", "space": "inbox", "count": 2,
  "total_bytes": 209715212,
  "files": [
    {"name": "a.txt", "size": 12, "mtime": 1762300000, "kind": "doc",
     "source": "receive", "space": "inbox"},
    {"name": "big.bin", "size": 209715200, "mtime": 1762300100, "kind": "bin",
     "source": "receive", "space": "inbox"}
  ]
}
```

### 5.4 `put`

单文件：

```json
{"name": "put", "arguments": {"paths": "D:\\报告\\q3.pdf", "space": "inbox"}}
```

多文件 + 目录递归：

```json
{"name": "put", "arguments": {
  "paths": ["D:\\报告\\q3.pdf", "D:\\图片"],
  "recursive": true, "space": "inbox", "verify_sha256": true, "resume": true
}}
```

返回：

```json
{
  "ok": true, "tool": "put", "space": "inbox",
  "file_count": 4, "uploaded_count": 4, "failed_count": 0,
  "total_bytes": 31457280, "elapsed_ms": 1855,
  "uploaded": [
    {"file": "D:\\报告\\q3.pdf", "name": "q3.pdf", "space": "inbox",
     "bytes": 20971520, "sha256": "949e…b309", "elapsed_ms": 1203,
     "verified": true, "resumed": true, "chunks": 23,
     "upload_id": "0ae721af59e02e0837", "mode": "session", "ok": true}
  ],
  "failed": []
}
```

- `recursive` 默认 `true`；传 `false` 时目录会返回 `INVALID_ARGUMENT`。
- `verify_sha256` 默认 `true`，服务端 sha256 与本地不一致时返回 `CHECKSUM_MISMATCH`。
- `resume: true` 会复用上次中断的分片会话（客户端状态文件 `*.ltupload.json`）。
- 服务端正在写入的上传超过并发上限时返回 `TOO_MANY_UPLOADS`（HTTP 429）。

### 5.5 `get`

```json
{"name": "get", "arguments": {
  "names": ["q3.pdf", "big.bin"], "space": "inbox",
  "dest": "D:\\下载", "resume": true, "verify_sha256": true
}}
```

```json
{
  "ok": true, "tool": "get", "space": "inbox", "dest": "D:\\下载",
  "downloaded_count": 1, "failed_count": 0, "total_bytes": 123731968,
  "elapsed_ms": 963,
  "downloaded": [
    {"name": "big.bin", "path": "D:\\下载\\big.bin", "space": "inbox",
     "bytes": 209715200, "size_header": 209715200, "sha256": "949e…b309",
     "elapsed_ms": 963, "verified": true, "resumed_from": 123731968,
     "attempt": 1, "ok": true}
  ],
  "failed": []
}
```

- `resume` 默认 `true`：`.part` 已收字节 + `Range`/`If-Range`；服务器文件变了会自动从头重下。
- 下载完成后才改名为正式文件，并校验整文件 sha256。

### 5.6 `rm`

```json
{"name": "rm", "arguments": {"names": ["old.zip"], "space": "inbox"}}
```

```json
{"ok": true, "tool": "rm", "space": "inbox", "removed_count": 1,
 "failed_count": 0, "removed": [{"name": "old.zip", "ok": true}], "failed": []}
```

只读空间返回 `UNAUTHORIZED` / HTTP 403。

### 5.7 `discover`

```json
{"name": "discover", "arguments": {"timeout": 4}}
```

```json
{
  "ok": true, "tool": "discover", "local_ip": "10.0.0.8",
  "elapsed_ms": 4020, "count": 1,
  "services": [
    {"ip": "10.0.0.5", "port": 8765, "server": "https://10.0.0.5:8765",
     "version": "1.5.0", "spaces": 2, "default_space": "inbox",
     "fallback": false, "host": "DESKTOP-ABC", "scheme": "https"}
  ],
  "suggested_server": "https://10.0.0.5:8765"
}
```

- 默认只接受与本机同网段的广播（`any_subnet: true` 可放开）。
- 监听时长默认 3 秒；beacon 每 3 秒一发，所以 4 秒基本必中。

## 6. 错误返回

工具失败时 `isError: true`，正文仍是 JSON（不会抛裸异常）：

```json
{
  "ok": false,
  "tool": "ls",
  "server": "https://10.0.0.5:8765",
  "error_code": "UNAUTHORIZED",
  "message": "口令不正确或已失效",
  "exit_code": 3,
  "http_status": 403
}
```

| `error_code` | 含义 | 对应 CLI 退出码 |
| --- | --- | --- |
| `INVALID_ARGUMENT` | 参数缺失/目录未开递归/路径不存在 | 2 |
| `UNAUTHORIZED` | 口令错误、空间只读 | 3 |
| `NETWORK_ERROR` | 连不上服务、超时、广播监听失败 | 4 |
| `CHECKSUM_MISMATCH` | sha256 与服务器不一致 | 5 |
| `NOT_FOUND` | 文件或空间不存在 | 6 |
| `TOO_MANY_UPLOADS` | 服务端上传并发已满（HTTP 429） | 1 |
| `FAILED` | 其他服务器/HTTP 错误 | 1 |
| `UNKNOWN_TOOL` | 调用了不存在的工具 | — |
| `INTERNAL_ERROR` | MCP 内部异常（程序缺陷） | — |

`put` / `get` / `rm` 支持批量：单个文件失败不会中断其余文件，`failed` 数组逐条给出原因。

## 7. 完整示例：AI 从发现到上传下载

> 场景：用户说「把电脑上的季度报告传到手机能访问的空间，再从手机下载回来验证」。

**用户**：帮我把 `D:\报告\q3.pdf` 传到局域网互传的 `inbox`，然后确认它出现在列表里。

**AI**：先找服务。

```json
→ tools/call discover {"timeout": 4}
← {"count":1,"suggested_server":"https://10.0.0.5:8765",
   "services":[{"ip":"10.0.0.5","port":8765,"version":"1.5.0","spaces":2}]}
```

**AI**：确认在线与版本。

```json
→ tools/call health {"server":"https://10.0.0.5:8765"}
← {"ok":true,"version":"1.5.0","default_space":"inbox","capabilities":{"resume":true}}
```

**AI**：看空间与当前文件。

```json
→ tools/call spaces {}
← {"ok":true,"default_space":"inbox","spaces":[
     {"name":"inbox","mode":"rw"},{"name":"shared","mode":"rw"}]}

→ tools/call ls {"space":"inbox"}
← {"ok":true,"count":0,"files":[]}
```

**AI**：上传（带 sha256 校验）。

```json
→ tools/call put {"paths":"D:\\报告\\q3.pdf","space":"inbox","verify_sha256":true}
← {"ok":true,"uploaded_count":1,"total_bytes":20971520,"elapsed_ms":1203,
   "uploaded":[{"name":"q3.pdf","verified":true,"chunks":3,"ok":true}]}
```

**AI**：确认列表。

```json
→ tools/call ls {"space":"inbox"}
← {"ok":true,"count":1,"files":[{"name":"q3.pdf","size":20971520}]}
```

**AI**：下载回来校验（`resume` 默认开启，断网可继续）。

```json
→ tools/call get {"name":"q3.pdf","space":"inbox","dest":"D:\\下载",
                  "verify_sha256":true}
← {"ok":true,"downloaded_count":1,"downloaded":[
     {"path":"D:\\下载\\q3.pdf","bytes":20971520,"verified":true,"resumed_from":0}]}
```

**AI**：清理旧文件。

```json
→ tools/call rm {"name":"q3-旧版.pdf","space":"inbox"}
← {"ok":true,"removed_count":1}
```

**AI 回复用户**：已上传 `q3.pdf`（20.0 MB，sha256 校验通过，用时 1.2s），
列表确认存在，并已下载到 `D:\下载\q3.pdf` 再次校验一致。

### 失败重试建议（给 AI 的规则）

1. `error_code == "NETWORK_ERROR"` → 稍后重试；`put`/`get` 会自动用断点。
2. `UNAUTHORIZED` → 检查 `token` / `token_file`，不要盲目重试。
3. `NOT_FOUND` → 先 `ls` 确认文件名与空间。
4. `TOO_MANY_UPLOADS` → 等几秒再试，或减少并发文件数。
5. `CHECKSUM_MISMATCH` → 重新传/下一次，`resume: true` 会从断点重建。

## 8. 安全说明

- 服务端理想情况下用 HTTPS（默认自签名证书）；CLI/MCP 按局域网场景不做证书主机名校验，
  内容完整性依靠 sha256。
- 建议把口令放在权限受限的文件里，用 `--token-file` / `LAN_TRANSFER_TOKEN_FILE`，
  不要把口令写进会提交到仓库的配置。
- 口令轮换：`python lan_transfer_cli.py rotate-token --token-file token.txt --grace 1800`
  会生成新口令并写回文件；旧口令 30 分钟内仍可用（同一空间新旧并存），到期自动失效。
- `discover` 只读广播元信息，不含口令。

## 9. 兼容性

- MCP 服务器为新增文件，不影响任何旧客户端。
- 所有 HTTP 接口保持向后兼容；不传新参数时行为与 v1.4 一致。
- MCP 的 `tools/list` / `tools/call` 结构遵循 MCP 2024-11-05 协议，
  可被 Claude Desktop、Cursor 等客户端直接识别。
