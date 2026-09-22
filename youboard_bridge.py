#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""YouBoard 浏览器扩展桥（只监听 127.0.0.1，供 Edge / Chrome 扩展调用）。

设计要点：
- **只绑定 127.0.0.1**：外网、局域网都连不上，也不做端口转发
- **令牌鉴权**：每次启用生成一串随机令牌，写在配置里；
  扩展那边（本机）带着同一个令牌才允许读写
- 只做三件事：查历史 / 存一条（文本、网址、图片）/ 把某条复制到系统剪贴板
- 不依赖 PyQt：跑在后台线程里，界面线程不参与
"""

import base64
import binascii
import hmac
import io
import json
import os
import secrets
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from youboard_core import (ClipboardStore, entry_full_text, entry_tags,
                           set_clipboard_files, set_clipboard_image,
                           set_clipboard_text)
from youboard_version import APP_NAME, APP_VERSION

BRIDGE_HOST = "127.0.0.1"
DEFAULT_BRIDGE_PORT = 8765
# 单次请求体上限（图片以 data URL 送过来，8MB 图片 base64 后约 11MB）
MAX_BODY_BYTES = 12 * 1024 * 1024
MAX_TEXT_CHARS = 200000
# 历史里最多回传多少张缩略图（避免一次响应过大）
MAX_THUMBS = 12


def ensure_bridge_config(cfg):
    """保证配置里有桥接需要的端口 / 令牌，返回 (enabled, port, token)。"""
    cfg = cfg if isinstance(cfg, dict) else {}
    enabled = bool(cfg.get("bridge_enabled", False))
    try:
        port = int(cfg.get("bridge_port") or DEFAULT_BRIDGE_PORT)
    except (TypeError, ValueError):
        port = DEFAULT_BRIDGE_PORT
    token = str(cfg.get("bridge_token") or "").strip()
    if not token:
        token = secrets.token_urlsafe(18)
    return enabled, port, token


def conn_line(port, token):
    """给用户复制到扩展选项页的那一行连接信息。"""
    return "%s:%s|%s" % (BRIDGE_HOST, int(port), token)


class _BridgeHTTPServer(ThreadingHTTPServer):
    """关掉 SO_REUSEADDR：Windows 上它会让两个进程绑同一个端口都"成功"，
    那样端口被占时我们就检测不到、也换不了端口。"""

    allow_reuse_address = False
    daemon_threads = True


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "YouBoardBridge/" + APP_VERSION

    # ---- 基础 ----
    def log_message(self, *args):
        """默认会把每条请求打到 stderr；打包后没有控制台，这里静默。"""

    @property
    def store(self):
        return self.server.store          # type: ignore[attr-defined]

    @property
    def token(self):
        return self.server.token          # type: ignore[attr-defined]

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods",
                         "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers",
                         "Content-Type, X-YouBoard-Token")
        self.send_header("Access-Control-Max-Age", "600")

    def _send(self, code, payload):
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _auth_ok(self, query):
        want = self.token or ""
        if not want:
            return False
        got = self.headers.get("X-YouBoard-Token") or ""
        if not got:
            got = (query.get("token") or [""])[0]
        return hmac.compare_digest(want, got or "")

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            length = 0
        if length <= 0:
            return {}
        if length > MAX_BODY_BYTES:
            raise ValueError("too_large")
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ValueError("bad_json")
        return data if isinstance(data, dict) else {}

    # ---- 路由 ----
    def do_OPTIONS(self):                # noqa: N802
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):                    # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        path = parsed.path.rstrip("/")
        if path in ("", "/api/ping"):
            # ping 也要令牌，避免本机其它程序探测
            if not self._auth_ok(query):
                return self._send(401, {"ok": False, "error": "unauthorized"})
            self.server.last_seen = time.time()      # type: ignore[attr-defined]
            return self._send(200, {
                "ok": True, "app": APP_NAME, "version": APP_VERSION,
                "count": self.store.count(),
            })
        if path == "/api/history":
            if not self._auth_ok(query):
                return self._send(401, {"ok": False, "error": "unauthorized"})
            self.server.last_seen = time.time()      # type: ignore[attr-defined]
            try:
                limit = int((query.get("limit") or ["50"])[0])
            except (TypeError, ValueError):
                limit = 50
            limit = max(1, min(500, limit))
            keyword = (query.get("q") or [""])[0].strip()
            etype = (query.get("type") or [""])[0].strip() or None
            return self._send(200, {"ok": True,
                                    "items": self._history(limit, keyword,
                                                           etype)})
        return self._send(404, {"ok": False, "error": "not_found"})

    def do_POST(self):                   # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        path = parsed.path.rstrip("/")
        if not self._auth_ok(query):
            return self._send(401, {"ok": False, "error": "unauthorized"})
        try:
            body = self._read_json()
        except ValueError as err:
            return self._send(400, {"ok": False, "error": str(err)})
        self.server.last_seen = time.time()          # type: ignore[attr-defined]

        if path == "/api/items":
            return self._add_item(body)
        if path == "/api/copy":
            return self._copy_item(body)
        if path == "/api/fav":
            return self._fav_item(body)
        return self._send(404, {"ok": False, "error": "not_found"})

    # ---- 业务 ----
    def _history(self, limit, keyword, etype):
        store = self.store
        if keyword:
            entries = store.search(keyword, etype)
        elif etype in ("text", "image", "file", "url"):
            entries = store.get_by_type(etype)
        else:
            entries = store.get_all()
        entries = sorted(entries,
                         key=lambda e: e.get("timestamp", "") or "",
                         reverse=True)[:limit]
        thumbs = 0
        out = []
        for e in entries:
            etype_ = e.get("type") or "text"
            item = {
                "hash": e.get("hash", ""),
                "type": etype_,
                "ts": e.get("timestamp", ""),
                "fav": bool(e.get("fav")),
                "tags": entry_tags(e),
            }
            if etype_ in ("text", "url"):
                item["content"] = (e.get("content") or "")[:2000]
            elif etype_ == "file":
                paths = e.get("file_paths") or []
                item["content"] = "\n".join(os.path.basename(p) for p in paths)
                item["file_count"] = e.get("file_count", len(paths))
            else:
                item["content"] = e.get("source_name") or "图片"
                item["width"] = e.get("width")
                item["height"] = e.get("height")
                if thumbs < MAX_THUMBS:
                    data = self._thumb_data_url(e)
                    if data:
                        item["thumb"] = data
                        thumbs += 1
            out.append(item)
        return out

    def _thumb_data_url(self, entry):
        try:
            from youboard_core import IMAGES_DIR
            name = os.path.basename(entry.get("filename", ""))
            if not name:
                return ""
            path = os.path.join(IMAGES_DIR, "thumb_" + name)
            if not os.path.exists(path):
                path = os.path.join(IMAGES_DIR, name)
            if not os.path.exists(path) or os.path.getsize(path) > 400 * 1024:
                return ""
            with open(path, "rb") as f:
                return "data:image/png;base64," + base64.b64encode(
                    f.read()).decode("ascii")
        except Exception:
            return ""

    def _add_item(self, body):
        etype = str(body.get("type") or "text").lower()
        content = body.get("content") or ""
        source = str(body.get("source") or "")
        title = str(body.get("title") or "")
        store = self.store
        try:
            if etype == "image":
                return self._add_image(content, title, source)
            text = str(content)
            if len(text) > MAX_TEXT_CHARS:
                text = text[:MAX_TEXT_CHARS]
            if not text.strip():
                return self._send(400, {"ok": False, "error": "empty"})
            if etype == "url" or (text.strip().startswith("http") and
                                  " " not in text.strip()):
                store.add_url(text.strip())
                h = store._text_hash(text.strip())
            else:
                if title and source:
                    text = text          # 原样保存，标题只作为来源信息
                store.add_text(text)
                h = store._text_hash(text.strip())
            return self._send(200, {"ok": True, "hash": h, "type": "text"})
        except Exception as err:                       # noqa: BLE001
            return self._send(500, {"ok": False, "error": str(err)[:200]})

    def _add_image(self, data_url, name, source):
        try:
            raw = decode_data_url(data_url)
        except (ValueError, binascii.Error):
            return self._send(400, {"ok": False, "error": "bad_image"})
        if not raw:
            return self._send(400, {"ok": False, "error": "empty"})
        if len(raw) > 8 * 1024 * 1024:
            return self._send(400, {"ok": False, "error": "image_too_large"})
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(raw))
            img.load()
        except Exception:
            return self._send(400, {"ok": False, "error": "not_an_image"})
        store = self.store
        store.add_image(img, store._image_hash(img),
                        source_name=(name or "浏览器图片")[:80])
        return self._send(200, {"ok": True, "hash": store._image_hash(img),
                                "type": "image"})

    def _find_entry(self, entry_hash, content):
        store = self.store
        for e in store.get_all():
            if entry_hash and e.get("hash") == entry_hash:
                return e
            if content and (e.get("content") or "") == content:
                return e
        return None

    def _copy_item(self, body):
        entry = self._find_entry(str(body.get("hash") or ""),
                                 str(body.get("content") or ""))
        if entry is None:
            return self._send(404, {"ok": False, "error": "not_found"})
        etype = entry.get("type", "text")
        try:
            store = self.store
            store.mark_self_copy()      # 别让剪贴板监控把它再记一条
            if etype in ("text", "url"):
                set_clipboard_text(entry_full_text(entry))
            elif etype == "image":
                from PIL import Image
                from youboard_core import IMAGES_DIR
                path = os.path.join(IMAGES_DIR,
                                    os.path.basename(entry.get("filename", "")))
                if not os.path.exists(path):
                    return self._send(404, {"ok": False, "error": "image_missing"})
                img = Image.open(path)
                img.load()
                set_clipboard_image(img)
            else:
                paths = [p for p in (entry.get("file_paths") or [])
                         if os.path.exists(p)]
                if not paths:
                    return self._send(404, {"ok": False, "error": "file_missing"})
                set_clipboard_files(paths)
        except Exception as err:                       # noqa: BLE001
            return self._send(500, {"ok": False, "error": str(err)[:200]})
        cb = getattr(self.server, "on_copy", None)     # type: ignore[attr-defined]
        if callable(cb):
            try:
                cb(entry)
            except Exception:
                pass
        return self._send(200, {"ok": True, "type": etype})

    def _fav_item(self, body):
        entry_hash = str(body.get("hash") or "")
        if not entry_hash:
            return self._send(400, {"ok": False, "error": "no_hash"})
        flag = bool(body.get("fav"))
        ok = self.store.set_fav(entry_hash, flag)
        return self._send(200 if ok else 404,
                          {"ok": ok, "fav": flag} if ok else
                          {"ok": False, "error": "not_found"})


def decode_data_url(data_url):
    """把 data:image/png;base64,xxx 解成原始字节。"""
    text = str(data_url or "")
    if not text.startswith("data:"):
        raise ValueError("not_data_url")
    head, _, payload = text.partition(",")
    if "base64" not in head:
        raise ValueError("not_base64")
    return base64.b64decode(payload)


class BridgeServer:
    """后台线程里的本机 HTTP 服务；只有 127.0.0.1 能连。"""

    def __init__(self, store, port=DEFAULT_BRIDGE_PORT, token="",
                 on_copy=None):
        self.store = store
        self.port = int(port or 0)
        self.token = str(token or "")
        self.on_copy = on_copy
        self.last_seen = 0.0
        # 启动失败的原因（给设置里的状态行显示用，之前是静默失败）
        self.last_error = ""
        self._httpd = None
        self._thread = None

    @property
    def running(self):
        return self._httpd is not None

    @property
    def actual_port(self):
        if self._httpd is None:
            return 0
        return int(self._httpd.server_address[1])

    def start(self):
        """启动；端口被占用时自动换一个空闲端口。返回实际端口。"""
        if self._httpd is not None:
            return self.actual_port
        last_err = None
        for candidate in ([self.port] if self.port else []) + [0]:
            try:
                httpd = _BridgeHTTPServer((BRIDGE_HOST, candidate), _Handler)
            except Exception as err:
                last_err = err
                continue
            httpd.store = self.store            # type: ignore[attr-defined]
            httpd.token = self.token            # type: ignore[attr-defined]
            httpd.on_copy = self.on_copy        # type: ignore[attr-defined]
            httpd.last_seen = 0.0               # type: ignore[attr-defined]
            self._httpd = httpd
            thread = threading.Thread(target=httpd.serve_forever,
                                      kwargs={"poll_interval": 0.4},
                                      daemon=True,
                                      name="YouBoardBridge")
            thread.start()
            self._thread = thread
            self.last_error = ""
            return self.actual_port
        if last_err:
            self.last_error = "%s: %s" % (type(last_err).__name__, last_err)
            raise last_err
        self.last_error = "无法绑定本机端口"
        return 0

    def stop(self):
        httpd, self._httpd = self._httpd, None
        if httpd is not None:
            try:
                httpd.shutdown()
                httpd.server_close()
            except Exception:
                pass
        self._thread = None
        self.last_error = ""

    def status(self):
        seen = 0.0
        if self._httpd is not None:
            seen = float(getattr(self._httpd, "last_seen", 0.0) or 0.0)
        return {
            "running": self.running,
            "port": self.actual_port,
            "last_seen": seen,
            "last_seen_text": (datetime.fromtimestamp(seen).strftime(
                "%m-%d %H:%M:%S") if seen else ""),
        }
