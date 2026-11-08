#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
局域网文件互传（LAN Transfer）核心服务

特性：
  · 手机 / 电脑 / 其他程序在同一局域网内互传文件
  · 多空间隔离（spaces），每个空间可配置只读/读写、独立口令
  · 可选无界面 headless 常驻，供 CLI / 其他项目 / AI 作为传输通道
  · 默认只监听本机局域网 IP，端口被占用自动顺延
  · 上传审计日志（按天切分）与结构化服务日志

用法:
  python lan_transfer.py                 # 前台启动，打印访问地址 / 二维码链接
  python lan_transfer.py --open          # 启动后自动打开电脑端页面
  python lan_transfer.py --headless      # 无界面常驻（写 PID / 日志）
"""

import argparse
import email.utils
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import signal
import socket
import sys
import tempfile
import threading
import time
import urllib.parse
import webbrowser
from collections import OrderedDict
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP_NAME = "lan-transfer"
APP_VERSION = "1.7.0"
API_VERSION = 1

TOKEN_CHARS = "abcdefghjkmnpqrstuvwxyz23456789"


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


BASE_DIR = get_base_dir()            # exe/脚本 所在目录（--data-dir 可覆盖）
RESOURCE_DIR = get_resource_dir()    # 静态资源目录
STATIC_DIR = os.path.join(RESOURCE_DIR, "static")

RECEIVE_DIR = os.path.join(BASE_DIR, "received-files")
PUBLISH_DIR = os.path.join(BASE_DIR, "shared-files")
LEGACY_DIRS = (("接收文件", RECEIVE_DIR), ("发布文件", PUBLISH_DIR))

SPACES_FILE = os.path.join(BASE_DIR, "spaces.yaml")
PID_FILE = os.path.join(BASE_DIR, "lan-transfer.pid")
LOG_DIR = os.path.join(BASE_DIR, "logs")
LOG_FILE = ""                        # --log-file 指定的固定日志文件

TOKEN = ""
TOKEN_FILE = ""                      # --token-file（headless 用）

# 口令轮换：旧口令在宽限期内继续可用，避免客户端被一刀切踢下线
TOKEN_HISTORY = []                   # [{"token": str, "expires": epoch}]
TOKEN_HISTORY_LOCK = threading.Lock()
HOST_NAME = socket.gethostname()
PORT = 8765
PORT_REQUESTED = 8765
PORT_IS_FALLBACK = False
BIND_ADDR = "0.0.0.0"
BIND_LAN_ONLY = False
BUF_SIZE = 1024 * 1024
USE_TLS = False
SCHEME = "http"
HEADLESS = False

# UDP 局域网发现（beacon 不含口令）
DISCOVERY_PORT = 8766
DISCOVERY_INTERVAL = 3.0
DISCOVERY_ENABLED = True
NO_TLS = False
_beacon_stop = None
_beacon_thread = None

# 分片上传会话
SESSION_IDLE_TIMEOUT = 30 * 60          # 30 分钟没动静就清理
SESSION_CHUNK_SIZE = 8 * 1024 * 1024    # 建议分片大小（客户端可自定）
SESSION_CLEANUP_INTERVAL = 60
UPLOAD_SESSIONS = {}
UPLOAD_SESSIONS_LOCK = threading.Lock()
_cleanup_thread = None
# 服务端同时处理的“上传写入”上限（一次性上传与分片 PUT 共用），超出返回 429
UPLOAD_LIMIT = 4
UPLOAD_SLOTS = threading.BoundedSemaphore(UPLOAD_LIMIT)

CERT_FILE = os.path.join(BASE_DIR, ".lan_transfer_cert.pem")
KEY_FILE = os.path.join(BASE_DIR, ".lan_transfer_key.pem")
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")
SESSION_FILE = os.path.join(BASE_DIR, "upload-sessions.json")

# 空间：name -> {name, path, mode, token, description}
SPACES = OrderedDict()
DEFAULT_SPACE_NAME = ""

LOG_LOCK = threading.Lock()
AUDIT_LOCK = threading.Lock()


# --------------------------------------------------------------------------- #
# 运行目录 / 空间配置
# --------------------------------------------------------------------------- #
def configure_data_dir(path):
    """把运行时目录（文件、证书、配置、日志）切到指定目录。"""
    global BASE_DIR, RECEIVE_DIR, PUBLISH_DIR
    global CERT_FILE, KEY_FILE, SPACES_FILE, PID_FILE, LOG_DIR, SETTINGS_FILE
    global SESSION_FILE
    if not path:
        return
    BASE_DIR = os.path.abspath(os.path.expanduser(path))
    RECEIVE_DIR = os.path.join(BASE_DIR, "received-files")
    PUBLISH_DIR = os.path.join(BASE_DIR, "shared-files")
    CERT_FILE = os.path.join(BASE_DIR, ".lan_transfer_cert.pem")
    KEY_FILE = os.path.join(BASE_DIR, ".lan_transfer_key.pem")
    SPACES_FILE = os.path.join(BASE_DIR, "spaces.yaml")
    PID_FILE = os.path.join(BASE_DIR, "lan-transfer.pid")
    LOG_DIR = os.path.join(BASE_DIR, "logs")
    SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")
    SESSION_FILE = os.path.join(BASE_DIR, "upload-sessions.json")


DEFAULT_SPACES_YAML = """# 局域网互传 - 空间配置
# mode: rw（可上传、可删除） / ro（只读，仅允许下载）
# token: 可选，给该空间单独设置口令（留空表示只认全局口令）
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
"""


def _unquote(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def parse_spaces_yaml(text):
    """解析 spaces.yaml 的最小可用子集（顶层键 + spaces 列表）。"""
    result = {"spaces": []}
    current = None
    in_spaces = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip() if raw.lstrip().startswith("#") else raw.rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        item = line.strip()
        if indent == 0:
            current = None
            if item.endswith(":"):
                key = item[:-1].strip()
                in_spaces = key == "spaces"
                continue
            in_spaces = False
            key, _, value = item.partition(":")
            result[key.strip()] = _unquote(value)
            continue
        if not in_spaces:
            continue
        if item.startswith("- "):
            current = {}
            result["spaces"].append(current)
            item = item[2:].strip()
        if current is not None and ":" in item:
            key, _, value = item.partition(":")
            current[key.strip()] = _unquote(value)
    return result


def _write_default_spaces(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(DEFAULT_SPACES_YAML)
    log("  已生成空间配置: %s" % path)


def load_spaces(config_path=None, create=True):
    """加载空间配置；不存在时自动生成示例并载入。配置有误时抛 ValueError。"""
    global SPACES, DEFAULT_SPACE_NAME, SPACES_FILE
    if config_path:
        SPACES_FILE = os.path.abspath(os.path.expanduser(config_path))

    path = SPACES_FILE
    if not os.path.isfile(path):
        if not create:
            raise ValueError("空间配置文件不存在: %s" % path)
        _write_default_spaces(path)

    if path.lower().endswith(".json"):
        with open(path, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
    else:
        with open(path, "r", encoding="utf-8") as fh:
            cfg = parse_spaces_yaml(fh.read())

    items = cfg.get("spaces") or []
    if not isinstance(items, list) or not items:
        raise ValueError("空间配置里没有 spaces 列表: %s" % path)

    base = os.path.dirname(path)
    loaded = OrderedDict()
    seen_paths = {}
    for entry in items:
        if not isinstance(entry, dict):
            raise ValueError("spaces 每一项都必须是对象")
        name = str(entry.get("name", "")).strip()
        if not re.match(r"^[A-Za-z0-9_-]{1,40}$", name):
            raise ValueError("空间名不合法（仅允许字母数字 _ -）: %r" % name)
        if name in loaded:
            raise ValueError("空间名重复: %s" % name)
        raw_path = str(entry.get("path", "")).strip()
        if not raw_path:
            raise ValueError("空间 %s 缺少 path" % name)
        space_path = os.path.abspath(os.path.expanduser(raw_path))
        if not os.path.isabs(os.path.expanduser(raw_path)):
            space_path = os.path.abspath(os.path.join(base, raw_path))
        real = os.path.realpath(space_path)
        if real in seen_paths:
            raise ValueError("空间 %s 与 %s 使用了同一个目录: %s"
                             % (name, seen_paths[real], real))
        seen_paths[real] = name
        mode = str(entry.get("mode", "rw")).strip().lower()
        if mode not in ("rw", "ro"):
            raise ValueError("空间 %s 的 mode 只能是 rw 或 ro" % name)
        try:
            os.makedirs(space_path, exist_ok=True)
        except OSError as exc:
            raise ValueError("空间 %s 目录不可用: %s" % (name, exc))
        loaded[name] = {
            "name": name,
            "path": space_path,
            "mode": mode,
            "token": str(entry.get("token", "")).strip(),
            "description": str(entry.get("description", "")).strip(),
        }

    default = str(cfg.get("default_space", "")).strip()
    if default not in loaded:
        default = next(iter(loaded.keys()))
    SPACES = loaded
    DEFAULT_SPACE_NAME = default
    log("  空间配置: %s（%d 个空间，默认 %s）" % (path, len(loaded), default))
    return loaded


def space_by_name(name):
    return SPACES.get(name)


# --------------------------------------------------------------------------- #
# 本地设置 / UDP 局域网发现
# --------------------------------------------------------------------------- #
def load_settings():
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(data):
    try:
        os.makedirs(os.path.dirname(os.path.abspath(SETTINGS_FILE)), exist_ok=True)
        with open(SETTINGS_FILE, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass


def set_discovery_enabled(enabled, persist=True):
    """开关局域网 UDP 广播；persist=True 时写入 settings.json。"""
    global DISCOVERY_ENABLED
    DISCOVERY_ENABLED = bool(enabled)
    if persist:
        cfg = load_settings()
        cfg["discovery"] = DISCOVERY_ENABLED
        save_settings(cfg)
    if DISCOVERY_ENABLED:
        start_beacon()
    else:
        stop_beacon()
    slog("discovery_toggle", enabled=DISCOVERY_ENABLED)
    return DISCOVERY_ENABLED


def start_beacon():
    """周期性向局域网广播 UDP beacon（只含元信息，不含口令）。"""
    global _beacon_stop, _beacon_thread
    if _beacon_thread is not None and _beacon_thread.is_alive():
        return
    stop = threading.Event()
    _beacon_stop = stop

    def loop():
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            if BIND_ADDR and BIND_ADDR not in ("0.0.0.0", "::", ""):
                sock.bind((BIND_ADDR, 0))
        except OSError:
            pass
        while not stop.is_set():
            if DISCOVERY_ENABLED:
                try:
                    payload = {
                        "app": APP_NAME,
                        "version": APP_VERSION,
                        "port": PORT,
                        "scheme": SCHEME,
                        "spaces": len(SPACES),
                        "default_space": DEFAULT_SPACE_NAME,
                        "fallback": PORT_IS_FALLBACK,
                        "host": HOST_NAME,
                        "ip": local_ip(),
                        "discovery_port": DISCOVERY_PORT,
                    }
                    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                    sock.sendto(data, ("255.255.255.255", DISCOVERY_PORT))
                except OSError:
                    pass
            if stop.wait(DISCOVERY_INTERVAL):
                break
        try:
            sock.close()
        except OSError:
            pass

    _beacon_thread = threading.Thread(target=loop, daemon=True, name="lan-beacon")
    _beacon_thread.start()
    slog("discovery_started", port=DISCOVERY_PORT, interval=DISCOVERY_INTERVAL)


def stop_beacon():
    global _beacon_thread, _beacon_stop
    if _beacon_stop:
        _beacon_stop.set()
    _beacon_thread = None
    _beacon_stop = None
    slog("discovery_stopped")


# --------------------------------------------------------------------------- #
# TLS 证书
# --------------------------------------------------------------------------- #
def ensure_certificate():
    if os.path.isfile(CERT_FILE) and os.path.isfile(KEY_FILE):
        return CERT_FILE, KEY_FILE
    try:
        import datetime as _dt
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
    now = _dt.datetime.now(_dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - _dt.timedelta(minutes=5))
        .not_valid_after(now + _dt.timedelta(days=3650))
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
    try:
        print(msg, flush=True)
    except Exception:
        pass


def slog(event, level="info", **fields):
    """结构化服务日志（JSON Lines，默认按天切分）。"""
    record = {
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "level": level,
        "event": event,
    }
    record.update(fields)
    try:
        if LOG_FILE:
            path = LOG_FILE
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        else:
            os.makedirs(LOG_DIR, exist_ok=True)
            path = os.path.join(LOG_DIR, "service-%s.log" % time.strftime("%Y-%m-%d"))
        line = json.dumps(record, ensure_ascii=False)
        with LOG_LOCK:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except Exception:
        pass


def audit_upload(space, name, size, ip, ok, sha256="", error="",
                 resumed=False, chunked=False, session=""):
    """上传审计日志（按天切分，单独文件）。"""
    record = {
        "time": datetime.now().astimezone().isoformat(timespec="seconds"),
        "event": "upload",
        "space": space,
        "name": name,
        "bytes": int(size or 0),
        "ip": ip,
        "success": bool(ok),
        "sha256": sha256,
        "error": error,
        "resumed": bool(resumed),
        "chunked": bool(chunked),
        "session": session,
    }
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        path = os.path.join(LOG_DIR, "upload-audit-%s.log" % time.strftime("%Y-%m-%d"))
        with AUDIT_LOCK:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


def session_temp_path(space_name, session_id):
    return os.path.join(SPACES[space_name]["path"], ".lt-upload-%s.part" % session_id)


def truncate_session_temp(path, size):
    """把分片临时文件截断到已确认的偏移，丢弃半截分片，避免续传数据错位。"""
    try:
        with open(path, "r+b") as fh:
            fh.truncate(size)
    except OSError:
        pass


def cleanup_upload_sessions(now=None, force_all=False):
    """清理超时未完成的分片上传会话与临时文件。"""
    now = now or time.time()
    expired = []
    with UPLOAD_SESSIONS_LOCK:
        for sid, session in list(UPLOAD_SESSIONS.items()):
            if force_all or now - session["updated"] > SESSION_IDLE_TIMEOUT:
                expired.append(UPLOAD_SESSIONS.pop(sid))
    for session in expired:
        path = session_temp_path(session["space"], session["id"])
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass
        slog("upload_session_cleanup", session=session["id"], space=session["space"],
             name=session["name"], bytes=session["offset"])
    if expired:
        save_upload_sessions()
    return len(expired)


def save_upload_sessions():
    """把未完成的会话元数据落盘，服务重启后可继续接收分片。"""
    with UPLOAD_SESSIONS_LOCK:
        data = {"version": 1, "saved": time.time(), "sessions": {}}
        for sid, session in UPLOAD_SESSIONS.items():
            data["sessions"][sid] = {
                "name": session["name"],
                "space": session["space"],
                "size": session["size"],
                "offset": session["offset"],
                "sha256": session["sha256"],
                "created": session["created"],
                "updated": session["updated"],
                "chunks": session["chunks"],
                "resumed": session["resumed"],
                "ip": session.get("ip", ""),
            }
    tmp = SESSION_FILE + ".tmp"
    try:
        os.makedirs(os.path.dirname(SESSION_FILE), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        os.replace(tmp, SESSION_FILE)
    except OSError as exc:
        slog("upload_sessions_save", level="error", error=str(exc))


def load_upload_sessions():
    """启动时恢复未完成会话；临时文件缺失/偏移不符/已超时的直接丢弃。"""
    try:
        with open(SESSION_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return 0
    now = time.time()
    restored = 0
    raw_sessions = data.get("sessions") if isinstance(data, dict) else None
    if not isinstance(raw_sessions, dict):
        return 0
    for sid, item in raw_sessions.items():
        if not isinstance(item, dict):
            continue
        space_name = item.get("space")
        if space_name not in SPACES:
            continue
        try:
            size = int(item["size"])
            offset = int(item["offset"])
            created = float(item.get("created", now))
            updated = float(item.get("updated", now))
        except (KeyError, TypeError, ValueError):
            continue
        if now - updated > SESSION_IDLE_TIMEOUT:
            continue
        tmp = session_temp_path(space_name, sid)
        if not os.path.isfile(tmp):
            continue
        actual = os.path.getsize(tmp)
        if actual < offset:
            continue
        if actual > offset:
            if offset <= 0:
                continue
            truncate_session_temp(tmp, offset)
        with UPLOAD_SESSIONS_LOCK:
            UPLOAD_SESSIONS[sid] = {
                "id": sid,
                "name": item.get("name") or "upload.bin",
                "space": space_name,
                "size": size,
                "offset": offset,
                "sha256": str(item.get("sha256") or ""),
                "created": created,
                "updated": updated,
                "chunks": int(item.get("chunks", 0) or 0),
                "resumed": bool(item.get("resumed")),
                "ip": item.get("ip", ""),
                "lock": threading.Lock(),
            }
        restored += 1
    if restored:
        slog("upload_sessions_restored", count=restored)
    else:
        try:
            os.remove(SESSION_FILE)
        except OSError:
            pass
    return restored


def cleanup_orphan_parts():
    """启动时清理上次崩溃留下的过期 .part 临时文件（跳过已恢复的会话）。"""
    cutoff = time.time() - SESSION_IDLE_TIMEOUT
    with UPLOAD_SESSIONS_LOCK:
        active = {"%s:%s" % (s["space"], s["id"]) for s in UPLOAD_SESSIONS.values()}
    for space in SPACES.values():
        try:
            entries = list(os.scandir(space["path"]))
        except OSError:
            continue
        for entry in entries:
            if not (entry.name.startswith(".lt-upload-") and entry.name.endswith(".part")):
                continue
            sid = entry.name[len(".lt-upload-"):-len(".part")]
            if "%s:%s" % (space["name"], sid) in active:
                continue
            try:
                if os.path.isfile(entry.path) and entry.stat().st_mtime < cutoff:
                    os.remove(entry.path)
                    slog("upload_part_cleanup", space=space["name"], name=entry.name)
            except OSError:
                pass


def start_session_cleanup():
    global _cleanup_thread
    if _cleanup_thread is not None and _cleanup_thread.is_alive():
        return

    def loop():
        cleanup_orphan_parts()
        while True:
            time.sleep(SESSION_CLEANUP_INTERVAL)
            try:
                cleanup_upload_sessions()
            except Exception:
                pass

    _cleanup_thread = threading.Thread(target=loop, daemon=True,
                                       name="upload-session-cleanup")
    _cleanup_thread.start()


def safe_name(raw: str) -> str:
    """把客户端传来的文件名清洗成安全的纯文件名（不允许任何目录层级）。"""
    name = (raw or "").replace("\\", "/").split("/")[-1]
    name = INVALID_CHARS.sub("_", name).strip().strip(".")
    if not name:
        name = "未命名文件"
    if len(name) > MAX_NAME_LEN:
        stem, ext = os.path.splitext(name)
        ext = ext[:20]
        name = stem[: MAX_NAME_LEN - len(ext)] + ext
    return name


def unique_name(name: str, base_dir=None) -> str:
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


def sha256_file(path, chunk=BUF_SIZE):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def local_ip() -> str:
    primary = ""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
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
        return (0 if ip.startswith("192.168.") else 1 if ip.startswith("10.") else 2, ip)

    return sorted(candidates, key=score)[0]


def is_lan_ip(ip: str) -> bool:
    return ip.startswith(("10.", "192.168.", "172.16.", "172.17.", "172.18.", "172.19.",
                          "172.2", "172.30.", "172.31."))


def access_host() -> str:
    if BIND_ADDR and BIND_ADDR not in ("0.0.0.0", "::", ""):
        return BIND_ADDR
    return "127.0.0.1"


def build_url(host: str = None) -> str:
    target = host or access_host()
    return "%s://%s:%d/?k=%s" % (SCHEME, target, PORT, TOKEN)


def migrate_legacy_dirs() -> None:
    import shutil
    for old_name, new_dir in LEGACY_DIRS:
        old_dir = os.path.join(BASE_DIR, old_name)
        if not os.path.isdir(old_dir):
            continue
        try:
            if not os.path.isdir(new_dir):
                os.rename(old_dir, new_dir)
                log("  已迁移目录: %s -> %s" % (old_name, os.path.basename(new_dir)))
                continue
            for entry in list(os.scandir(old_dir)):
                if not entry.is_file():
                    continue
                dest = os.path.join(new_dir, unique_name(entry.name, new_dir))
                shutil.move(entry.path, dest)
            os.rmdir(old_dir)
            log("  已合并旧目录: %s" % old_name)
        except OSError as exc:
            log("  [警告] 旧目录迁移失败: %s" % exc)


def _legacy_source(space_name):
    if space_name == "inbox":
        return "receive"
    if space_name == "shared":
        return "publish"
    return "space"


def list_files(base_dir, space_name=None) -> list:
    """列出目录里的文件；space_name 用于给前端标记所属空间。"""
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
            "source": _legacy_source(space_name),
            "space": space_name or "",
        })
    items.sort(key=lambda item: item["mtime"], reverse=True)
    return items


def list_all_files() -> list:
    """兼容旧前端：合并所有空间的文件。"""
    items = []
    for name, space in SPACES.items():
        items.extend(list_files(space["path"], name))
    items.sort(key=lambda item: item["mtime"], reverse=True)
    return items


def count_space_files(space):
    items = list_files(space["path"], space["name"])
    return len(items), sum(item["size"] for item in items)


# --------------------------------------------------------------------------- #
# HTTP 处理
# --------------------------------------------------------------------------- #
def _constant_compare(a, b):
    if not a or not b:
        return False
    try:
        return hmac.compare_digest(a, b)
    except TypeError:
        return False


def previous_token_ok(token):
    """旧口令在宽限期内仍然有效（口令轮换的平滑过渡）。"""
    if not token:
        return False
    now = time.time()
    with TOKEN_HISTORY_LOCK:
        for item in TOKEN_HISTORY:
            if float(item.get("expires", 0)) > now and \
                    _constant_compare(token, str(item.get("token", ""))):
                return True
    return False


def global_token_ok(token):
    return _constant_compare(token, TOKEN) or previous_token_ok(token)


def persist_token_state():
    """把当前口令与旧口令宽限期写入 settings.json（重启后仍生效）。"""
    cfg = load_settings()
    cfg["token"] = TOKEN
    with TOKEN_HISTORY_LOCK:
        cfg["token_history"] = [dict(item) for item in TOKEN_HISTORY]
    save_settings(cfg)


def apply_persisted_token():
    """启动时恢复上次轮换后的口令与旧口令宽限期（token 文件优先）。"""
    global TOKEN
    cfg = load_settings()
    saved = str(cfg.get("token") or "").strip()
    now = time.time()
    history = []
    for item in cfg.get("token_history") or []:
        if not isinstance(item, dict):
            continue
        token = str(item.get("token") or "").strip()
        try:
            expires = float(item.get("expires", 0))
        except (TypeError, ValueError):
            continue
        if token and expires > now:
            history.append({"token": token, "expires": expires})
    with TOKEN_HISTORY_LOCK:
        TOKEN_HISTORY[:] = history
    if saved and not TOKEN_FILE:
        TOKEN = saved


class Handler(BaseHTTPRequestHandler):
    server_version = "LanTransfer/%s" % APP_VERSION
    protocol_version = "HTTP/1.1"

    # ---------------------------- 基础响应 ---------------------------- #
    def log_message(self, fmt, *args):
        pass

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

    def _json(self, data, status=200, extra=None):
        self._send(status, json.dumps(data, ensure_ascii=False),
                   "application/json; charset=utf-8", extra)

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

    # ---------------------------- 鉴权 ---------------------------- #
    def _token(self, query):
        auth = self.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        cookie = self.headers.get("Cookie", "")
        for part in cookie.split(";"):
            key, _, value = part.strip().partition("=")
            if key == "k":
                if value:
                    return value
        return query.get("k", [""])[0]

    @staticmethod
    def _global_token_ok(token):
        return global_token_ok(token)

    @staticmethod
    def _space_token_ok(token, name):
        if global_token_ok(token):
            return True
        space = SPACES.get(name)
        return bool(space and space.get("token") and _constant_compare(token, space["token"]))

    def _any_token_ok(self, token):
        if self._global_token_ok(token):
            return True
        return any(space.get("token") and _constant_compare(token, space["token"])
                   for space in SPACES.values())

    def _space_access(self, token, name):
        """校验空间访问权；返回 (space, None) 或 (None, (status, message))。"""
        space = SPACES.get(name)
        if not space:
            return None, (404, "空间不存在: %s" % name)
        if not self._space_token_ok(token, name):
            return None, (403, "口令无权访问空间: %s" % name)
        return space, None

    def _requested_space(self, query):
        return query.get("space", [""])[0].strip()

    def _token_rotate(self, query):
        """轮换全局口令：生成新口令，旧口令在宽限期内仍然可用。"""
        global TOKEN
        token = self._token(query)
        if not self._global_token_ok(token):
            self._json({"error": "需要当前全局口令才能轮换", "code": "unauthorized"}, 403)
            return
        try:
            grace = int(query.get("grace", ["1800"])[0])
        except (TypeError, ValueError):
            grace = 1800
        grace = max(60, min(grace, 7 * 24 * 3600))

        new_token = "".join(secrets.choice(TOKEN_CHARS) for _ in range(10))
        old_token = TOKEN
        now = time.time()
        with TOKEN_HISTORY_LOCK:
            TOKEN_HISTORY[:] = [item for item in TOKEN_HISTORY
                                if float(item.get("expires", 0)) > now]
            if old_token:
                TOKEN_HISTORY.append({"token": old_token, "expires": now + grace})
        TOKEN = new_token
        persist_token_state()
        if TOKEN_FILE:
            try:
                with open(TOKEN_FILE, "w", encoding="utf-8") as fh:
                    fh.write(new_token + "\n")
                try:
                    os.chmod(TOKEN_FILE, 0o600)
                except OSError:
                    pass
            except OSError as exc:
                slog("token_file_write_failed", level="error", error=str(exc))
        slog("token_rotate", grace=grace)
        self._json({
            "ok": True,
            "token": new_token,
            "grace_seconds": grace,
            "previous_valid_until": int(now + grace),
            "message": "口令已轮换，旧口令在 %d 秒内仍然可用" % grace,
        })

    # ---------------------------- GET ---------------------------- #
    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        path = urllib.parse.unquote(parsed.path)

        if path == "/favicon.ico":
            self._serve_static("favicon.ico")
            return

        if path in ("/api/health", "/api/version"):
            self._json(self._health() if path == "/api/health" else self._version())
            return

        if path == "/" or path == "/index.html":
            if not self._global_token_ok(self._token(query)):
                self._deny()
                return
            self._serve_index()
            return

        if path.startswith("/static/"):
            self._serve_static(path[len("/static/"):].lstrip("/"))
            return

        token = self._token(query)

        if path == "/api/info":
            if not self._global_token_ok(token):
                self._json({"error": "unauthorized"}, 403)
                return
            self._json(self._info())
            return

        if path == "/api/spaces":
            if not self._any_token_ok(token):
                self._json({"error": "unauthorized"}, 403)
                return
            self._json(self._spaces_payload(token))
            return

        if path == "/api/files":
            if not self._list_allowed(token, query):
                return
            self._json({"files": self._list_files_for(token, query)})
            return

        if path == "/api/hash":
            self._handle_hash(token, query)
            return

        if path.startswith("/api/upload/session/"):
            self._upload_session_status(token, path)
            return

        if path == "/download":
            self._download(token, query)
            return

        self._send(404, "404 Not Found")

    # ---------------------------- POST ---------------------------- #
    def do_POST(self):
        parsed = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        path = urllib.parse.unquote(parsed.path)

        if path == "/api/token/rotate":
            self._token_rotate(query)
            return
        if path == "/api/upload/session":
            self._upload_session_create(query)
            return
        if path.startswith("/api/upload/session/") and path.endswith("/commit"):
            self._upload_session_commit(query, path)
            return
        if path != "/api/upload":
            self._send(404, "404 Not Found")
            return
        token = self._token(query)
        space_name = self._requested_space(query) or DEFAULT_SPACE_NAME
        space, err = self._space_access(token, space_name)
        if err:
            self.close_connection = True
            self._json({"error": err[1], "code": "space"}, err[0])
            return
        if space["mode"] == "ro":
            self.close_connection = True
            self._json({"error": "空间 %s 为只读（mode=ro），不允许上传" % space_name,
                        "code": "read_only"}, 403)
            return
        self._upload(query, space)

    # ---------------------------- PUT（分片上传） ---------------------------- #
    def do_PUT(self):
        parsed = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        path = urllib.parse.unquote(parsed.path)
        if path.startswith("/api/upload/session/") and not path.endswith("/commit"):
            self._upload_session_chunk(query, path)
            return
        self._send(404, "404 Not Found")

    # ---------------------------- DELETE ---------------------------- #
    def do_DELETE(self):
        parsed = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        path = urllib.parse.unquote(parsed.path)
        if path.startswith("/api/upload/session/"):
            self._upload_session_abort(query, path)
            return
        if path != "/api/files":
            self._send(404, "404 Not Found")
            return
        token = self._token(query)
        raw = query.get("name", [""])[0]
        name = safe_name(raw) if raw else ""
        if not name:
            self._json({"error": "缺少文件名"}, 400)
            return

        space_name = self._requested_space(query)
        if space_name:
            space, err = self._space_access(token, space_name)
            if err:
                self._json({"error": err[1]}, err[0])
                return
            if space["mode"] == "ro":
                self._json({"error": "空间 %s 为只读，不允许删除" % space_name}, 403)
                return
            candidates = [space]
        else:
            # 兼容旧前端：全局口令 + 跨空间查找
            if not self._global_token_ok(token):
                self._json({"error": "unauthorized"}, 403)
                return
            candidates = []
            default_space = SPACES[DEFAULT_SPACE_NAME]
            if default_space["mode"] == "rw":
                candidates.append(default_space)
            candidates += [
                sp for name_, sp in SPACES.items()
                if name_ != DEFAULT_SPACE_NAME and sp["mode"] == "rw"
            ]

        target = self._find_file(candidates, name)
        if target is None:
            self._json({"error": "文件不存在"}, 404)
            return
        path, space = target
        try:
            os.remove(path)
        except OSError as exc:
            self._json({"error": "删除失败: %s" % exc}, 500)
            return
        slog("delete", space=space["name"], name=name, ip=self.client_address[0])
        log("  已删除: %s (%s)" % (name, space["name"]))
        self._json({"ok": True, "space": space["name"]})

    # ---------------------------- 列表 ---------------------------- #
    def _list_allowed(self, token, query):
        space_name = self._requested_space(query)
        if space_name:
            _, err = self._space_access(token, space_name)
            if err:
                self._json({"error": err[1]}, err[0])
                return False
            return True
        if not self._global_token_ok(token):
            self._json({"error": "unauthorized（列出全部空间需要全局口令；访问单个空间请带 space 参数）"}, 403)
            return False
        return True

    def _list_files_for(self, token, query):
        space_name = self._requested_space(query)
        if space_name:
            return list_files(SPACES[space_name]["path"], space_name)
        return list_all_files()

    def _spaces_payload(self, token):
        result = []
        is_global = self._global_token_ok(token)
        for name, space in SPACES.items():
            if not is_global and not self._space_token_ok(token, name):
                continue
            files, size = count_space_files(space)
            result.append({
                "name": name,
                "mode": space["mode"],
                "description": space["description"],
                "path": space["path"],
                "files": files,
                "size": size,
                "has_own_token": bool(space.get("token")),
            })
        return {
            "default": DEFAULT_SPACE_NAME,
            "spaces": result,
            "version": APP_VERSION,
        }

    def _handle_hash(self, token, query):
        raw = query.get("name", [""])[0]
        name = safe_name(raw) if raw else ""
        space_name = self._requested_space(query)
        if not name:
            self._json({"error": "缺少文件名"}, 400)
            return

        if space_name:
            space, err = self._space_access(token, space_name)
            if err:
                self._json({"error": err[1]}, err[0])
                return
            candidates = [space]
        else:
            if not self._global_token_ok(token):
                self._json({"error": "unauthorized"}, 403)
                return
            candidates = list(SPACES.values())

        target = self._find_file(candidates, name)
        if target is None:
            self._json({"error": "文件不存在"}, 404)
            return
        path, space = target
        try:
            digest = sha256_file(path)
            size = os.path.getsize(path)
        except OSError as exc:
            self._json({"error": "读取失败: %s" % exc}, 500)
            return
        self._json({"name": name, "space": space["name"], "size": size, "sha256": digest})

    # ---------------------------- 查找文件 ---------------------------- #
    @staticmethod
    def _find_file(candidates, name):
        """在候选空间里查找文件，返回 (path, space) 或 None。"""
        for space in candidates:
            path = os.path.join(space["path"], name)
            try:
                real = os.path.realpath(path)
                if os.path.commonpath([real, os.path.realpath(space["path"])]) != \
                        os.path.realpath(space["path"]):
                    continue
                if os.path.isfile(real):
                    return real, space
            except (OSError, ValueError):
                continue
        return None

    # ---------------------------- 页面 / 静态文件 ---------------------------- #
    def _serve_index(self):
        index_path = os.path.join(STATIC_DIR, "index.html")
        try:
            with open(index_path, "rb") as fh:
                body = fh.read()
        except OSError:
            self._send(500, "缺少 static/index.html")
            return
        cookie = "k=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=86400" % TOKEN
        if USE_TLS:
            cookie += "; Secure"
        self._send(200, body, "text/html; charset=utf-8", {"Set-Cookie": cookie})

    def _serve_static(self, name):
        allowed = {"qrcode.js", "app.js", "i18n.js", "style.css",
                   "favicon.ico", "favicon.svg", "favicon-32.png",
                   "logo-96.png", "logo-192.png", "brand-48.png", "brand-256.png",
                   "i18n/zh-Hans.json", "i18n/zh-Hant.json", "i18n/en.json",
                   "i18n/ja.json", "i18n/ko.json", "i18n/es.json", "i18n/ru.json"}
        # 只接受白名单里的相对路径，避免目录穿越
        if name not in allowed:
            self._send(404, "404 Not Found")
            return
        path = os.path.join(STATIC_DIR, name)
        if not os.path.isfile(path):
            self._send(404, "404 Not Found")
            return
        overrides = {".svg": "image/svg+xml", ".ico": "image/x-icon",
                     ".png": "image/png", ".json": "application/json; charset=utf-8"}
        ext = os.path.splitext(name)[1].lower()
        ctype = overrides.get(ext) or mimetypes.guess_type(path)[0] or "application/octet-stream"
        with open(path, "rb") as fh:
            body = fh.read()
        self._send(200, body, ctype, {"Cache-Control": "max-age=86400"})

    # ---------------------------- 信息 ---------------------------- #
    def _health(self):
        return {
            "ok": True,
            "app": APP_NAME,
            "version": APP_VERSION,
            "api_version": API_VERSION,
            "listen": BIND_ADDR,
            "port": PORT,
            "port_requested": PORT_REQUESTED,
            "port_fallback": PORT_IS_FALLBACK,
            "scheme": SCHEME,
            "lan_only": BIND_LAN_ONLY,
            "spaces": len(SPACES),
            "default_space": DEFAULT_SPACE_NAME,
            "token_required": True,
            "discovery": {
                "enabled": DISCOVERY_ENABLED,
                "port": DISCOVERY_PORT,
                "interval": DISCOVERY_INTERVAL,
            },
            "capabilities": {
                "spaces": True,
                "per_space_token": True,
                "read_only_space": True,
                "sha256": True,
                "upload_audit": True,
                "http_range": True,
                "if_range": True,
                "headless": True,
                "resume": True,
                "upload_session": True,
                "upload_chunk_size": SESSION_CHUNK_SIZE,
                "discovery": True,
                "session_persist": True,
                "upload_limit": UPLOAD_LIMIT,
                "token_rotate": True,
            },
        }

    def _version(self):
        return {"app": APP_NAME, "version": APP_VERSION, "api_version": API_VERSION}

    def _info(self):
        ip = local_ip()
        default_space = SPACES.get(DEFAULT_SPACE_NAME) or next(iter(SPACES.values()))
        pub_files = list_files(PUBLISH_DIR)
        recv_files = list_files(RECEIVE_DIR)
        default_files = list_files(default_space["path"], default_space["name"])
        return {
            "host": HOST_NAME,
            "ip": ip,
            "port": PORT,
            "port_requested": PORT_REQUESTED,
            "https": USE_TLS,
            "fingerprint": cert_fingerprint() if USE_TLS else "",
            "share_url": build_url(ip),
            "local_url": build_url(),
            "storage": default_space["path"],
            "publish_dir": PUBLISH_DIR,
            "receive_dir": RECEIVE_DIR,
            "lan_only": BIND_LAN_ONLY,
            "port_changed": PORT_IS_FALLBACK,
            "version": APP_VERSION,
            "default_space": DEFAULT_SPACE_NAME,
            "discovery": {
                "enabled": DISCOVERY_ENABLED,
                "port": DISCOVERY_PORT,
                "interval": DISCOVERY_INTERVAL,
            },
            "spaces": [
                {"name": name, "mode": space["mode"], "path": space["path"],
                 "description": space["description"]}
                for name, space in SPACES.items()
            ],
            "publish_count": len(pub_files),
            "receive_count": len(recv_files),
            "publish_size": sum(item["size"] for item in pub_files),
            "receive_size": sum(item["size"] for item in recv_files),
            "default_count": len(default_files),
            "default_size": sum(item["size"] for item in default_files),
        }

    # ---------------------------- 上传 ---------------------------- #
    def _upload(self, query, space):
        raw_name = query.get("name", [""])[0]
        if not raw_name.strip():
            self.close_connection = True
            self._json({"error": "缺少文件名"}, 400)
            return
        name = safe_name(raw_name)
        if not name:
            self.close_connection = True
            self._json({"error": "文件名不合法"}, 400)
            return

        length_header = self.headers.get("Content-Length")
        if length_header is None:
            self.close_connection = True
            self._json({"error": "缺少 Content-Length"}, 411)
            return
        try:
            length = int(length_header)
        except ValueError:
            length = -1
        if length < 0:
            self.close_connection = True
            self._json({"error": "Content-Length 不合法"}, 400)
            return

        if not UPLOAD_SLOTS.acquire(blocking=False):
            self.close_connection = True
            self._json({"error": "当前上传任务过多，请稍后重试（服务端并发上限 %d）" % UPLOAD_LIMIT,
                        "code": "busy"}, 429)
            return

        dest_name = unique_name(name, space["path"])
        dest_path = os.path.join(space["path"], dest_name)
        tmp = None
        digest = hashlib.sha256()
        received = 0
        try:
            fd, tmp = tempfile.mkstemp(prefix=".upload-", suffix=".part", dir=space["path"])
            with os.fdopen(fd, "wb") as out:
                remaining = length
                while remaining > 0:
                    chunk = self.rfile.read(min(BUF_SIZE, remaining))
                    if not chunk:
                        raise ConnectionError("连接中断")
                    out.write(chunk)
                    digest.update(chunk)
                    received += len(chunk)
                    remaining -= len(chunk)
            os.replace(tmp, dest_path)
            tmp = None
        except (OSError, ConnectionError) as exc:
            self.close_connection = True
            audit_upload(space["name"], dest_name, received, self.client_address[0],
                         False, error=str(exc))
            slog("upload", level="error", space=space["name"], name=dest_name,
                 bytes=received, ip=self.client_address[0], ok=False, error=str(exc))
            self._json({"error": "上传失败: %s" % exc}, 500)
            return
        finally:
            if tmp and os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            UPLOAD_SLOTS.release()

        checksum = digest.hexdigest()
        audit_upload(space["name"], dest_name, length, self.client_address[0], True,
                     sha256=checksum)
        slog("upload", space=space["name"], name=dest_name, bytes=length,
             ip=self.client_address[0], ok=True, sha256=checksum)
        log("  收到 %s -> %s (%s)" % (dest_name, space["name"], human_size(length)))
        self._json({"ok": True, "name": dest_name, "size": length,
                    "space": space["name"], "sha256": checksum})

    # ---------------------------- 分片上传会话 ---------------------------- #
    @staticmethod
    def _session_id(path, suffix=""):
        pattern = r"^/api/upload/session/([A-Za-z0-9_-]{1,64})%s$" % suffix
        match = re.match(pattern, path)
        return match.group(1) if match else ""

    def _session_error(self, token, query, session_id, need_write=False):
        """返回 (session, space) 或 (None, None)，并已发送错误响应。"""
        session = UPLOAD_SESSIONS.get(session_id)
        if not session:
            self._json({"error": "上传会话不存在或已过期，请重新开始", "code": "session"},
                       404)
            return None, None
        space, err = self._space_access(token, session["space"])
        if err:
            self._json({"error": err[1]}, err[0])
            return None, None
        if need_write and space["mode"] == "ro":
            self._json({"error": "空间 %s 为只读（mode=ro），不允许上传" % space["name"],
                        "code": "read_only"}, 403)
            return None, None
        return session, space

    def _upload_session_create(self, query):
        token = self._token(query)
        raw = query.get("name", [""])[0]
        name = safe_name(raw) if raw.strip() else ""
        if not name:
            self._json({"error": "缺少文件名"}, 400)
            return
        space_name = self._requested_space(query) or DEFAULT_SPACE_NAME
        space, err = self._space_access(token, space_name)
        if err:
            self._json({"error": err[1], "code": "space"}, err[0])
            return
        if space["mode"] == "ro":
            self._json({"error": "空间 %s 为只读（mode=ro），不允许上传" % space_name,
                        "code": "read_only"}, 403)
            return
        try:
            total = int(query.get("size", ["0"])[0])
        except ValueError:
            total = -1
        if total < 0:
            self._json({"error": "size 不合法"}, 400)
            return
        expect_sha = query.get("sha256", [""])[0].strip().lower()

        session_id = secrets.token_hex(9)
        tmp = session_temp_path(space_name, session_id)
        try:
            with open(tmp, "wb"):
                pass
        except OSError as exc:
            self._json({"error": "无法创建上传临时文件: %s" % exc}, 500)
            return
        now = time.time()
        session = {
            "id": session_id,
            "name": name,
            "space": space_name,
            "size": total,
            "offset": 0,
            "sha256": expect_sha,
            "created": now,
            "updated": now,
            "chunks": 0,
            "resumed": False,
            "ip": self.client_address[0],
            "lock": threading.Lock(),
        }
        with UPLOAD_SESSIONS_LOCK:
            UPLOAD_SESSIONS[session_id] = session
        save_upload_sessions()
        slog("upload_session_create", session=session_id, space=space_name,
             name=name, bytes=total, ip=self.client_address[0])
        self._json({
            "ok": True,
            "upload_id": session_id,
            "offset": 0,
            "size": total,
            "chunk_size": SESSION_CHUNK_SIZE,
            "space": space_name,
            "name": name,
            "expires_in": SESSION_IDLE_TIMEOUT,
        })

    def _upload_session_status(self, token, path):
        session_id = self._session_id(path)
        if not session_id:
            self._json({"error": "会话 id 不合法"}, 400)
            return
        session, _ = self._session_error(token, {}, session_id)
        if not session:
            return
        self._json({
            "ok": True,
            "upload_id": session_id,
            "offset": session["offset"],
            "size": session["size"],
            "chunk_size": SESSION_CHUNK_SIZE,
            "space": session["space"],
            "name": session["name"],
            "chunks": session["chunks"],
            "resumed": session["resumed"],
            "expires_in": max(0, int(SESSION_IDLE_TIMEOUT - (time.time() - session["updated"]))),
        })

    def _upload_session_chunk(self, query, path):
        token = self._token(query)
        session_id = self._session_id(path)
        if not session_id:
            self._json({"error": "会话 id 不合法"}, 400)
            return
        session, space = self._session_error(token, query, session_id, need_write=True)
        if not session:
            return
        tmp = session_temp_path(session["space"], session_id)
        if not os.path.isfile(tmp):
            with UPLOAD_SESSIONS_LOCK:
                UPLOAD_SESSIONS.pop(session_id, None)
            save_upload_sessions()
            self._json({"error": "上传临时文件已丢失，请重新开始", "code": "session"}, 404)
            return

        raw_offset = query.get("offset", [""])[0].strip()
        try:
            offset = int(raw_offset) if raw_offset else session["offset"]
        except ValueError:
            self._json({"error": "offset 不合法"}, 400)
            return
        if offset != session["offset"]:
            self.close_connection = True
            self._json({"error": "offset 不匹配", "offset": session["offset"],
                        "size": session["size"], "code": "offset"}, 409)
            return
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            length = -1
        if length < 0:
            self.close_connection = True
            self._json({"error": "缺少 Content-Length"}, 411)
            return
        if length == 0:
            self._json({"error": "分片为空"}, 400)
            return
        if session["size"] and offset + length > session["size"]:
            self.close_connection = True
            self._json({"error": "分片超出文件总大小", "offset": offset,
                        "size": session["size"]}, 400)
            return

        if not UPLOAD_SLOTS.acquire(blocking=False):
            self.close_connection = True
            self._json({"error": "当前上传任务过多，请稍后重试（服务端并发上限 %d）" % UPLOAD_LIMIT,
                        "code": "busy"}, 429)
            return

        written = 0
        try:
            with session["lock"]:
                # 上一次分片可能只写了一半（客户端被杀/断网），先截断到确认偏移
                try:
                    current_size = os.path.getsize(tmp)
                except OSError:
                    current_size = offset
                if current_size != offset:
                    truncate_session_temp(tmp, offset)
                with open(tmp, "ab") as out:
                    remaining = length
                    while remaining > 0:
                        block = self.rfile.read(min(BUF_SIZE, remaining))
                        if not block:
                            raise ConnectionError("连接中断")
                        out.write(block)
                        written += len(block)
                        remaining -= len(block)
                session["offset"] = offset + written
                session["updated"] = time.time()
                session["chunks"] += 1
                if query.get("resumed", [""])[0] == "1":
                    session["resumed"] = True
        except (OSError, ConnectionError) as exc:
            # 丢弃本片已写入的半截数据，保证后续续传从 offset 继续
            truncate_session_temp(tmp, offset)
            self.close_connection = True
            slog("upload_session_chunk", level="error", session=session_id,
                 space=session["space"], offset=offset, bytes=written, error=str(exc))
            self._json({"error": "分片接收失败: %s" % exc, "offset": session["offset"]}, 500)
            return
        finally:
            UPLOAD_SLOTS.release()
        if session["size"] and session["offset"] > session["size"]:
            session["offset"] = session["size"]

        save_upload_sessions()

        slog("upload_session_chunk", session=session_id, space=session["space"],
             offset=offset, bytes=written, total=session["offset"], size=session["size"])
        self._json({
            "ok": True,
            "upload_id": session_id,
            "offset": session["offset"],
            "size": session["size"],
            "received": written,
        })

    def _upload_session_commit(self, query, path):
        token = self._token(query)
        session_id = self._session_id(path, "/commit")
        if not session_id:
            self._json({"error": "会话 id 不合法"}, 400)
            return
        session, space = self._session_error(token, query, session_id, need_write=True)
        if not session:
            return
        tmp = session_temp_path(session["space"], session_id)
        if not os.path.isfile(tmp):
            with UPLOAD_SESSIONS_LOCK:
                UPLOAD_SESSIONS.pop(session_id, None)
            save_upload_sessions()
            self._json({"error": "上传临时文件已丢失，请重新开始", "code": "session"}, 404)
            return
        if session["size"] and session["offset"] != session["size"]:
            self._json({"error": "上传未完成", "offset": session["offset"],
                        "size": session["size"], "code": "incomplete"}, 409)
            return

        expect_sha = session["sha256"] or query.get("sha256", [""])[0].strip().lower()
        try:
            checksum = sha256_file(tmp)
        except OSError as exc:
            self._json({"error": "校验失败: %s" % exc}, 500)
            return
        if expect_sha and checksum != expect_sha:
            with UPLOAD_SESSIONS_LOCK:
                UPLOAD_SESSIONS.pop(session_id, None)
            save_upload_sessions()
            try:
                os.remove(tmp)
            except OSError:
                pass
            audit_upload(session["space"], session["name"], session["offset"],
                         self.client_address[0], False, sha256=checksum,
                         error="sha256 不一致", resumed=session["resumed"],
                         chunked=True, session=session_id)
            slog("upload_session_commit", level="error", session=session_id,
                 space=session["space"], name=session["name"], error="sha256 不一致")
            self._json({"error": "sha256 校验失败", "sha256": checksum,
                        "expected": expect_sha}, 422)
            return

        dest_name = unique_name(session["name"], space["path"])
        dest_path = os.path.join(space["path"], dest_name)
        try:
            os.replace(tmp, dest_path)
        except OSError as exc:
            self._json({"error": "保存文件失败: %s" % exc}, 500)
            return
        with UPLOAD_SESSIONS_LOCK:
            UPLOAD_SESSIONS.pop(session_id, None)
        save_upload_sessions()

        size = session["offset"]
        audit_upload(space["name"], dest_name, size, self.client_address[0], True,
                     sha256=checksum, resumed=session["resumed"], chunked=True,
                     session=session_id)
        slog("upload_session_commit", session=session_id, space=space["name"],
             name=dest_name, bytes=size, chunks=session["chunks"],
             resumed=session["resumed"], sha256=checksum)
        log("  分片上传完成 %s -> %s (%s, %d 片)"
            % (dest_name, space["name"], human_size(size), session["chunks"]))
        self._json({"ok": True, "name": dest_name, "size": size, "space": space["name"],
                    "sha256": checksum, "chunks": session["chunks"],
                    "resumed": session["resumed"], "upload_id": session_id})

    def _upload_session_abort(self, query, path):
        token = self._token(query)
        session_id = self._session_id(path)
        if not session_id:
            self._json({"error": "会话 id 不合法"}, 400)
            return
        session, _ = self._session_error(token, query, session_id)
        if not session:
            return
        with UPLOAD_SESSIONS_LOCK:
            UPLOAD_SESSIONS.pop(session_id, None)
        save_upload_sessions()
        try:
            os.remove(session_temp_path(session["space"], session_id))
        except OSError:
            pass
        slog("upload_session_abort", session=session_id, space=session["space"],
             name=session["name"], bytes=session["offset"])
        self._json({"ok": True, "upload_id": session_id})

    # ---------------------------- 下载 ---------------------------- #
    def _download(self, token, query):
        raw = query.get("name", [""])[0]
        name = safe_name(raw) if raw else ""
        if not name:
            self._json({"error": "缺少文件名"}, 400)
            return

        space_name = self._requested_space(query)
        if space_name:
            space, err = self._space_access(token, space_name)
            if err:
                self._json({"error": err[1]}, err[0])
                return
            candidates = [space]
        else:
            if not self._global_token_ok(token):
                self._json({"error": "unauthorized"}, 403)
                return
            candidates = list(SPACES.values())

        target = self._find_file(candidates, name)
        if target is None:
            self._json({"error": "文件不存在"}, 404)
            return
        path, space = target

        size = os.path.getsize(path)
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        if name.lower().endswith((".md", ".txt", ".csv", ".json")):
            ctype = "text/plain; charset=utf-8"

        inline = query.get("inline", [""])[0] in ("1", "true")
        quoted = urllib.parse.quote(name)
        disposition = "%s; filename=\"%s\"; filename*=UTF-8''%s" % (
            "inline" if inline else "attachment", quoted, quoted)

        stat = os.stat(path)
        etag = '"%d-%d"' % (size, int(stat.st_mtime))
        last_modified = email.utils.formatdate(stat.st_mtime, usegmt=True)

        start, end = 0, size - 1
        status = 200
        range_header = self.headers.get("Range", "")
        if_range = self.headers.get("If-Range", "").strip()
        if range_header.startswith("bytes=") and if_range:
            # If-Range 不匹配（文件已变化）时按完整文件返回 200
            same_etag = if_range.strip('"') == etag.strip('"')
            if not same_etag and if_range != last_modified:
                range_header = ""
        if range_header.startswith("bytes="):
            try:
                spec = range_header[6:].split(",")[0].strip()
                first, _, last = spec.partition("-")
                if first:
                    start = int(first)
                    end = int(last) if last else size - 1
                elif last:
                    start = max(0, size - int(last))
                if start >= size or end < start:
                    raise ValueError
                end = min(end, size - 1)
                status = 206
            except ValueError:
                self._send(416, "range not satisfiable",
                           extra={"Content-Range": "bytes */%d" % size,
                                  "Accept-Ranges": "bytes", "ETag": etag})
                return

        length = 0 if size == 0 else end - start + 1

        extra = {
            "Accept-Ranges": "bytes",
            "X-Space": space["name"],
            "X-File-Size": str(size),
            "ETag": etag,
            "Last-Modified": last_modified,
        }
        if query.get("checksum", [""])[0] in ("1", "true"):
            try:
                extra["X-File-SHA256"] = sha256_file(path)
            except OSError:
                pass

        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Disposition", disposition)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        if status == 206:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        for key, value in extra.items():
            self.send_header(key, value)
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

    def do_HEAD(self):
        self.do_GET()


# --------------------------------------------------------------------------- #
# 启动
# --------------------------------------------------------------------------- #
def banner(url: str, ip_url: str) -> None:
    line = "=" * 58
    log(line)
    log("  局域网互传 已启动 v%s%s" % (APP_VERSION, "（HTTPS 加密）" if USE_TLS else "（HTTP 明文）"))
    log("")
    if HEADLESS:
        log("  服务地址:  %s://%s:%d/（口令见 token 文件）" % (SCHEME, access_host(), PORT))
        if TOKEN_FILE:
            log("  口令文件:  %s" % TOKEN_FILE)
        log("  配置文件:  %s" % SPACES_FILE)
        log("  日志目录:  %s" % (LOG_FILE or LOG_DIR))
    else:
        log("  电脑本机:  %s" % url)
        log("  手机/其他设备: %s" % ip_url)
    log("")
    if USE_TLS and not HEADLESS:
        log("  证书指纹:  %s" % cert_fingerprint())
        log("")
    log("  空间(%d):" % len(SPACES))
    for name, space in SPACES.items():
        log("    %-10s %-2s %s" % (name, space["mode"], space["path"]))
    log("")
    log("  局域网发现: %s（UDP %d）" % (
        "已广播，可用 CLI discover 查找" if DISCOVERY_ENABLED else "已关闭",
        DISCOVERY_PORT))
    log("")
    log("  提示: 手机与电脑需连接同一个 Wi-Fi；首次运行若弹出")
    log("        Windows 防火墙提示，请选择 \"允许访问\"。")
    log("  按 Ctrl+C 停止服务")
    log(line)
    log("")


def run(port: int, open_browser: bool, bind: str, spaces_file: str = None) -> ThreadingHTTPServer:
    """启动服务并返回 server 对象。

    bind: "lan"/"auto"/"" 只绑局域网 IP；具体 IP；"0.0.0.0" 全部网卡。
    端口被占用时在 port ~ port+11 自动顺延，并置 PORT_IS_FALLBACK。
    """
    global PORT, PORT_REQUESTED, PORT_IS_FALLBACK, BIND_ADDR, BIND_LAN_ONLY
    global USE_TLS, SCHEME, SPACES_FILE

    migrate_legacy_dirs()
    if SPACES.get(DEFAULT_SPACE_NAME) is None or len(SPACES) == 0:
        load_spaces(spaces_file or SPACES_FILE)
    os.makedirs(RECEIVE_DIR, exist_ok=True)
    os.makedirs(PUBLISH_DIR, exist_ok=True)

    PORT_REQUESTED = port
    BIND_LAN_ONLY = False
    lan_ip = local_ip()
    if bind in ("lan", "auto", ""):
        if lan_ip and lan_ip != "127.0.0.1" and is_lan_ip(lan_ip):
            bind = lan_ip
            BIND_LAN_ONLY = True
        else:
            bind = "0.0.0.0"

    def _open_server(host):
        for candidate in range(port, port + 12):
            try:
                srv = ThreadingHTTPServer((host, candidate), Handler)
                return srv, candidate
            except OSError:
                continue
        return None, None

    server, chosen = _open_server(bind)
    if server is None and bind != "0.0.0.0":
        BIND_LAN_ONLY = False
        bind = "0.0.0.0"
        server, chosen = _open_server(bind)
    if server is None:
        raise RuntimeError("端口 %d-%d 均被占用，请关闭占用程序或换一个端口。"
                           % (port, port + 11))

    BIND_ADDR = bind
    PORT = chosen
    PORT_IS_FALLBACK = chosen != port

    tls = ensure_certificate()
    if tls and not NO_TLS:
        import ssl
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(tls[0], tls[1])
        server.socket = ctx.wrap_socket(server.socket, server_side=True)
        USE_TLS = True
        SCHEME = "https"
    else:
        if NO_TLS:
            log("  已指定 --no-tls：以 HTTP 明文模式运行（仅建议本机调试使用）。")
        else:
            log("  [警告] 未安装 cryptography 库，以 HTTP 明文模式运行。")
        USE_TLS = False
        SCHEME = "http"

    server.daemon_threads = True
    server.serve_forever_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server.serve_forever_thread.start()

    load_upload_sessions()
    start_session_cleanup()
    if DISCOVERY_ENABLED:
        start_beacon()
    else:
        stop_beacon()

    ip = local_ip()
    web_url = build_url()
    share_url = build_url(ip)

    if PORT_IS_FALLBACK:
        log("  [提示] 默认端口 %d 被占用，已自动改用 %d。" % (port, PORT))
    banner(web_url, share_url)

    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(web_url)).start()

    slog("server_start", bind=BIND_ADDR, port=PORT, port_requested=PORT_REQUESTED,
         fallback=PORT_IS_FALLBACK, spaces=list(SPACES.keys()),
         default_space=DEFAULT_SPACE_NAME, scheme=SCHEME)
    return server


# --------------------------------------------------------------------------- #
# 无界面 Headless
# --------------------------------------------------------------------------- #
def read_or_create_token(path):
    if path:
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    token = fh.read().strip()
                if token:
                    return token
            except OSError:
                pass
        token = "".join(secrets.choice(TOKEN_CHARS) for _ in range(10))
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(token + "\n")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return token
    return "".join(secrets.choice(TOKEN_CHARS) for _ in range(10))


def write_pid(path, pid):
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(str(pid))
    except OSError:
        pass


def remove_pid(path, pid):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            current = fh.read().strip()
        if current == str(pid):
            os.remove(path)
    except OSError:
        pass


def _ensure_usable_streams():
    """PyInstaller 的窗口版（console=False）里 sys.stdout/stderr 是 None，
    任何未被捕获的写入都会连带报错。无界面模式下补一个空实现，保证服务稳定运行。"""
    class _NullStream:
        def write(self, *args, **kwargs):
            return 0

        def flush(self):
            pass

        def isatty(self):
            return False

        encoding = "utf-8"

    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            setattr(sys, name, _NullStream())


def headless_main(argv=None) -> int:
    """无界面常驻模式。返回进程退出码。"""
    global TOKEN, TOKEN_FILE, LOG_FILE, HEADLESS, SPACES_FILE

    _ensure_usable_streams()

    parser = argparse.ArgumentParser(prog="lan_transfer.py --headless",
                                     description="局域网互传 - 无界面服务")
    parser.add_argument("--headless", action="store_true", help="无界面常驻")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--bind", default="lan", help="监听地址，默认仅局域网 IP")
    parser.add_argument("--token-file", default="",
                        help="口令文件：存在则读取，不存在则生成并写入")
    parser.add_argument("--spaces-file", default="", help="空间配置（yaml/json）")
    parser.add_argument("--log-file", default="", help="结构化日志文件（默认按天切分）")
    parser.add_argument("--data-dir", default="",
                        help="数据目录（文件、证书、配置、PID、日志）")
    parser.add_argument("--no-discovery", action="store_true",
                        help="关闭局域网 UDP 广播发现")
    parser.add_argument("--no-tls", action="store_true",
                        help="以 HTTP 明文运行（仅本机调试；默认强制 HTTPS）")
    args = parser.parse_args(argv)

    global DISCOVERY_ENABLED, NO_TLS
    HEADLESS = True
    if args.no_discovery:
        DISCOVERY_ENABLED = False
    if args.no_tls:
        NO_TLS = True
    configure_data_dir(args.data_dir)
    if args.spaces_file:
        SPACES_FILE = os.path.abspath(os.path.expanduser(args.spaces_file))
    if args.log_file:
        LOG_FILE = os.path.abspath(os.path.expanduser(args.log_file))
    TOKEN_FILE = os.path.abspath(os.path.expanduser(args.token_file)) if args.token_file else ""

    try:
        TOKEN = read_or_create_token(TOKEN_FILE)
        apply_persisted_token()
        load_spaces(SPACES_FILE)
        server = run(args.port, False, args.bind, spaces_file=SPACES_FILE)
    except KeyboardInterrupt:
        log("服务已停止。")
        return 0
    except Exception as exc:
        slog("startup_error", level="error", error=str(exc))
        log("[错误] 启动失败: %s" % exc)
        return 2

    pid = os.getpid()
    write_pid(PID_FILE, pid)
    slog("pid_written", pid=pid, path=PID_FILE)

    stop = threading.Event()

    def _stop_handler(signum, _frame):
        slog("shutdown_signal", signal=signum)
        stop.set()

    for sig_name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        sig = getattr(signal, sig_name, None)
        if sig is not None:
            try:
                signal.signal(sig, _stop_handler)
            except (OSError, ValueError):
                pass

    try:
        while not stop.wait(1.0):
            pass
    except KeyboardInterrupt:
        pass

    try:
        server.shutdown()
        server.server_close()
    except Exception:
        pass
    stop_beacon()
    cleanup_upload_sessions(force_all=True)
    remove_pid(PID_FILE, pid)
    slog("server_stop", pid=pid)
    log("服务已停止。")
    return 0


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    if "--headless" in sys.argv[1:]:
        sys.exit(headless_main(sys.argv[1:]))

    parser = argparse.ArgumentParser(description="局域网文件互传")
    parser.add_argument("--port", type=int, default=8765, help="服务端口，默认 8765")
    parser.add_argument("--bind", default="lan",
                        help="监听地址，默认 lan（仅本机局域网 IP）；也可填具体 IP")
    parser.add_argument("--open", action="store_true", help="启动后自动打开电脑端页面")
    parser.add_argument("--token-file", default="", help="从文件读取固定口令")
    parser.add_argument("--spaces-file", default="", help="空间配置（yaml/json）")
    parser.add_argument("--data-dir", default="", help="数据目录")
    parser.add_argument("--no-discovery", action="store_true",
                        help="关闭局域网 UDP 广播发现")
    parser.add_argument("--no-tls", action="store_true",
                        help="以 HTTP 明文运行（仅本机调试；默认强制 HTTPS）")
    args = parser.parse_args()

    global TOKEN, TOKEN_FILE, LOG_FILE, SPACES_FILE, DISCOVERY_ENABLED, NO_TLS
    if args.no_discovery:
        DISCOVERY_ENABLED = False
    if args.no_tls:
        NO_TLS = True
    configure_data_dir(args.data_dir)
    if args.spaces_file:
        SPACES_FILE = os.path.abspath(os.path.expanduser(args.spaces_file))
    TOKEN_FILE = os.path.abspath(os.path.expanduser(args.token_file)) if args.token_file else ""
    TOKEN = read_or_create_token(TOKEN_FILE) if TOKEN_FILE else \
        "".join(secrets.choice(TOKEN_CHARS) for _ in range(10))
    apply_persisted_token()

    server = run(args.port, args.open, args.bind, spaces_file=SPACES_FILE)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log("")
        log("服务已停止。")
        stop_beacon()
        try:
            server.shutdown()
            server.server_close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
