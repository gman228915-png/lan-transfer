#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
局域网互传 - MCP 服务器（stdio 传输）

把 lan_transfer.py 的 HTTP 能力直接暴露成 MCP 工具，供 Claude Desktop / Cursor /
任意 MCP 客户端（含 AI Agent）调用。所有网络逻辑复用 lan_transfer_cli.py，
不拼接命令行、不依赖 subprocess。

工具：
  health   查看服务状态（无需口令）
  spaces   列出空间
  ls       列出文件
  put      上传文件/目录（支持多文件、目录递归、断点续传）
  get      下载文件（支持断点续传、sha256 校验）
  rm       删除文件
  discover 局域网 UDP 广播发现服务

用法：
  python lan_transfer_mcp.py --server https://10.0.0.5:8765 --token-file token.txt

环境变量：
  LAN_TRANSFER_SERVER       默认服务地址
  LAN_TRANSFER_TOKEN        默认口令（不推荐，优先口令文件）
  LAN_TRANSFER_TOKEN_FILE   默认口令文件
  LAN_TRANSFER_SPACE        默认空间

注意：stdout 只能输出 JSON-RPC；日志一律写 stderr。
"""

import argparse
import json
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lan_transfer_cli as cli  # noqa: E402

MCP_VERSION = "1.5.0"
SERVER_NAME = "lan-transfer-mcp"
PROTOCOL_VERSION = "2024-11-05"

DEFAULT_SERVER = ""
DEFAULT_SPACE = ""
DEFAULT_TOKEN = ""
DEFAULT_TOKEN_FILE = ""


class _SilentProgress(object):
    """MCP 里不要往 stderr 刷进度条，避免污染客户端日志。"""

    def __init__(self, *args, **kwargs):
        pass

    def update(self, *args, **kwargs):
        pass

    def finish(self, *args, **kwargs):
        pass


cli.Progress = _SilentProgress


def log(message):
    sys.stderr.write("[lan-transfer-mcp] %s\n" % message)
    sys.stderr.flush()


# --------------------------------------------------------------------------- #
# 参数解析：工具参数 > 进程参数 > 环境变量
# --------------------------------------------------------------------------- #
def resolve_server(params):
    return (params.get("server") or DEFAULT_SERVER or "").strip()


def resolve_space(params):
    return (params.get("space") or DEFAULT_SPACE or "").strip()


def resolve_token(params):
    token = (params.get("token") or "").strip()
    if token:
        return token
    token_file = (params.get("token_file") or DEFAULT_TOKEN_FILE or "").strip()
    if token_file:
        return cli.read_token(token_file)
    return (DEFAULT_TOKEN or "").strip()


CODE_NAMES = {
    cli.EXIT_USAGE: "INVALID_ARGUMENT",
    cli.EXIT_AUTH: "UNAUTHORIZED",
    cli.EXIT_NETWORK: "NETWORK_ERROR",
    cli.EXIT_CHECKSUM: "CHECKSUM_MISMATCH",
    cli.EXIT_NOT_FOUND: "NOT_FOUND",
    cli.EXIT_FAIL: "FAILED",
}


def error_payload(tool, server, exc):
    code = getattr(exc, "code", cli.EXIT_FAIL)
    name = CODE_NAMES.get(code, "UNKNOWN")
    status = ""
    detail = getattr(exc, "detail", None) or {}
    if isinstance(detail, dict) and detail.get("status"):
        status = int(detail["status"])
        if status == 429:
            name = "TOO_MANY_UPLOADS"
        elif status == 401 or status == 403:
            name = "UNAUTHORIZED"
        elif status == 404:
            name = "NOT_FOUND"
    payload = {
        "ok": False,
        "tool": tool,
        "server": server,
        "error_code": name,
        "message": getattr(exc, "message", str(exc)),
        "exit_code": code,
    }
    if status:
        payload["http_status"] = status
    return payload


def normalize_list(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


# --------------------------------------------------------------------------- #
# 工具实现
# --------------------------------------------------------------------------- #
def tool_health(params):
    server = resolve_server(params)
    data = cli.get_json(server, "/api/health", {}, token="")
    return {
        "ok": bool(data.get("ok", True)),
        "tool": "health",
        "server": server,
        "version": data.get("version", ""),
        "api_version": data.get("api_version", 1),
        "listen": data.get("listen", ""),
        "port": data.get("port", 0),
        "scheme": data.get("scheme", ""),
        "spaces": data.get("spaces", 0),
        "default_space": data.get("default_space", ""),
        "capabilities": data.get("capabilities", {}),
        "health": data,
    }


def tool_spaces(params):
    server = resolve_server(params)
    data = cli.get_json(server, "/api/spaces", {}, token=resolve_token(params))
    return {
        "ok": True,
        "tool": "spaces",
        "server": server,
        "default_space": data.get("default", ""),
        "count": len(data.get("spaces", [])),
        "spaces": data.get("spaces", []),
    }


def tool_ls(params):
    server = resolve_server(params)
    space = resolve_space(params)
    data = cli.get_json(server, "/api/files", {"space": space},
                        token=resolve_token(params))
    files = data.get("files", [])
    total = sum(int(item.get("size") or 0) for item in files)
    return {
        "ok": True,
        "tool": "ls",
        "server": server,
        "space": space or "(all)",
        "count": len(files),
        "total_bytes": total,
        "files": files,
    }


def tool_put(params):
    server = resolve_server(params)
    space = resolve_space(params)
    token = resolve_token(params)
    verify = params.get("verify_sha256")
    verify = True if verify is None else bool(verify)
    resume = bool(params.get("resume", False))
    recursive = params.get("recursive")
    recursive = True if recursive is None else bool(recursive)
    paths = normalize_list(params.get("paths") or params.get("path"))
    if not paths:
        raise cli.CliError("缺少必需参数 path（本地文件或目录）", cli.EXIT_USAGE)

    files = cli.resolve_upload_paths(paths, recursive)
    started = time.time()
    uploaded, failed = [], []
    total_bytes = 0
    for path in files:
        try:
            result = cli.upload_one(server, token, space, path, verify, resume=resume)
            result["ok"] = True
            uploaded.append(result)
            total_bytes += int(result.get("bytes") or 0)
        except cli.CliError as exc:
            failed.append({
                "file": path,
                "ok": False,
                "error_code": error_payload("put", server, exc)["error_code"],
                "message": exc.message,
                "resume_hint": bool(os.path.exists(cli.upload_state_path(path))),
            })

    payload = {
        "ok": not failed,
        "tool": "put",
        "server": server,
        "space": space,
        "file_count": len(files),
        "uploaded_count": len(uploaded),
        "failed_count": len(failed),
        "total_bytes": total_bytes,
        "elapsed_ms": int((time.time() - started) * 1000),
        "uploaded": uploaded,
        "failed": failed,
    }
    if failed:
        payload["error_code"] = failed[0]["error_code"]
        payload["message"] = failed[0]["message"]
    return payload


def tool_get(params):
    server = resolve_server(params)
    space = resolve_space(params)
    token = resolve_token(params)
    verify = params.get("verify_sha256")
    verify = True if verify is None else bool(verify)
    resume = params.get("resume")
    resume = True if resume is None else bool(resume)
    dest = params.get("dest") or params.get("out") or "."
    dest = os.path.abspath(os.path.expanduser(str(dest)))
    names = normalize_list(params.get("names") or params.get("name"))
    if not names:
        raise cli.CliError("缺少必需参数 name（服务器上的文件名）", cli.EXIT_USAGE)

    started = time.time()
    downloaded, failed = [], []
    total_bytes = 0
    for name in names:
        try:
            result = cli.download_one(server, token, space, name, dest, verify,
                                      resume=resume)
            result["ok"] = True
            downloaded.append(result)
            total_bytes += int(result.get("bytes") or 0)
        except cli.CliError as exc:
            failed.append({
                "name": name,
                "ok": False,
                "error_code": error_payload("get", server, exc)["error_code"],
                "message": exc.message,
            })

    payload = {
        "ok": not failed,
        "tool": "get",
        "server": server,
        "space": space,
        "dest": dest,
        "downloaded_count": len(downloaded),
        "failed_count": len(failed),
        "total_bytes": total_bytes,
        "elapsed_ms": int((time.time() - started) * 1000),
        "downloaded": downloaded,
        "failed": failed,
    }
    if failed:
        payload["error_code"] = failed[0]["error_code"]
        payload["message"] = failed[0]["message"]
    return payload


def tool_rm(params):
    server = resolve_server(params)
    space = resolve_space(params)
    token = resolve_token(params)
    names = normalize_list(params.get("names") or params.get("name"))
    if not names:
        raise cli.CliError("缺少必需参数 name（要删除的文件名）", cli.EXIT_USAGE)

    removed, failed = [], []
    for name in names:
        try:
            cli.http_call(server, "/api/files", {"name": name, "space": space},
                          method="DELETE", token=token)
            removed.append({"name": name, "ok": True})
        except cli.CliError as exc:
            failed.append({
                "name": name,
                "ok": False,
                "error_code": error_payload("rm", server, exc)["error_code"],
                "message": exc.message,
            })

    payload = {
        "ok": not failed,
        "tool": "rm",
        "server": server,
        "space": space,
        "removed_count": len(removed),
        "failed_count": len(failed),
        "removed": removed,
        "failed": failed,
    }
    if failed:
        payload["error_code"] = failed[0]["error_code"]
        payload["message"] = failed[0]["message"]
    return payload


def tool_discover(params):
    timeout = float(params.get("timeout") or 3.0)
    port = int(params.get("discovery_port") or cli.DISCOVERY_PORT)
    any_subnet = bool(params.get("any_subnet", False))
    info = cli.collect_services(timeout, port, any_subnet)
    services = info["services"]
    return {
        "ok": True,
        "tool": "discover",
        "local_ip": info["local_ip"],
        "elapsed_ms": info["elapsed_ms"],
        "count": len(services),
        "services": services,
        "suggested_server": services[0]["server"] if services else "",
    }


TOOLS = {
    "health": tool_health,
    "spaces": tool_spaces,
    "ls": tool_ls,
    "put": tool_put,
    "get": tool_get,
    "rm": tool_rm,
    "discover": tool_discover,
}

TOOL_DEFINITIONS = [
    {
        "name": "health",
        "description": "查看局域网互传服务是否在线、版本、端口与支持的能力。不需要口令。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "server": {"type": "string",
                           "description": "服务地址，如 https://10.0.0.5:8765；不填用默认值"}
            },
        },
    },
    {
        "name": "spaces",
        "description": "列出服务端配置的空间（名称、读写模式、文件数、大小），用于决定上传/下载用哪个空间。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "server": {"type": "string", "description": "服务地址"},
                "token": {"type": "string", "description": "口令（也可用 token_file 或进程默认口令）"},
                "token_file": {"type": "string", "description": "口令文件路径（文件内为一行口令）"},
            },
        },
    },
    {
        "name": "ls",
        "description": "列出某个空间里的文件（名称、大小、修改时间）。不传 space 时列出全部空间。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "server": {"type": "string", "description": "服务地址"},
                "space": {"type": "string", "description": "空间名，如 inbox / shared"},
                "token": {"type": "string", "description": "口令"},
                "token_file": {"type": "string", "description": "口令文件路径"},
            },
        },
    },
    {
        "name": "put",
        "description": "上传本地文件或目录到指定空间。支持一次传多个文件、目录递归、分片断点续传，默认校验 sha256。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "server": {"type": "string", "description": "服务地址"},
                "space": {"type": "string", "description": "目标空间名，不填为默认空间"},
                "paths": {
                    "type": ["string", "array"],
                    "description": "本地路径，可传字符串或字符串数组（目录需 recursive=true，默认 true）",
                    "items": {"type": "string"},
                },
                "path": {"type": ["string", "array"],
                         "description": "同 paths 的别名"},
                "recursive": {"type": "boolean",
                              "description": "目录是否递归展开，默认 true"},
                "verify_sha256": {"type": "boolean",
                                  "description": "上传后校验 sha256，默认 true"},
                "resume": {"type": "boolean",
                           "description": "沿用上次未完成的分片会话，默认 false"},
                "token": {"type": "string", "description": "口令"},
                "token_file": {"type": "string", "description": "口令文件路径"},
            },
            "required": [],
        },
    },
    {
        "name": "get",
        "description": "从服务端下载文件到本地目录。支持断点续传（.part + Range）与 sha256 校验。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "server": {"type": "string", "description": "服务地址"},
                "space": {"type": "string", "description": "空间名"},
                "name": {"type": ["string", "array"],
                         "description": "服务器上的文件名，可传字符串或字符串数组"},
                "names": {"type": ["string", "array"],
                          "description": "同 name 的别名"},
                "dest": {"type": "string",
                         "description": "本地保存目录，默认当前目录"},
                "resume": {"type": "boolean",
                           "description": "断点续传，默认 true"},
                "verify_sha256": {"type": "boolean",
                                  "description": "下载后校验 sha256，默认 true"},
                "token": {"type": "string", "description": "口令"},
                "token_file": {"type": "string", "description": "口令文件路径"},
            },
            "required": [],
        },
    },
    {
        "name": "rm",
        "description": "删除指定空间里的文件（需要读写空间与口令）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "server": {"type": "string", "description": "服务地址"},
                "space": {"type": "string", "description": "空间名"},
                "name": {"type": ["string", "array"],
                         "description": "要删除的文件名，可传多个"},
                "names": {"type": ["string", "array"],
                          "description": "同 name 的别名"},
                "token": {"type": "string", "description": "口令"},
                "token_file": {"type": "string", "description": "口令文件路径"},
            },
            "required": [],
        },
    },
    {
        "name": "discover",
        "description": "监听局域网 UDP 广播，发现可用的 lan-transfer 服务（IP、端口、版本），返回可直接使用的 server 地址。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "timeout": {"type": "number",
                            "description": "监听时长（秒），默认 3"},
                "discovery_port": {"type": "integer",
                                   "description": "发现端口，默认 8766"},
                "any_subnet": {"type": "boolean",
                               "description": "是否接受非同网段广播，默认 false"},
            },
        },
    },
]


# --------------------------------------------------------------------------- #
# JSON-RPC / MCP 协议
# --------------------------------------------------------------------------- #
def send(message):
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def send_result(msg_id, result):
    send({"jsonrpc": "2.0", "id": msg_id, "result": result})


def send_error(msg_id, code, message):
    send({"jsonrpc": "2.0", "id": msg_id,
          "error": {"code": code, "message": message}})


def make_content(payload, is_error=False):
    result = {
        "content": [{"type": "text",
                     "text": json.dumps(payload, ensure_ascii=False, indent=2)}],
        "isError": bool(is_error),
    }
    if not is_error:
        result["structuredContent"] = payload
    return result


def handle_initialize(msg_id, params):
    requested = str((params or {}).get("protocolVersion") or "").strip()
    result = {
        "protocolVersion": requested or PROTOCOL_VERSION,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": SERVER_NAME, "version": MCP_VERSION},
        "instructions": (
            "局域网互传 MCP。先用 discover 找到同一 Wi-Fi 下的服务，再用 health 确认在线；"
            "spaces 查看可用空间，ls 浏览文件；put 上传（支持目录递归），get 下载（支持断点续传），"
            "rm 删除。除 health/discover 外都需要口令，可用 token 或 token_file 传入。"
        ),
    }
    send_result(msg_id, result)


def handle_tools_call(msg_id, params):
    params = params or {}
    name = str(params.get("name") or "").strip()
    arguments = params.get("arguments") or {}
    if not isinstance(arguments, dict):
        send_result(msg_id, make_content(
            {"ok": False, "tool": name, "error_code": "INVALID_ARGUMENT",
             "message": "arguments 必须是对象"}, True))
        return
    func = TOOLS.get(name)
    if func is None:
        send_result(msg_id, make_content(
            {"ok": False, "tool": name, "error_code": "UNKNOWN_TOOL",
             "message": "未知工具: %s（可用: %s）"
                        % (name, ", ".join(sorted(TOOLS)))}, True))
        return

    started = time.time()
    try:
        payload = func(arguments)
        payload.setdefault("elapsed_ms", int((time.time() - started) * 1000))
        send_result(msg_id, make_content(payload, bool(payload.get("ok") is False)))
    except cli.CliError as exc:
        send_result(msg_id, make_content(
            error_payload(name, resolve_server(arguments), exc), True))
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        log("工具 %s 异常: %s" % (name, traceback.format_exc().splitlines()[-1]))
        payload = {
            "ok": False,
            "tool": name,
            "error_code": "INTERNAL_ERROR",
            "message": "工具执行失败：%s（这是程序缺陷，请检查参数或联系维护者）" % exc,
        }
        send_result(msg_id, make_content(payload, True))


def handle_message(message):
    if not isinstance(message, dict):
        send_error(None, -32600, "无效请求：必须是 JSON 对象")
        return
    msg_id = message.get("id")
    method = message.get("method")
    params = message.get("params") or {}

    if message.get("jsonrpc") != "2.0" or not method:
        if msg_id is not None or not method:
            send_error(msg_id, -32600, "无效请求：缺少 method 或 jsonrpc=2.0")
        return

    if method == "initialize":
        handle_initialize(msg_id, params)
    elif method in ("notifications/initialized", "initialized",
                    "notifications/cancelled", "notifications/roots/list_changed"):
        return
    elif method == "ping":
        send_result(msg_id, {})
    elif method == "tools/list":
        send_result(msg_id, {"tools": TOOL_DEFINITIONS})
    elif method == "tools/call":
        handle_tools_call(msg_id, params)
    elif method in ("resources/list", "resources/templates/list"):
        send_result(msg_id, {"resources": []})
    elif method == "prompts/list":
        send_result(msg_id, {"prompts": []})
    elif method == "logging/setLevel":
        send_result(msg_id, {})
    elif method == "shutdown":
        send_result(msg_id, {})
    else:
        if msg_id is None:
            return
        send_error(msg_id, -32601, "不支持的方法: %s" % method)


def main(argv=None):
    global DEFAULT_SERVER, DEFAULT_SPACE, DEFAULT_TOKEN, DEFAULT_TOKEN_FILE
    parser = argparse.ArgumentParser(
        prog="lan_transfer_mcp.py",
        description="局域网互传 MCP 服务器 v%s（stdio）" % MCP_VERSION)
    parser.add_argument("--server", default=os.environ.get("LAN_TRANSFER_SERVER", ""),
                        help="默认服务地址，如 https://10.0.0.5:8765")
    parser.add_argument("--space", default=os.environ.get("LAN_TRANSFER_SPACE", ""),
                        help="默认空间名")
    parser.add_argument("--token", default=os.environ.get("LAN_TRANSFER_TOKEN", ""),
                        help="默认口令（不推荐，优先 --token-file）")
    parser.add_argument("--token-file",
                        default=os.environ.get("LAN_TRANSFER_TOKEN_FILE", ""),
                        help="默认口令文件")
    args = parser.parse_args(argv)

    DEFAULT_SERVER = args.server.strip()
    DEFAULT_SPACE = args.space.strip()
    DEFAULT_TOKEN = args.token.strip()
    DEFAULT_TOKEN_FILE = args.token_file.strip()

    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stdin.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    log("MCP 服务器已启动 v%s，等待 JSON-RPC（stdio）。server=%s space=%s token_file=%s"
        % (MCP_VERSION, DEFAULT_SERVER or "(工具参数)",
           DEFAULT_SPACE or "(默认)", DEFAULT_TOKEN_FILE or "(未配置)"))

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            send_error(None, -32700, "解析失败：不是合法 JSON")
            continue
        try:
            handle_message(message)
        except KeyboardInterrupt:
            log("收到中断，退出")
            return 0
        except Exception as exc:
            log("处理消息失败: %s" % exc)
            msg_id = message.get("id") if isinstance(message, dict) else None
            send_error(msg_id, -32603, "内部错误: %s" % exc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
