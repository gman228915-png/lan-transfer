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
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

APP_VERSION = "1.6.0"
DISCOVERY_PORT = 8766
CHUNK_SIZE = 8 * 1024 * 1024

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


class Progress:
    """简单的传输进度（速率 + 剩余时间），写到 stderr，不干扰 --json。"""

    def __init__(self, label, total, start=0, enabled=True):
        self.label = label
        self.total = max(0, int(total or 0))
        self.done = start
        self.start = start
        self.started = time.time()
        self.last_report = 0.0
        self.enabled = enabled

    def update(self, done, force=False):
        self.done = done
        if not self.enabled:
            return
        now = time.time()
        if not force and now - self.last_report < 1.0:
            return
        self.last_report = now
        elapsed = max(0.001, now - self.started)
        rate = max(0.0, (done - self.start) / elapsed)
        pct = (done * 100.0 / self.total) if self.total else 100.0
        eta = ((self.total - done) / rate) if (self.total and rate > 0 and done < self.total) else 0
        sys.stderr.write("\r%s %s/%s  %.1f%%  %s/s  剩余 %s    "
                         % (self.label, human_size(done), human_size(self.total), pct,
                            human_size(rate), ("%ds" % eta) if eta else "0s"))
        sys.stderr.flush()

    def finish(self, done=None):
        if done is not None:
            self.done = done
        if not self.enabled:
            return
        self.update(self.done, force=True)
        sys.stderr.write("\n")
        sys.stderr.flush()


def upload_state_path(path):
    return path + ".ltupload.json"


def load_upload_state(path, server, space, size, checksum):
    try:
        with open(upload_state_path(path), "r", encoding="utf-8") as fh:
            state = json.load(fh)
    except (OSError, ValueError):
        return None
    if (state.get("server") != server or state.get("space") != space
            or state.get("size") != size or state.get("sha256") != checksum
            or not state.get("upload_id")):
        return None
    return state


def save_upload_state(path, **state):
    try:
        with open(upload_state_path(path), "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass


def remove_upload_state(path):
    try:
        os.remove(upload_state_path(path))
    except OSError:
        pass


def upload_legacy(server, token, space, path, verify):
    """旧版一次性上传（服务端不支持分片会话时回退）。"""
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
        "resumed": False,
        "chunks": 0,
        "upload_id": "",
        "mode": "legacy",
    }


def upload_one(server, token, space, path, verify, resume=False):
    """分片会话上传；resume=True 时沿用上次未完成的会话。"""
    size = os.path.getsize(path)
    checksum = sha256_local(path)
    name = os.path.basename(path)
    started = time.time()

    state = load_upload_state(path, server, space, size, checksum) if resume else None
    upload_id, offset, resumed = "", 0, False
    if state:
        try:
            status, _, body = http_call(
                server, "/api/upload/session/%s" % state["upload_id"],
                method="GET", token=token)
            info = json.loads(body.decode("utf-8"))
            if info.get("size") and int(info["size"]) != size:
                state = None
            else:
                upload_id = state["upload_id"]
                offset = int(info.get("offset", 0))
                resumed = offset > 0
                if not sys.stderr.isatty():
                    sys.stderr.write("从服务器断点续传：已有 %s / %s\n"
                                     % (human_size(offset), human_size(size)))
        except CliError as exc:
            if exc.detail.get("status") == 404:
                state = None
            else:
                raise
        except (ValueError, KeyError):
            state = None

    if not upload_id:
        try:
            status, _, body = http_call(
                server, "/api/upload/session",
                {"name": name, "space": space, "size": str(size),
                 "sha256": checksum},
                method="POST", token=token, data=b"")
            info = json.loads(body.decode("utf-8"))
            upload_id = info["upload_id"]
            offset = int(info.get("offset", 0))
        except CliError as exc:
            if exc.detail.get("status") == 404:
                # 服务端是旧版本（<1.4），回退到一次性上传
                return upload_legacy(server, token, space, path, verify)
            raise
        except (ValueError, KeyError):
            raise CliError("服务端返回的上传会话格式不正确", EXIT_FAIL)

    save_upload_state(path, server=server, space=space, name=name, size=size,
                      sha256=checksum, upload_id=upload_id, offset=offset)

    progress = Progress("上传中", size, offset)
    progress.update(offset, force=True)
    chunks = 0
    try:
        while offset < size:
            with open(path, "rb") as fh:
                fh.seek(offset)
                block = fh.read(min(CHUNK_SIZE, size - offset))
            length = len(block)
            chunk_query = {"offset": str(offset)}
            if resumed:
                chunk_query["resumed"] = "1"
            status, _, body = http_call(
                server, "/api/upload/session/%s" % upload_id,
                chunk_query, method="PUT", token=token, data=block,
                headers={"Content-Type": "application/octet-stream",
                         "Content-Length": str(length)})
            info = json.loads(body.decode("utf-8"))
            new_offset = int(info.get("offset", offset + length))
            if new_offset <= offset:
                raise CliError("服务端未推进上传偏移（%d）" % new_offset, EXIT_FAIL)
            offset = new_offset
            chunks += 1
            save_upload_state(path, server=server, space=space, name=name, size=size,
                              sha256=checksum, upload_id=upload_id, offset=offset)
            progress.update(offset)
        progress.finish(offset)

        status, _, body = http_call(
            server, "/api/upload/session/%s/commit" % upload_id,
            method="POST", token=token, data=b"",
            headers={"Content-Length": "0"})
        payload = json.loads(body.decode("utf-8"))
        remote_sum = payload.get("sha256", "")
        if verify and remote_sum and remote_sum != checksum:
            raise CliError("上传后 sha256 不一致：本地 %s / 服务器 %s"
                           % (checksum, remote_sum), EXIT_CHECKSUM)
        remove_upload_state(path)
    except KeyboardInterrupt:
        sys.stderr.write("\n上传已中断，断点已保留；加 --resume 可继续。\n")
        raise
    except CliError:
        raise
    except (OSError, ValueError) as exc:
        raise CliError("上传中断: %s（断点已保留，加 --resume 可继续）" % exc,
                       EXIT_NETWORK)

    elapsed_ms = int((time.time() - started) * 1000)
    return {
        "file": path,
        "name": payload.get("name", name),
        "space": payload.get("space", space),
        "bytes": payload.get("size", size),
        "sha256": remote_sum or checksum,
        "elapsed_ms": elapsed_ms,
        "verified": bool(remote_sum and remote_sum == checksum),
        "resumed": bool(payload.get("resumed", resumed)),
        "chunks": int(payload.get("chunks", chunks)),
        "upload_id": upload_id,
        "mode": "session",
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


def parse_content_range(value):
    """解析 bytes start-end/total，返回 (start, end, total) 或 None。"""
    try:
        spec = value.split("bytes", 1)[1].strip()
        span, _, total = spec.partition("/")
        first, _, last = span.partition("-")
        return int(first), int(last), int(total)
    except (IndexError, ValueError):
        return None


def download_meta_path(part):
    return part + ".json"


def download_one(server, token, space, name, out_dir, verify, resume=False):
    """下载文件；resume=True 时用同名 .part 文件 + Range 从断点继续。"""
    os.makedirs(out_dir, exist_ok=True)
    local_name = os.path.basename(str(name).replace("\\", "/")) or "download.bin"
    part = os.path.join(out_dir, local_name + ".part")
    meta_path = download_meta_path(part)
    started = time.time()

    offset, etag, total_hint = 0, "", 0
    if resume and os.path.isfile(part):
        offset = os.path.getsize(part)
        try:
            with open(meta_path, "r", encoding="utf-8") as fh:
                meta = json.load(fh)
            if meta.get("space") == space and meta.get("name") == local_name:
                etag = meta.get("etag", "")
                total_hint = int(meta.get("total", 0) or 0)
            else:
                etag, total_hint = "", 0
        except (OSError, ValueError):
            pass
        if offset:
            sys.stderr.write("从本地断点续传：已有 %s%s\n"
                             % (human_size(offset),
                                (" / " + human_size(total_hint)) if total_hint else ""))
    else:
        for stale in (part, meta_path):
            try:
                if os.path.exists(stale):
                    os.remove(stale)
            except OSError:
                pass
        offset, etag, total_hint = 0, "", 0

    restarted = False
    for attempt in range(2):
        params = {"name": name, "space": space, "checksum": "1" if verify else ""}
        req = urllib.request.Request(build_url(server, "/download", params))
        req.add_header("Authorization", "Bearer " + token)
        if offset > 0:
            req.add_header("Range", "bytes=%d-" % offset)
            if etag:
                req.add_header("If-Range", etag)
        try:
            resp = urllib.request.urlopen(req, context=ssl_context(), timeout=_TIMEOUT)
        except urllib.error.HTTPError as exc:
            body = exc.read()
            message = "HTTP %d" % exc.code
            try:
                message = json.loads(body.decode("utf-8")).get("error") or message
            except Exception:
                pass
            if exc.code == 416 and offset > 0 and not restarted:
                # 本地 .part 已超过/不匹配服务器文件：删掉重下
                try:
                    os.remove(part)
                except OSError:
                    pass
                offset, etag, total_hint, restarted = 0, "", 0, True
                continue
            code = EXIT_AUTH if exc.code in (401, 403) else \
                EXIT_NOT_FOUND if exc.code == 404 else EXIT_FAIL
            raise CliError(message, code, {"status": exc.code})
        except urllib.error.URLError as exc:
            raise CliError("下载失败: %s（加 --resume 可从断点继续）"
                           % getattr(exc, "reason", exc), EXIT_NETWORK)

        status = getattr(resp, "status", 200)
        remote_sum = resp.headers.get("X-File-SHA256", "")
        remote_space = resp.headers.get("X-Space", space)
        remote_etag = resp.headers.get("ETag", "")
        remote_total = int(resp.headers.get("X-File-Size") or 0)
        mode = "ab"
        if status == 206:
            content_range = parse_content_range(resp.headers.get("Content-Range", ""))
            if not content_range or content_range[0] != offset:
                resp.close()
                if offset > 0 and not restarted:
                    try:
                        os.remove(part)
                    except OSError:
                        pass
                    offset, etag, total_hint, restarted = 0, "", 0, True
                    continue
                raise CliError("服务器返回的 Content-Range 不正确", EXIT_FAIL)
            remote_total = content_range[2]
        else:
            # 200：服务器忽略 Range（文件已变化或无 If-Range 支持），从头写
            if offset > 0:
                sys.stderr.write("服务器返回完整文件（文件可能已变化），从头下载\n")
            offset = 0
            mode = "wb"

        total = remote_total or total_hint
        progress = Progress("下载中", total, offset)
        progress.update(offset, force=True)
        written = offset
        try:
            with resp:
                with open(part, mode) as fh:
                    while True:
                        block = resp.read(1024 * 1024)
                        if not block:
                            break
                        fh.write(block)
                        written += len(block)
                        progress.update(written)
            progress.finish(written)
        except CliError:
            raise
        except Exception as exc:
            try:
                with open(meta_path, "w", encoding="utf-8") as fh:
                    json.dump({"space": space, "name": local_name,
                               "etag": remote_etag, "total": total}, fh)
            except OSError:
                pass
            raise CliError("下载中断: %s（已保留 %s，加 --resume 可继续）"
                           % (exc, human_size(os.path.getsize(part) if os.path.exists(part)
                                              else 0)), EXIT_NETWORK)

        local_sum = sha256_local(part) if verify else ""
        if verify:
            if not remote_sum:
                raise CliError("服务器未返回 sha256，无法完成校验（确认服务端版本 >= 1.2）",
                               EXIT_CHECKSUM)
            if remote_sum != local_sum:
                try:
                    os.remove(part)
                except OSError:
                    pass
                raise CliError("下载后 sha256 不一致：服务器 %s / 本地 %s"
                               % (remote_sum, local_sum), EXIT_CHECKSUM)

        dest = unique_local_path(out_dir, local_name)
        os.replace(part, dest)
        try:
            os.remove(meta_path)
        except OSError:
            pass
        elapsed_ms = int((time.time() - started) * 1000)
        return {
            "name": local_name,
            "path": dest,
            "space": remote_space,
            "bytes": written,
            "size_header": total,
            "sha256": local_sum or remote_sum,
            "elapsed_ms": elapsed_ms,
            "verified": bool(verify and remote_sum and remote_sum == local_sum),
            "resumed_from": offset if status == 206 or offset else 0,
            "attempt": attempt + 1,
        }

    raise CliError("下载失败（已重试）", EXIT_FAIL)


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
    discovery = data.get("discovery") or {}
    print("发现: %s（UDP %s）" % (
        "广播中" if discovery.get("enabled") else "已关闭", discovery.get("port")))
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
            result = upload_one(args.server, token, args.space, path, args.verify,
                                resume=args.resume)
            result["ok"] = True
            results.append(result)
            if not args.json:
                print("上传完成: %s -> %s (%s, %d ms, %s)"
                      % (os.path.basename(path), result["name"],
                         human_size(result["bytes"]), result["elapsed_ms"],
                         ("续传 %d 片" % result["chunks"]) if result.get("resumed")
                         else "分片 %d 片" % result["chunks"]))
        except CliError as exc:
            results.append({
                "file": path, "ok": False, "error": exc.message, "code": exc.code,
                "resume_hint": bool(os.path.exists(upload_state_path(path))),
            })
            if first_error is None:
                first_error = exc
            if not args.json:
                sys.stderr.write("上传失败: %s: %s\n" % (path, exc.message))
                if os.path.exists(upload_state_path(path)):
                    sys.stderr.write("  断点已保留，重新执行并加 --resume 可继续。\n")

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
    for name in args.names:
        try:
            result = download_one(args.server, token, args.space, name, out_dir,
                                  args.verify, resume=args.resume)
            result["ok"] = True
            results.append(result)
            if not args.json:
                resumed = result.get("resumed_from") or 0
                print("下载完成: %s -> %s (%s, %d ms%s)"
                      % (name, result["path"], human_size(result["bytes"]),
                         result["elapsed_ms"],
                         (", 从 %s 续传" % human_size(resumed)) if resumed else ""))
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


def cmd_rotate(args):
    """轮换全局口令：服务器生成新口令，旧口令在宽限期内仍可用。"""
    token = read_token(args.token_file) or args.token
    status, _, body = http_call(args.server, "/api/token/rotate",
                                {"grace": str(args.grace)}, method="POST",
                                token=token, data=b"",
                                headers={"Content-Length": "0"})
    try:
        data = json.loads(body.decode("utf-8"))
    except Exception:
        raise CliError("服务器返回不是合法 JSON", EXIT_FAIL)
    new_token = str(data.get("token") or "").strip()
    if not new_token:
        raise CliError("服务器未返回新口令", EXIT_FAIL)

    file_updated = False
    if args.token_file:
        try:
            with open(args.token_file, "w", encoding="utf-8") as fh:
                fh.write(new_token + "\n")
            file_updated = True
        except OSError as exc:
            raise CliError("新口令是 %s，但写入口令文件失败: %s" % (new_token, exc),
                           EXIT_FAIL)

    payload = {
        "ok": True,
        "command": "rotate-token",
        "server": args.server,
        "token": new_token,
        "grace_seconds": data.get("grace_seconds"),
        "previous_valid_until": data.get("previous_valid_until"),
        "token_file_updated": file_updated,
        "message": data.get("message", ""),
    }
    if args.json:
        out_json(payload)
    else:
        print("口令已轮换：%s" % new_token)
        print("旧口令在 %s 秒内仍然可用（平滑过渡）。"
              % data.get("grace_seconds", 0))
        if file_updated:
            print("已写入口令文件：%s" % args.token_file)
        else:
            print("请更新客户端口令（例如 MCP 的 --token 或 token 文件）。")
    return EXIT_OK


def local_ipv4():
    """获取本机用于局域网通信的 IPv4 地址（不实际发包）。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("", 0))
        sock.connect(("223.5.5.5", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def same_subnet(ip_a, ip_b):
    """粗略的同网段判断（/24）；无法判断时返回 True，避免误杀。"""
    if not ip_a or not ip_b or ip_a.startswith("127.") or ip_b.startswith("127."):
        return True
    try:
        a, b = ip_a.split("."), ip_b.split(".")
        return len(a) == 4 and len(b) == 4 and a[:3] == b[:3]
    except ValueError:
        return True


def collect_services(timeout=3.0, discovery_port=8766, any_subnet=False):
    """监听 UDP beacon，返回 {local_ip, elapsed_ms, services}。供 CLI 与 MCP 复用。"""
    started = time.time()
    local = local_ipv4()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    except (AttributeError, OSError):
        pass
    try:
        sock.bind(("", discovery_port))
    except OSError as exc:
        raise CliError("无法监听发现端口 %d: %s" % (discovery_port, exc),
                       EXIT_NETWORK)
    sock.settimeout(0.5)

    found = {}
    deadline = started + max(0.5, timeout)
    try:
        while time.time() < deadline:
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError as exc:
                raise CliError("监听广播失败: %s" % exc, EXIT_NETWORK)
            try:
                payload = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                continue
            if payload.get("app") != "lan-transfer":
                continue
            # beacon 里带的是服务端局域网 IP；广播源地址可能是 VPN/虚拟网卡，
            # 因此同网段判断优先用 payload 里的 ip，两者任一匹配即可。
            ip = str(payload.get("ip") or "").strip() or str(addr[0])
            if not any_subnet:
                if not (same_subnet(local, ip) or same_subnet(local, str(addr[0]))):
                    continue
            try:
                port = int(payload.get("port") or 0)
            except (TypeError, ValueError):
                continue
            if port <= 0:
                continue
            scheme = payload.get("scheme") or "https"
            found[(ip, port)] = {
                "ip": ip,
                "port": port,
                "server": "%s://%s:%d" % (scheme, ip, port),
                "version": payload.get("version", ""),
                "spaces": payload.get("spaces", 0),
                "default_space": payload.get("default_space", ""),
                "fallback": bool(payload.get("fallback")),
                "host": payload.get("host", ""),
                "scheme": scheme,
            }
    finally:
        sock.close()

    elapsed_ms = int((time.time() - started) * 1000)
    return {
        "local_ip": local,
        "elapsed_ms": elapsed_ms,
        "services": [found[key] for key in sorted(found)],
    }


def cmd_discover(args):
    """监听 UDP beacon，列出局域网内可用的 lan-transfer 服务。"""
    info = collect_services(args.timeout, args.discovery_port, args.any_subnet)
    local = info["local_ip"]
    elapsed_ms = info["elapsed_ms"]
    items = info["services"]
    if args.json:
        out_json({"ok": True, "command": "discover", "local_ip": local,
                  "elapsed_ms": elapsed_ms, "services": items})
        return EXIT_OK
    if not items:
        print("未发现服务（耗时 %d ms，本机 %s）" % (elapsed_ms, local))
        print("请确认：电脑端已启动、手机/电脑在同一网段、防火墙允许 UDP %d。"
              % args.discovery_port)
        return EXIT_OK
    print("发现 %d 个服务（耗时 %d ms，本机 %s）：" % (len(items), elapsed_ms, local))
    for item in items:
        print("  %-32s v%-8s spaces=%s%s  %s" % (
            item["server"], item["version"] or "?",
            item["spaces"], "  端口顺延" if item["fallback"] else "",
            item["host"]))
    print("可直接使用: --server %s" % items[0]["server"])
    return EXIT_OK


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
    common.add_argument("--resume", action="store_true",
                        help="断点续传（get 用 .part + Range；put 恢复分片会话）")

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

    p = sub.add_parser("rotate-token", parents=[common],
                       help="轮换全局口令（旧口令在宽限期内仍可用）")
    p.add_argument("--grace", type=int, default=1800,
                   help="旧口令宽限期（秒，默认 1800）")
    p.set_defaults(func=cmd_rotate)

    p = sub.add_parser("discover", parents=[common],
                       help="发现局域网内可用的服务（UDP 广播）")
    p.add_argument("--timeout", type=float, default=3.0, help="监听时长（秒，默认 3）")
    p.add_argument("--discovery-port", type=int, default=DISCOVERY_PORT,
                   help="发现端口（默认 %d）" % DISCOVERY_PORT)
    p.add_argument("--any-subnet", action="store_true",
                   help="不限制同网段（默认只接受本机同网段广播）")
    p.set_defaults(func=cmd_discover)

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
