#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
局域网互传 - 命令行客户端（零第三方依赖）

示例:
  python lan_transfer_cli.py health --server https://10.0.0.5:8765
  python lan_transfer_cli.py put ./a.txt --server https://10.0.0.5:8765 --token-file token.txt
  python lan_transfer_cli.py put ./dir -r --space shared --server ... --json
  python lan_transfer_cli.py ls --space shared --server ...
  python lan_transfer_cli.py get report.pdf --out ./downloads --server ...

退出码:
  0 成功  1 一般失败  2 参数错误  3 口令/权限错误
  4 网络错误  5 sha256 校验失败  6 文件或空间不存在
"""

import argparse
import hashlib
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

APP_VERSION = "1.3.1"

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2
EXIT_AUTH = 3
EXIT_NETWORK = 4
EXIT_CHECKSUM = 5
EXIT_NOT_FOUND = 6

_TIMEOUT = 300
_SSL_CTX = None


class CliError(Exception):
    def __init__(self, message, code=EXIT_FAIL, detail=None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.detail = detail or {}


def ssl_context():
    global _SSL_CTX
    if _SSL_CTX is None:
        ctx = ssl.create_default_context()
        # 局域网内使用自签名证书，CLI 不做主机名校验（可用 SHA-256 校验保证内容）
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        _SSL_CTX = ctx
    return _SSL_CTX


def read_token(path):
    if not path:
        return ""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError as exc:
        raise CliError("无法读取口令文件 %s: %s" % (path, exc), EXIT_USAGE)


def sha256_local(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def build_url(server, path, params):
    server = server.rstrip("/")
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
    return server + path + ("?" + query if query else "")


def http_call(server, path, params=None, method="GET", token="", data=None,
              headers=None, timeout=_TIMEOUT):
    url = build_url(server, path, params or {})
    req = urllib.request.Request(url, data=data, method=method)
    if token:
        req.add_header("Authorization", "Bearer " + token)
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, context=ssl_context(), timeout=timeout) as resp:
            return resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as exc:
        body = exc.read()
        message = "HTTP %d" % exc.code
        try:
            payload = json.loads(body.decode("utf-8"))
            message = payload.get("error") or payload.get("message") or message
        except Exception:
            pass
        if exc.code in (401, 403):
            raise CliError(message, EXIT_AUTH, {"status": exc.code})
        if exc.code == 404:
            raise CliError(message, EXIT_NOT_FOUND, {"status": exc.code})
        raise CliError(message, EXIT_FAIL, {"status": exc.code})
    except urllib.error.URLError as exc:
        raise CliError("连接失败: %s（检查服务器地址、端口和同一 Wi-Fi）"
                       % getattr(exc, "reason", exc), EXIT_NETWORK)
    except (OSError, ValueError) as exc:
        raise CliError("网络错误: %s" % exc, EXIT_NETWORK)


def get_json(server, path, params, token):
    status, _, body = http_call(server, path, params, token=token)
    try:
        return json.loads(body.decode("utf-8"))
    except Exception:
        raise CliError("服务器返回不是合法 JSON")


def human_size(num):
    num = float(num or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < 1024 or unit == "TB":
            return ("%d %s" % (num, unit)) if unit == "B" else ("%.1f %s" % (num, unit))
        num /= 1024.0


def human_time(ts):
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(int(ts)))
    except Exception:
        return "-"


def out_json(payload):
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def resolve_upload_paths(paths, recursive):
    """把文件/目录展开成待上传文件列表。"""
    files = []
    for raw in paths:
        path = os.path.abspath(os.path.expanduser(raw))
        if not os.path.exists(path):
            raise CliError("路径不存在: %s" % raw, EXIT_USAGE)
        if os.path.isdir(path):
            if not recursive:
                raise CliError("目录需要加 -r/--recursive: %s" % raw, EXIT_USAGE)
            for root, _dirs, filenames in os.walk(path):
                for filename in sorted(filenames):
                    full = os.path.join(root, filename)
                    if os.path.isfile(full):
                        files.append(full)
        elif os.path.isfile(path):
            files.append(path)
        else:
            raise CliError("不是普通文件: %s" % raw, EXIT_USAGE)
    if not files:
        raise CliError("没有找到可上传的文件", EXIT_USAGE)
    return files


def upload_one(server, token, space, path, verify):
    size = os.path.getsize(path)
    checksum = sha256_local(path)
    started = time.time()
    url = build_url(server, "/api/upload",
                    {"name": os.path.basename(path), "space": space})
    with open(path, "rb") as fh:
        # 注意：先设置 data 再加请求头，否则 Request 会清掉 Content-Length
        req = urllib.request.Request(url, data=fh, method="POST")
        req.add_header("Authorization", "Bearer " + token)
        req.add_header("Content-Type", "application/octet-stream")
        req.add_header("Content-Length", str(size))
        try:
            with urllib.request.urlopen(req, context=ssl_context(), timeout=_TIMEOUT) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read()
            message = "HTTP %d" % exc.code
            try:
                message = json.loads(body.decode("utf-8")).get("error") or message
            except Exception:
                pass
            code = EXIT_AUTH if exc.code in (401, 403) else \
                EXIT_NOT_FOUND if exc.code == 404 else EXIT_FAIL
            raise CliError(message, code, {"status": exc.code})
        except urllib.error.URLError as exc:
            raise CliError("上传失败: %s" % getattr(exc, "reason", exc), EXIT_NETWORK)

    elapsed_ms = int((time.time() - started) * 1000)
    remote_sum = payload.get("sha256", "")
    if verify and remote_sum and remote_sum != checksum:
        raise CliError("上传后 sha256 不一致：本地 %s / 服务器 %s"
                       % (checksum, remote_sum), EXIT_CHECKSUM)
    return {
        "file": path,
        "name": payload.get("name", os.path.basename(path)),
        "space": payload.get("space", space),
        "bytes": payload.get("size", size),
        "sha256": remote_sum or checksum,
        "elapsed_ms": elapsed_ms,
        "verified": bool(remote_sum and remote_sum == checksum),
    }


def unique_local_path(directory, name):
    target = os.path.join(directory, name)
    if not os.path.exists(target):
        return target
    stem, ext = os.path.splitext(name)
    for i in range(1, 10000):
        candidate = os.path.join(directory, "%s (%d)%s" % (stem, i, ext))
        if not os.path.exists(candidate):
            return candidate
    return os.path.join(directory, "%s-%d%s" % (stem, int(time.time()), ext))


def download_one(server, token, space, name, out_dir, verify):
    os.makedirs(out_dir, exist_ok=True)
    params = {"name": name, "space": space, "checksum": "1" if verify else ""}
    url = build_url(server, "/download", params)
    req = urllib.request.Request(url)
    req.add_header("Authorization", "Bearer " + token)
    started = time.time()
    try:
        resp = urllib.request.urlopen(req, context=ssl_context(), timeout=_TIMEOUT)
    except urllib.error.HTTPError as exc:
        body = exc.read()
        message = "HTTP %d" % exc.code
        try:
            message = json.loads(body.decode("utf-8")).get("error") or message
        except Exception:
            pass
        code = EXIT_AUTH if exc.code in (401, 403) else \
            EXIT_NOT_FOUND if exc.code == 404 else EXIT_FAIL
        raise CliError(message, code, {"status": exc.code})
    except urllib.error.URLError as exc:
        raise CliError("下载失败: %s" % getattr(exc, "reason", exc), EXIT_NETWORK)

    with resp:
        remote_sum = resp.headers.get("X-File-SHA256", "")
        remote_space = resp.headers.get("X-Space", space)
        remote_size = int(resp.headers.get("Content-Length") or 0)
        dest = unique_local_path(out_dir, os.path.basename(name))
        tmp = dest + ".part"
        digest = hashlib.sha256()
        written = 0
        try:
            with open(tmp, "wb") as fh:
                while True:
                    block = resp.read(1024 * 1024)
                    if not block:
                        break
                    fh.write(block)
                    digest.update(block)
                    written += len(block)
            os.replace(tmp, dest)
        except OSError as exc:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            raise CliError("写入文件失败: %s" % exc, EXIT_FAIL)

    local_sum = digest.hexdigest()
    elapsed_ms = int((time.time() - started) * 1000)
    if verify:
        if remote_sum:
            if remote_sum != local_sum:
                raise CliError("下载后 sha256 不一致：服务器 %s / 本地 %s"
                               % (remote_sum, local_sum), EXIT_CHECKSUM)
        else:
            raise CliError("服务器未返回 sha256，无法完成校验（确认服务端版本 >= 1.2）",
                           EXIT_CHECKSUM)
    return {
        "name": os.path.basename(name),
        "path": dest,
        "space": remote_space,
        "bytes": written,
        "size_header": remote_size,
        "sha256": local_sum,
        "elapsed_ms": elapsed_ms,
        "verified": bool(verify and remote_sum and remote_sum == local_sum),
    }


# --------------------------------------------------------------------------- #
# 命令
# --------------------------------------------------------------------------- #
def cmd_health(args):
    data = get_json(args.server, "/api/health", {}, token="")
    if args.json:
        out_json({"ok": data.get("ok", True), "command": "health", "result": data})
        return EXIT_OK
    print("服务: %s v%s (API v%s)" % (data.get("app"), data.get("version"),
                                     data.get("api_version")))
    print("监听: %s:%s  加密: %s  仅局域网: %s"
          % (data.get("listen"), data.get("port"), data.get("scheme"),
             "是" if data.get("lan_only") else "否"))
    if data.get("port_fallback"):
        print("端口: 默认 %s 被占用，已自动改用 %s"
              % (data.get("port_requested"), data.get("port")))
    print("空间: %s 个（默认 %s）" % (data.get("spaces"), data.get("default_space")))
    caps = data.get("capabilities") or {}
    enabled = [name for name, ok in caps.items() if ok]
    print("能力: %s" % ", ".join(enabled))
    return EXIT_OK


def cmd_spaces(args):
    token = read_token(args.token_file) or args.token
    data = get_json(args.server, "/api/spaces", {}, token=token)
    if args.json:
        out_json({"ok": True, "command": "spaces", "result": data})
        return EXIT_OK
    print("默认空间: %s" % data.get("default"))
    print("%-12s %-4s %8s %10s  %s" % ("空间", "模式", "文件数", "大小", "目录"))
    for space in data.get("spaces", []):
        print("%-12s %-4s %8d %10s  %s" % (
            space.get("name"), space.get("mode"), space.get("files", 0),
            human_size(space.get("size", 0)), space.get("path")))
        if space.get("description"):
            print("             %s" % space["description"])
    return EXIT_OK


def cmd_ls(args):
    token = read_token(args.token_file) or args.token
    data = get_json(args.server, "/api/files", {"space": args.space}, token=token)
    files = data.get("files", [])
    if args.json:
        out_json({"ok": True, "command": "ls", "space": args.space or "(all)", "files": files})
        return EXIT_OK
    if not files:
        print("没有文件")
        return EXIT_OK
    print("%-42s %10s %-16s %s" % ("文件名", "大小", "修改时间", "空间"))
    for item in files:
        print("%-42s %10s %-16s %s" % (
            item.get("name"), human_size(item.get("size", 0)),
            human_time(item.get("mtime", 0)), item.get("space") or "-"))
    return EXIT_OK


def cmd_put(args):
    token = read_token(args.token_file) or args.token
    paths = resolve_upload_paths(args.paths, args.recursive)
    results = []
    first_error = None
    for path in paths:
        try:
            result = upload_one(args.server, token, args.space, path, args.verify)
            result["ok"] = True
            results.append(result)
            if not args.json:
                print("上传完成: %s -> %s (%s, %d ms)"
                      % (os.path.basename(path), result["name"],
                         human_size(result["bytes"]), result["elapsed_ms"]))
        except CliError as exc:
            results.append({
                "file": path, "ok": False, "error": exc.message, "code": exc.code,
            })
            if first_error is None:
                first_error = exc
            if not args.json:
                sys.stderr.write("上传失败: %s: %s\n" % (path, exc.message))

    payload = {
        "ok": first_error is None,
        "command": "put",
        "server": args.server,
        "space": args.space,
        "results": results,
    }
    if first_error is not None:
        payload["error"] = first_error.message
        payload["code"] = first_error.code
    if args.json:
        out_json(payload)
    return first_error.code if first_error else EXIT_OK


def cmd_get(args):
    token = read_token(args.token_file) or args.token
    out_dir = os.path.abspath(os.path.expanduser(args.out))
    results = []
    first_error = None
    if args.resume:
        if not args.json:
            sys.stderr.write("提示: --resume 目前仅为接口占位，本轮将完整下载。\n")
    for name in args.names:
        try:
            result = download_one(args.server, token, args.space, name, out_dir, args.verify)
            result["ok"] = True
            results.append(result)
            if not args.json:
                print("下载完成: %s -> %s (%s, %d ms)"
                      % (name, result["path"], human_size(result["bytes"]),
                         result["elapsed_ms"]))
        except CliError as exc:
            results.append({"name": name, "ok": False, "error": exc.message, "code": exc.code})
            if first_error is None:
                first_error = exc
            if not args.json:
                sys.stderr.write("下载失败: %s: %s\n" % (name, exc.message))

    payload = {
        "ok": first_error is None,
        "command": "get",
        "server": args.server,
        "space": args.space,
        "output_dir": out_dir,
        "results": results,
    }
    if first_error is not None:
        payload["error"] = first_error.message
        payload["code"] = first_error.code
    if args.json:
        out_json(payload)
    return first_error.code if first_error else EXIT_OK


def cmd_rm(args):
    token = read_token(args.token_file) or args.token
    results = []
    first_error = None
    for name in args.names:
        url = build_url(args.server, "/api/files", {"name": name, "space": args.space})
        req = urllib.request.Request(url, method="DELETE")
        req.add_header("Authorization", "Bearer " + token)
        try:
            with urllib.request.urlopen(req, context=ssl_context(), timeout=_TIMEOUT) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            results.append({"name": name, "ok": True, "space": payload.get("space", args.space)})
            if not args.json:
                print("已删除: %s" % name)
        except urllib.error.HTTPError as exc:
            body = exc.read()
            message = "HTTP %d" % exc.code
            try:
                message = json.loads(body.decode("utf-8")).get("error") or message
            except Exception:
                pass
            code = EXIT_AUTH if exc.code in (401, 403) else \
                EXIT_NOT_FOUND if exc.code == 404 else EXIT_FAIL
            err = CliError(message, code, {"status": exc.code})
            results.append({"name": name, "ok": False, "error": message, "code": code})
            if first_error is None:
                first_error = err
            if not args.json:
                sys.stderr.write("删除失败: %s: %s\n" % (name, message))
        except urllib.error.URLError as exc:
            err = CliError("删除失败: %s" % getattr(exc, "reason", exc), EXIT_NETWORK)
            results.append({"name": name, "ok": False, "error": err.message})
            if first_error is None:
                first_error = err

    payload = {
        "ok": first_error is None,
        "command": "rm",
        "server": args.server,
        "space": args.space,
        "results": results,
    }
    if first_error is not None:
        payload["error"] = first_error.message
        payload["code"] = first_error.code
    if args.json:
        out_json(payload)
    return first_error.code if first_error else EXIT_OK


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--server", default=os.environ.get(
        "LAN_TRANSFER_SERVER", "https://127.0.0.1:8765"),
        help="服务地址，如 https://10.0.0.5:8765（也可用环境变量 LAN_TRANSFER_SERVER）")
    common.add_argument("--space", default="", help="空间名（不填为默认/全部，取决于命令）")
    common.add_argument("--token-file", default=os.environ.get("LAN_TRANSFER_TOKEN_FILE", ""),
                        help="口令文件路径")
    common.add_argument("--token", default=os.environ.get("LAN_TRANSFER_TOKEN", ""),
                        help="直接传口令（不推荐，优先使用 --token-file）")
    common.add_argument("--json", action="store_true", help="输出 JSON（供程序读取）")
    common.add_argument("--verify-sha256", dest="verify", action="store_true", default=True,
                        help="传输后校验 sha256（默认开启）")
    common.add_argument("--no-verify-sha256", dest="verify", action="store_false",
                        help="关闭 sha256 校验")
    common.add_argument("--resume", action="store_true", help="断点续传（当前仅占位）")

    parser = argparse.ArgumentParser(prog="lan_transfer_cli.py",
                                     description="局域网互传命令行客户端 v%s" % APP_VERSION)
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("health", parents=[common], help="查看服务健康状态")
    p.set_defaults(func=cmd_health)

    p = sub.add_parser("spaces", parents=[common], help="列出空间")
    p.set_defaults(func=cmd_spaces)

    p = sub.add_parser("ls", parents=[common], help="列出文件")
    p.set_defaults(func=cmd_ls)

    p = sub.add_parser("put", parents=[common], help="上传文件/目录")
    p.add_argument("paths", nargs="+", help="本地文件或目录")
    p.add_argument("-r", "--recursive", action="store_true", help="递归上传目录")
    p.set_defaults(func=cmd_put)

    p = sub.add_parser("get", parents=[common], help="下载文件")
    p.add_argument("names", nargs="+", help="服务器上的文件名")
    p.add_argument("-o", "--out", default=".", help="保存目录（默认当前目录）")
    p.set_defaults(func=cmd_get)

    p = sub.add_parser("rm", parents=[common], help="删除文件")
    p.add_argument("names", nargs="+", help="要删除的文件名")
    p.set_defaults(func=cmd_rm)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return EXIT_USAGE

    try:
        return args.func(args)
    except CliError as exc:
        if getattr(args, "json", False):
            out_json({"ok": False, "command": args.command, "error": exc.message,
                      "code": exc.code, **exc.detail})
        else:
            sys.stderr.write("[错误] %s\n" % exc.message)
        return exc.code
    except KeyboardInterrupt:
        sys.stderr.write("\n已取消\n")
        return EXIT_FAIL
    except Exception as exc:
        if getattr(args, "json", False):
            out_json({"ok": False, "command": args.command, "error": "未预期错误: %s" % exc,
                      "code": EXIT_FAIL})
        else:
            sys.stderr.write("[错误] 未预期错误: %s\n" % exc)
        return EXIT_FAIL


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    sys.exit(main())
