#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
局域网文件互传（LAN Transfer）
核心传输零第三方依赖（纯 Python 标准库），TLS 加密为可选增强。

电脑运行本程序后，手机连同一个 Wi-Fi，扫描窗口里显示的二维码即可：
  · 手机 -> 电脑：在网页里选择 / 拖入文件上传
  · 电脑 -> 手机：把文件放进"接收文件"文件夹，手机刷新后下载
  · 手机 -> 手机：上传后另一台手机从列表里下载

用法:
  python lan_transfer.py            # 启动并显示访问地址 / 二维码
  python lan_transfer.py --open     # 启动后自动用浏览器打开电脑端页面
"""

import argparse
import html
import json
import mimetypes
import os
import re
import secrets
import socket
import sys
import tempfile
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


# --------------------------------------------------------------------------- #
# 路径处理（兼容 PyInstaller 打包）
# --------------------------------------------------------------------------- #
def get_base_dir():
    """获取程序所在目录（打包后指向 exe 所在目录）。"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def get_resource_dir():
    """获取资源文件目录（打包后指向 PyInstaller 临时解压目录）。"""
    if getattr(sys, 'frozen', False):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = get_base_dir()        # exe/脚本 所在目录
RESOURCE_DIR = get_resource_dir()  # 静态资源所在目录（打包后在临时解压目录）
STATIC_DIR = os.path.join(RESOURCE_DIR, "static")
RECEIVE_DIR = os.path.join(BASE_DIR, "接收文件")   # 手机 → 电脑：上传落这里
PUBLISH_DIR = os.path.join(BASE_DIR, "发布文件")   # 电脑 → 手机：拷进来可被下载

TOKEN = ""
HOST_NAME = socket.gethostname()
PORT = 8765
BUF_SIZE = 1024 * 1024
USE_TLS = False  # run() 启动时根据证书可用性更新
SCHEME = "http"

CERT_FILE = os.path.join(BASE_DIR, ".lan_transfer_cert.pem")
KEY_FILE = os.path.join(BASE_DIR, ".lan_transfer_key.pem")


# --------------------------------------------------------------------------- #
# TLS 证书（自签名，首次启动生成并缓存，保证指纹稳定）
# --------------------------------------------------------------------------- #
def ensure_certificate():
    """确保自签名证书存在，返回 (cert, key)；缺少 cryptography 库时返回 None。"""
    if os.path.isfile(CERT_FILE) and os.path.isfile(KEY_FILE):
        return CERT_FILE, KEY_FILE
    try:
        import datetime
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except ImportError:
        return None

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "lan-transfer.local"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "LanTransfer"),
    ])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    with open(KEY_FILE, "wb") as fh:
        fh.write(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption()))
    with open(CERT_FILE, "wb") as fh:
        fh.write(cert.public_bytes(serialization.Encoding.PEM))
    log("  已生成自签名 TLS 证书: %s" % CERT_FILE)
    return CERT_FILE, KEY_FILE


def cert_fingerprint():
    """证书 SHA-256 指纹（冒号分隔大写十六进制），供手机端校验。"""
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes
        with open(CERT_FILE, "rb") as fh:
            cert = x509.load_pem_x509_certificate(fh.read())
        return ":".join("%02X" % b for b in cert.fingerprint(hashes.SHA256()))
    except Exception:
        return ""

KIND_MAP = {
    "image": {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".heic", ".svg", ".ico"},
    "video": {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".flv", ".wmv"},
    "audio": {".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg", ".ape"},
    "doc": {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".md", ".csv", ".epub"},
    "archive": {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"},
    "app": {".exe", ".msi", ".apk", ".dmg", ".pkg", ".deb", ".ipa"},
}

INVALID_CHARS = re.compile(r'[\x00-\x1f<>:"/\\|?*]')
MAX_NAME_LEN = 180


# --------------------------------------------------------------------------- #
# 工具函数
# --------------------------------------------------------------------------- #
def log(msg: str) -> None:
    print(msg, flush=True)


def safe_name(raw: str) -> str:
    """把客户端传来的文件名清洗成安全的纯文件名。"""
    name = raw.replace("\\", "/").split("/")[-1]
    name = INVALID_CHARS.sub("_", name).strip().strip(".")
    if not name:
        name = "未命名文件"
    if len(name) > MAX_NAME_LEN:
        stem, ext = os.path.splitext(name)
        ext = ext[:20]
        name = stem[: MAX_NAME_LEN - len(ext)] + ext
    return name


def unique_name(name: str, base_dir=None) -> str:
    """同名文件自动追加序号，避免覆盖。默认发布目录（电脑→手机）。"""
    target_dir = base_dir or PUBLISH_DIR
    target = os.path.join(target_dir, name)
    if not os.path.exists(target):
        return name
    stem, ext = os.path.splitext(name)
    for i in range(1, 1000):
        candidate = "%s (%d)%s" % (stem, i, ext)
        if not os.path.exists(os.path.join(target_dir, candidate)):
            return candidate
    return "%s-%d%s" % (stem, int(time.time()), ext)


def file_kind(filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    for kind, exts in KIND_MAP.items():
        if ext in exts:
            return kind
    return "other"


def human_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < 1024 or unit == "TB":
            return ("%d %s" % (num, unit)) if unit == "B" else ("%.1f %s" % (num, unit))
        num /= 1024.0


def local_ip() -> str:
    """获取局域网 IP：优先系统默认路由所用的网卡，避开虚拟网卡。"""
    primary = ""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))  # 不会真的发包，仅用于选路
            primary = sock.getsockname()[0]
        finally:
            sock.close()
    except OSError:
        pass

    if primary and not primary.startswith(("127.", "169.254.", "0.")):
        return primary

    ips = set()
    try:
        for info in socket.getaddrinfo(HOST_NAME, None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    candidates = [ip for ip in ips if not ip.startswith(("127.", "169.254.", "0."))]
    if not candidates:
        return "127.0.0.1"

    def score(ip):
        return (
            0 if ip.startswith("192.168.") else 1 if ip.startswith("10.") else 2,
            ip,
        )

    return sorted(candidates, key=score)[0]


def list_files(base_dir) -> list:
    """列出 base_dir 里的文件，返回带 source 标记的列表。"""
    items = []
    try:
        entries = list(os.scandir(base_dir))
    except OSError:
        return items
    for entry in entries:
        try:
            if not entry.is_file() or entry.name.startswith("."):
                continue
            stat = entry.stat()
        except OSError:
            continue
        items.append({
            "name": entry.name,
            "size": stat.st_size,
            "mtime": int(stat.st_mtime),
            "kind": file_kind(entry.name),
            "source": "publish" if base_dir == PUBLISH_DIR else "receive",
        })
    items.sort(key=lambda item: item["mtime"], reverse=True)
    return items


def list_all_files() -> list:
    """合并发布目录 + 接收目录，供手机端下载列表使用。"""
    return list_files(PUBLISH_DIR) + list_files(RECEIVE_DIR)


# --------------------------------------------------------------------------- #
# HTTP 处理
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    server_version = "LanTransfer/1.0"
    protocol_version = "HTTP/1.1"

    # ---------------------------- 基础响应 ---------------------------- #
    def log_message(self, fmt, *args):
        pass  # 控制台保持简洁，不打印每个请求

    def _send(self, status, body=b"", content_type="text/plain; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD" and body:
            self.wfile.write(body)

    def _json(self, data, status=200):
        self._send(status, json.dumps(data, ensure_ascii=False), "application/json; charset=utf-8")

    def _authorized(self, query):
        cookie = self.headers.get("Cookie", "")
        for part in cookie.split(";"):
            key, _, value = part.strip().partition("=")
            if key == "k" and value == TOKEN:
                return True
        return query.get("k", [""])[0] == TOKEN

    def _deny(self):
        page = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>需要连接口令</title>
<style>
body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
background:#0b1020;color:#e8ecf8;font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
.card{max-width:420px;padding:32px 28px;border-radius:20px;background:#151b31;
box-shadow:0 20px 60px rgba(0,0,0,.5);text-align:center}
h1{font-size:19px;margin:0 0 12px}p{color:#93a0c4;line-height:1.7;font-size:14px;margin:0}
</style></head><body><div class="card">
<h1>请扫描电脑上的二维码</h1>
<p>本机互传服务开启了访问口令。<br>请用手机扫描电脑窗口中显示的二维码，<br>
或直接打开完整的链接（带 ?k= 口令）。</p>
<p style="margin-top:10px;font-size:12px;color:#6b7a99">若浏览器提示证书不受信任，<br>选择"继续访问"即可（自签名证书属正常现象）。</p></div></body></html>"""
        self._send(403, page, "text/html; charset=utf-8")

    # ---------------------------- GET ---------------------------- #
    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        path = urllib.parse.unquote(parsed.path)

        if path == "/favicon.ico":
            self._send(204)
            return

        if path == "/" or path == "/index.html":
            if not self._authorized(query):
                self._deny()
                return
            self._serve_index()
            return

        if path.startswith("/static/"):
            self._serve_static(os.path.basename(path))
            return

        if path == "/api/info":
            if not self._authorized(query):
                self._json({"error": "unauthorized"}, 403)
                return
            self._json(self._info())
            return

        if path == "/api/files":
            if not self._authorized(query):
                self._json({"error": "unauthorized"}, 403)
                return
            self._json({"files": list_all_files()})
            return

        if path == "/download":
            if not self._authorized(query):
                self._json({"error": "unauthorized"}, 403)
                return
            self._download(query)
            return

        self._send(404, "404 Not Found")

    # ---------------------------- POST ---------------------------- #
    def do_POST(self):
        parsed = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        if urllib.parse.unquote(parsed.path) != "/api/upload":
            self._send(404, "404 Not Found")
            return
        if not self._authorized(query):
            self.close_connection = True  # 请求体未读取，不复用连接
            self._json({"error": "unauthorized"}, 403)
            return
        self._upload(query)

    # ---------------------------- DELETE ---------------------------- #
    def _resolve_file(self, name):
        """优先从发布目录、其次接收目录查找文件，返回 (path, source) 或 None。"""
        for source, base in (("publish", PUBLISH_DIR), ("receive", RECEIVE_DIR)):
            path = os.path.join(base, name)
            if os.path.isfile(path):
                return path, source
        return None

    def do_DELETE(self):
        parsed = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        if urllib.parse.unquote(parsed.path) != "/api/files":
            self._send(404, "404 Not Found")
            return
        if not self._authorized(query):
            self._json({"error": "unauthorized"}, 403)
            return
        raw = query.get("name", [""])[0]
        name = safe_name(raw) if raw else ""
        target = self._resolve_file(name)
        if not name or target is None:
            self._json({"error": "文件不存在"}, 404)
            return
        path, source = target
        try:
            os.remove(path)
        except OSError as exc:
            self._json({"error": "删除失败: %s" % exc}, 500)
            return
        log("  已删除: %s (%s)" % (name, source))
        self._json({"ok": True})

    # ---------------------------- 页面 / 静态文件 ---------------------------- #
    def _serve_index(self):
        index_path = os.path.join(STATIC_DIR, "index.html")
        try:
            with open(index_path, "rb") as fh:
                body = fh.read()
        except OSError:
            self._send(500, "缺少 static/index.html")
            return
        extra = {
            "Set-Cookie": "k=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=86400" % TOKEN,
        }
        self._send(200, body, "text/html; charset=utf-8", extra)

    def _serve_static(self, name):
        allowed = {"qrcode.js", "app.js", "style.css"}
        if name not in allowed:
            self._send(404, "404 Not Found")
            return
        path = os.path.join(STATIC_DIR, name)
        if not os.path.isfile(path):
            self._send(404, "404 Not Found")
            return
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        with open(path, "rb") as fh:
            body = fh.read()
        self._send(200, body, ctype, {"Cache-Control": "max-age=86400"})

    # ---------------------------- 信息 ---------------------------- #
    def _info(self):
        ip = local_ip()
        share_url = "%s://%s:%d/?k=%s" % (SCHEME, ip, PORT, TOKEN)
        pub_files = list_files(PUBLISH_DIR)
        recv_files = list_files(RECEIVE_DIR)
        return {
            "host": HOST_NAME,
            "ip": ip,
            "port": PORT,
            "https": USE_TLS,
            "fingerprint": cert_fingerprint() if USE_TLS else "",
            "share_url": share_url,
            "local_url": "%s://127.0.0.1:%d/?k=%s" % (SCHEME, PORT, TOKEN),
            "publish_dir": PUBLISH_DIR,
            "receive_dir": RECEIVE_DIR,
            "publish_count": len(pub_files),
            "receive_count": len(recv_files),
            "publish_size": sum(item["size"] for item in pub_files),
            "receive_size": sum(item["size"] for item in recv_files),
        }

    # ---------------------------- 上传 ---------------------------- #
    def _upload(self, query):
        raw_name = query.get("name", [""])[0]
        if not raw_name.strip():
            self.close_connection = True  # 请求体未读取，不复用连接
            self._json({"error": "缺少文件名"}, 400)
            return
        name = safe_name(raw_name)
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0:
            self._json({"error": "缺少 Content-Length"}, 411)
            return

        dest_name = unique_name(name, RECEIVE_DIR)
        dest_path = os.path.join(RECEIVE_DIR, dest_name)
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(prefix=".upload-", suffix=".part", dir=RECEIVE_DIR)
            with os.fdopen(fd, "wb") as out:
                remaining = length
                while remaining > 0:
                    chunk = self.rfile.read(min(BUF_SIZE, remaining))
                    if not chunk:
                        raise ConnectionError("连接中断")
                    out.write(chunk)
                    remaining -= len(chunk)
            os.replace(tmp, dest_path)
            tmp = None
        except (OSError, ConnectionError) as exc:
            self.close_connection = True
            self._json({"error": "上传失败: %s" % exc}, 500)
            return
        finally:
            if tmp and os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

        log("  收到 %s (%s)" % (dest_name, human_size(length)))
        self._json({"ok": True, "name": dest_name, "size": length})

    # ---------------------------- 下载 ---------------------------- #
    def _download(self, query):
        raw = query.get("name", [""])[0]
        name = safe_name(raw) if raw else ""
        resolved = self._resolve_file(name)
        if not name or resolved is None:
            self._json({"error": "文件不存在"}, 404)
            return
        path, source = resolved

        size = os.path.getsize(path)
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        if name.lower().endswith((".md", ".txt", ".csv", ".json")):
            ctype = "text/plain; charset=utf-8"

        inline = query.get("inline", [""])[0] in ("1", "true")
        quoted = urllib.parse.quote(name)
        disposition = "%s; filename=\"%s\"; filename*=UTF-8''%s" % (
            "inline" if inline else "attachment",
            quoted,
            quoted,
        )

        start, end = 0, size - 1
        status = 200
        range_header = self.headers.get("Range", "")
        if range_header.startswith("bytes="):
            try:
                spec = range_header[6:].split(",")[0].strip()
                first, _, last = spec.partition("-")
                if first:
                    start = int(first)
                    end = int(last) if last else size - 1
                elif last:  # 后缀范围 bytes=-500
                    start = max(0, size - int(last))
                if start >= size or end < start:
                    raise ValueError
                end = min(end, size - 1)
                status = 206
            except ValueError:
                self._send(416, "range not satisfiable", extra={
                    "Content-Range": "bytes */%d" % size
                })
                return

        length = 0 if size == 0 else end - start + 1

        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Disposition", disposition)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        if status == 206:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        self.end_headers()

        if self.command == "HEAD" or length == 0:
            return
        try:
            with open(path, "rb") as fh:
                fh.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = fh.read(min(BUF_SIZE, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    # HEAD 仅用于浏览器探测，复用 GET 逻辑
    def do_HEAD(self):
        self.do_GET()


# --------------------------------------------------------------------------- #
# 启动
# --------------------------------------------------------------------------- #
def banner(url: str, ip_url: str) -> None:
    line = "=" * 58
    log(line)
    log("  局域网互传 已启动" + ("（HTTPS 加密）" if USE_TLS else "（HTTP 明文）"))
    log("")
    log("  电脑本机:  %s" % url)
    log("  手机/其他设备: %s" % ip_url)
    log("")
    if USE_TLS:
        log("  证书指纹:  %s" % cert_fingerprint())
        log("")
    log("  接收目录:  %s" % RECEIVE_DIR)
    log("  发布目录:  %s" % PUBLISH_DIR)
    log("  提示: 手机与电脑需连接同一个 Wi-Fi；首次运行若弹出")
    log("        Windows 防火墙提示，请选择 \"允许访问\"。")
    log("  按 Ctrl+C 停止服务")
    log(line)
    log("")


def run(port: int, open_browser: bool, bind: str) -> ThreadingHTTPServer:
    """启动服务并返回 server 对象，供 GUI 控制。"""
    global PORT, USE_TLS, SCHEME
    os.makedirs(RECEIVE_DIR, exist_ok=True)
    os.makedirs(PUBLISH_DIR, exist_ok=True)

    server = None
    for candidate in range(port, port + 12):
        try:
            server = ThreadingHTTPServer((bind, candidate), Handler)
            PORT = candidate
            break
        except OSError:
            continue
    if server is None:
        raise RuntimeError("端口 %d-%d 均被占用。" % (port, port + 11))

    # 包装 TLS（证书可用时启用 HTTPS）
    tls = ensure_certificate()
    if tls:
        import ssl
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(tls[0], tls[1])
        server.socket = ctx.wrap_socket(server.socket, server_side=True)
        USE_TLS = True
        SCHEME = "https"
    else:
        log("  [警告] 未安装 cryptography 库，以 HTTP 明文模式运行。")
        log("         执行 pip install cryptography 后可启用 HTTPS 加密传输。")
        USE_TLS = False
        SCHEME = "http"

    server.daemon_threads = True
    server.serve_forever_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server.serve_forever_thread.start()

    ip = local_ip()
    web_url = "%s://127.0.0.1:%d/?k=%s" % (SCHEME, PORT, TOKEN)
    share_url = "%s://%s:%d/?k=%s" % (SCHEME, ip, PORT, TOKEN)

    banner(web_url, share_url)

    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(web_url)).start()

    return server


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="局域网文件互传")
    parser.add_argument("--port", type=int, default=8765, help="服务端口，默认 8765")
    parser.add_argument("--bind", default="0.0.0.0", help="监听地址，默认 0.0.0.0")
    parser.add_argument("--open", action="store_true", help="启动后自动打开电脑端页面")
    args = parser.parse_args()

    global TOKEN
    TOKEN = "".join(secrets.choice("abcdefghjkmnpqrstuvwxyz23456789") for _ in range(10))

    try:
        server = run(args.port, args.open, args.bind)
        try:
            while True:
                import time as _time
                _time.sleep(1)
        except KeyboardInterrupt:
            log("")
            log("服务已停止。")
    finally:
        pass


if __name__ == "__main__":
    main()
