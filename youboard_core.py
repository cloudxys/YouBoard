#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
YouBoard Core — Win32 helpers, type detection, data store, monitor, snapshots,
config persistence and Windows autostart (registry).
"""

import copy
import ctypes
import hashlib
import json
import os
import re
import struct
import sys
import threading
import time
import uuid
from ctypes import wintypes
from datetime import datetime

import pyperclip

try:
    from PIL import Image, ImageGrab
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

# ===========================================================================
# Constants
# ===========================================================================

# 数据目录：打包为 EXE 后数据落在 EXE 所在目录（便携版，可直接拷贝给朋友用）；
# 开发运行时落在脚本目录。
if getattr(sys, "frozen", False):
    _BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))

HISTORY_FILE = os.path.join(_BASE_DIR, ".youboard.json")
SNAPSHOTS_FILE = os.path.join(_BASE_DIR, ".youboard_snapshots.json")
CONFIG_FILE = os.path.join(_BASE_DIR, "youboard_config.json")
IMAGES_DIR = os.path.join(_BASE_DIR, "images")
MAX_ENTRIES = None          # 无上限：不限制历史记录条数
POLL_INTERVAL = 0.5
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
URL_PATTERN = re.compile(r'https?://\S+|www\.\S+')

# 旧版数据文件名（品牌更名前的历史遗留），首次启动自动迁移
_LEGACY_FILES = {
    ".clipboard_history.json": HISTORY_FILE,
    ".clipboard_snapshots.json": SNAPSHOTS_FILE,
}


def _migrate_legacy_files():
    for old_name, new_path in _LEGACY_FILES.items():
        old_path = os.path.join(_BASE_DIR, old_name)
        if os.path.exists(old_path) and not os.path.exists(new_path):
            try:
                os.replace(old_path, new_path)
            except OSError:
                pass


_migrate_legacy_files()


# ===========================================================================
# Config（语言等用户偏好，JSON 持久化）
# ===========================================================================

def load_config():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, IOError, OSError):
        return {}


def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return True
    except (IOError, OSError):
        return False


# ===========================================================================
# 开机自启动（HKCU\...\Run 注册表项）
# ===========================================================================

AUTOSTART_REG_NAME = "YouBoard"
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _autostart_command():
    """开机启动命令：打包后用 EXE 自身路径；开发运行用 pythonw + 脚本。"""
    if getattr(sys, "frozen", False):
        return '"%s"' % os.path.abspath(sys.executable)
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(pythonw):
        pythonw = sys.executable
    script = os.path.abspath(sys.argv[0]) if (sys.argv and sys.argv[0]) else os.path.abspath(__file__)
    return '"%s" "%s"' % (pythonw, script)


def get_autostart():
    """当前是否已注册开机自启动。"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_READ) as k:
            winreg.QueryValueEx(k, AUTOSTART_REG_NAME)
        return True
    except OSError:
        return False


def set_autostart(enabled):
    """开启/关闭开机自启动，成功返回 True。"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if enabled:
                winreg.SetValueEx(k, AUTOSTART_REG_NAME, 0, winreg.REG_SZ, _autostart_command())
            else:
                try:
                    winreg.DeleteValue(k, AUTOSTART_REG_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False

CF_DIB = 8
CF_HDROP = 15
GMEM_MOVEABLE = 0x0002
GHND = 0x0042

IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".svg", ".webp", ".gif", ".heif", ".heic",
    ".raw", ".ico", ".avif", ".apng", ".tiff", ".tif", ".bmp", ".pcx", ".eps",
    ".dib", ".nef", ".cr2", ".arw", ".orf", ".rw2",
}

# ===========================================================================
# Win32 ctypes declarations (64-bit safe: always set argtypes + restype)
# ===========================================================================

kernel32 = ctypes.windll.kernel32
user32 = ctypes.windll.user32

kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
kernel32.GlobalUnlock.restype = wintypes.BOOL
kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
kernel32.GlobalFree.restype = ctypes.c_void_p

user32.OpenClipboard.argtypes = [ctypes.c_void_p]
user32.OpenClipboard.restype = wintypes.BOOL
user32.CloseClipboard.argtypes = []
user32.CloseClipboard.restype = wintypes.BOOL
user32.EmptyClipboard.argtypes = []
user32.EmptyClipboard.restype = wintypes.BOOL
user32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
user32.SetClipboardData.restype = ctypes.c_void_p
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.GetClipboardData.restype = ctypes.c_void_p
user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
user32.IsClipboardFormatAvailable.restype = wintypes.BOOL


# ===========================================================================
# Clipboard type detection
# ===========================================================================

def is_image_file_path(path):
    return os.path.splitext(path)[1].lower() in IMAGE_EXTENSIONS


def get_clipboard_content():
    """Returns (type, data) tuple.
    type is 'text', 'image', 'file', or None.
    data is: str for text, PIL.Image for image, list[str] for files.
    """
    if HAS_PIL:
        result = ImageGrab.grabclipboard()
        if isinstance(result, Image.Image):
            return ("image", result)
        if isinstance(result, list):
            return ("file", result)

    try:
        text = pyperclip.paste()
        if text and text.strip():
            return ("text", text)
    except Exception:
        pass

    return (None, None)


# ===========================================================================
# Copy-back to Windows clipboard
# ===========================================================================

def set_clipboard_text(text):
    pyperclip.copy(text)


def set_clipboard_image(pil_image):
    img = pil_image.convert("RGB")
    width, height = img.size
    row_size = ((width * 3 + 3) // 4) * 4

    flipped = img.transpose(Image.FLIP_TOP_BOTTOM)
    raw = flipped.tobytes()
    pixels = bytearray()
    for y in range(height):
        start = y * width * 3
        pixels.extend(raw[start:start + width * 3])
        pad = row_size - width * 3
        if pad > 0:
            pixels.extend(b"\x00" * pad)

    header = struct.pack("<IiiHHIIiiII", 40, width, height, 1, 24, 0, len(pixels), 0, 0, 0, 0)
    dib_data = header + bytes(pixels)

    hmem = kernel32.GlobalAlloc(GHND, len(dib_data))
    if not hmem:
        return False
    ptr = kernel32.GlobalLock(hmem)
    if not ptr:
        kernel32.GlobalFree(hmem)
        return False
    try:
        ctypes.memmove(ptr, dib_data, len(dib_data))
    finally:
        kernel32.GlobalUnlock(hmem)

    user32.OpenClipboard(None)
    try:
        user32.EmptyClipboard()
        user32.SetClipboardData(CF_DIB, hmem)
    finally:
        user32.CloseClipboard()
    return True


def set_clipboard_files(file_paths):
    encoded = b""
    for fp in file_paths:
        encoded += fp.encode("utf-16-le") + b"\x00\x00"
    encoded += b"\x00\x00"

    DROPFILES_SIZE = 20
    total_size = DROPFILES_SIZE + len(encoded)

    hmem = kernel32.GlobalAlloc(GHND, total_size)
    if not hmem:
        return False
    ptr = kernel32.GlobalLock(hmem)
    if not ptr:
        kernel32.GlobalFree(hmem)
        return False
    try:
        ctypes.c_uint32.from_address(ptr).value = DROPFILES_SIZE
        ctypes.c_int32.from_address(ptr + 16).value = 1
        ctypes.memmove(ptr + DROPFILES_SIZE, encoded, len(encoded))
    finally:
        kernel32.GlobalUnlock(hmem)

    user32.OpenClipboard(None)
    try:
        user32.EmptyClipboard()
        user32.SetClipboardData(CF_HDROP, hmem)
    finally:
        user32.CloseClipboard()
    return True


# ===========================================================================
# ClipboardStore — 3 categories, each max 10000, + snapshot history
# ===========================================================================

class ClipboardStore:
    def __init__(self, path=HISTORY_FILE, max_entries=MAX_ENTRIES):
        self.path = path
        self.snapshots_path = SNAPSHOTS_FILE
        self.max_entries = max_entries
        self.categories = {}
        self._snapshots = []
        self._lock = threading.Lock()
        self._self_copy_time = 0.0      # 应用内复制时间戳（防重复收录）
        self._init_empty()
        self._load()
        self._load_snapshots()

    def mark_self_copy(self):
        """标记应用内复制，监控线程在短时间窗口内跳过剪贴板变化。"""
        self._self_copy_time = time.time()

    def is_self_copy(self, window=2.0):
        """判断当前剪贴板变化是否由应用内复制触发。"""
        return (time.time() - self._self_copy_time) < window

    def _init_empty(self):
        self.categories = {
            "text":  {"pinned": [], "entries": []},
            "image": {"pinned": [], "entries": []},
            "file":  {"pinned": [], "entries": []},
            "url":   {"pinned": [], "entries": []},
        }

    # ---- persistence ----

    def _load(self):
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError):
            return

        version = data.get("version", 1)
        if version == 1:
            self.categories = {
                "text": {
                    "pinned": data.get("pinned", []),
                    "entries": data.get("entries", [])[:self.max_entries],
                },
                "image": {"pinned": [], "entries": []},
                "file":  {"pinned": [], "entries": []},
                "url":   {"pinned": [], "entries": []},
            }
            self._save()
            return

        cats = data.get("categories", {})
        self.categories = {
            "text":  cats.get("text",  {"pinned": [], "entries": []}),
            "image": cats.get("image", {"pinned": [], "entries": []}),
            "file":  cats.get("file",  {"pinned": [], "entries": []}),
            "url":   cats.get("url",   {"pinned": [], "entries": []}),
        }

    def _save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump({"version": 2, "categories": self.categories},
                          f, ensure_ascii=False, indent=2)
        except IOError:
            pass

    # ---- snapshots ----

    def _load_snapshots(self):
        if not os.path.exists(self.snapshots_path):
            self._snapshots = []
            return
        try:
            with open(self.snapshots_path, "r", encoding="utf-8") as f:
                self._snapshots = json.load(f)
        except (json.JSONDecodeError, IOError):
            self._snapshots = []

    def _save_snapshots(self):
        try:
            with open(self.snapshots_path, "w", encoding="utf-8") as f:
                json.dump(self._snapshots, f, ensure_ascii=False, indent=2)
        except IOError:
            pass

    def save_snapshot(self, description):
        snap = {
            "id": uuid.uuid4().hex[:12],
            "desc": description,
            "time": datetime.now().isoformat(),
            "state": copy.deepcopy(self.categories),
        }
        self._snapshots.append(snap)
        self._save_snapshots()
        return snap

    def get_snapshots(self):
        return list(self._snapshots)

    def restore_snapshot(self, snapshot_id):
        for snap in self._snapshots:
            if snap["id"] == snapshot_id:
                with self._lock:
                    self.categories = copy.deepcopy(snap["state"])
                    self._save()
                return True
        return False

    def clear_snapshots(self):
        self._snapshots = []
        self._save_snapshots()

    # ---- hashing ----

    @staticmethod
    def _text_hash(text):
        return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()

    @staticmethod
    def _image_hash(pil_image):
        raw = pil_image.convert("RGB").tobytes()
        return hashlib.sha256(raw).hexdigest()

    @staticmethod
    def _files_hash(file_paths):
        combined = "\0".join(sorted(file_paths))
        return hashlib.sha256(combined.encode("utf-16-le")).hexdigest()

    # ---- add entries (no auto-snapshot — monitor calls these) ----

    def add_text(self, text):
        text = text.strip()
        if not text:
            return False
        h = self._text_hash(text)
        with self._lock:
            cat = self.categories["text"]
            cat["entries"] = [e for e in cat["entries"] if e["hash"] != h]
            cat["entries"].insert(0, {
                "hash": h, "type": "text", "content": text,
                "timestamp": datetime.now().isoformat(), "length": len(text),
            })
            if self.max_entries and len(cat["entries"]) > self.max_entries:
                cat["entries"] = cat["entries"][:self.max_entries]
            self._save()
        return True

    def add_image(self, pil_image, image_hash, source_name=None):
        images_dir = IMAGES_DIR
        os.makedirs(images_dir, exist_ok=True)

        save_path = os.path.join(images_dir, f"{image_hash}.png")
        if not os.path.exists(save_path):
            pil_image.save(save_path, "PNG")

        thumb_path = os.path.join(images_dir, f"thumb_{image_hash}.png")
        if not os.path.exists(thumb_path):
            thumb = pil_image.copy()
            thumb.thumbnail((320, 320), Image.LANCZOS)
            thumb.save(thumb_path, "PNG")

        fmt = pil_image.format or "PNG"
        with self._lock:
            cat = self.categories["image"]
            cat["entries"] = [e for e in cat["entries"] if e["hash"] != image_hash]
            cat["entries"].insert(0, {
                "hash": image_hash, "type": "image",
                "filename": f"images/{image_hash}.png",
                "original_format": fmt,
                "source_name": source_name or "",
                "width": pil_image.width, "height": pil_image.height,
                "file_size": os.path.getsize(save_path),
                "timestamp": datetime.now().isoformat(),
            })
            if self.max_entries and len(cat["entries"]) > self.max_entries:
                cat["entries"] = cat["entries"][:self.max_entries]
            self._save()
        return True

    def add_files(self, file_paths, files_hash):
        if not file_paths:
            return False
        file_sizes = []
        for fp in file_paths:
            try:
                file_sizes.append(os.path.getsize(fp))
            except OSError:
                file_sizes.append(-1)
        with self._lock:
            cat = self.categories["file"]
            cat["entries"] = [e for e in cat["entries"] if e["hash"] != files_hash]
            cat["entries"].insert(0, {
                "hash": files_hash, "type": "file",
                "file_paths": list(file_paths),
                "file_sizes": file_sizes,
                "file_count": len(file_paths),
                "timestamp": datetime.now().isoformat(),
            })
            if self.max_entries and len(cat["entries"]) > self.max_entries:
                cat["entries"] = cat["entries"][:self.max_entries]
            self._save()
        return True

    def add_url(self, url):
        """收录一条网址到 url 分类。"""
        url = url.strip()
        if not url:
            return False
        h = self._text_hash(url)
        with self._lock:
            cat = self.categories["url"]
            cat["entries"] = [e for e in cat["entries"] if e["hash"] != h]
            cat["entries"].insert(0, {
                "hash": h, "type": "url", "content": url,
                "timestamp": datetime.now().isoformat(), "length": len(url),
            })
            if self.max_entries and len(cat["entries"]) > self.max_entries:
                cat["entries"] = cat["entries"][:self.max_entries]
            self._save()
        return True

    # ---- pin / unpin ----

    def pin(self, entry_hash):
        with self._lock:
            for cat in self.categories.values():
                for i, e in enumerate(cat["entries"]):
                    if e["hash"] == entry_hash:
                        cat["entries"].pop(i)
                        cat["pinned"].insert(0, e)
                        self._save()
                        return True
            return False

    def unpin(self, entry_hash):
        with self._lock:
            for cat in self.categories.values():
                for i, e in enumerate(cat["pinned"]):
                    if e["hash"] == entry_hash:
                        cat["pinned"].pop(i)
                        cat["entries"].insert(0, e)
                        if self.max_entries and len(cat["entries"]) > self.max_entries:
                            cat["entries"] = cat["entries"][:self.max_entries]
                        self._save()
                        return True
            return False

    def toggle_pin(self, entry_hash):
        if self.is_pinned(entry_hash):
            return self.unpin(entry_hash)
        return self.pin(entry_hash)

    def is_pinned(self, entry_hash):
        with self._lock:
            for cat in self.categories.values():
                if any(e["hash"] == entry_hash for e in cat["pinned"]):
                    return True
            return False

    def pin_many(self, hashes):
        count = 0
        with self._lock:
            for h in hashes:
                for cat in self.categories.values():
                    for i, e in enumerate(cat["entries"]):
                        if e["hash"] == h:
                            cat["entries"].pop(i)
                            cat["pinned"].insert(0, e)
                            count += 1
                            break
            if count:
                self._save()
        return count

    def unpin_many(self, hashes):
        count = 0
        with self._lock:
            for h in hashes:
                for cat in self.categories.values():
                    for i, e in enumerate(cat["pinned"]):
                        if e["hash"] == h:
                            cat["pinned"].pop(i)
                            cat["entries"].insert(0, e)
                            count += 1
                            break
            if count:
                for cat in self.categories.values():
                    if len(cat["entries"]) > self.max_entries:
                        cat["entries"] = cat["entries"][:self.max_entries]
                self._save()
        return count

    # ---- read ----

    def get_by_type(self, entry_type):
        with self._lock:
            cat = self.categories.get(entry_type, {"pinned": [], "entries": []})
            return list(cat["pinned"]) + list(cat["entries"])

    def get_all(self):
        result = []
        with self._lock:
            for key in ("text", "image", "file", "url"):
                cat = self.categories[key]
                result.extend(cat["pinned"])
                result.extend(cat["entries"])
        return result

    def get_recent(self, n=20):
        return self.get_all()[:n]

    # ---- counts ----

    def count(self, entry_type=None):
        with self._lock:
            if entry_type:
                cat = self.categories.get(entry_type, {})
                return len(cat.get("pinned", [])) + len(cat.get("entries", []))
            return sum(len(c["pinned"]) + len(c["entries"]) for c in self.categories.values())

    def pinned_count(self, entry_type=None):
        with self._lock:
            if entry_type:
                return len(self.categories.get(entry_type, {}).get("pinned", []))
            return sum(len(c["pinned"]) for c in self.categories.values())

    def unpinned_count(self, entry_type=None):
        with self._lock:
            if entry_type:
                return len(self.categories.get(entry_type, {}).get("entries", []))
            return sum(len(c["entries"]) for c in self.categories.values())

    # ---- delete ----

    def delete(self, entry_hash):
        with self._lock:
            for cat_name, cat in self.categories.items():
                for lst_name in ("pinned", "entries"):
                    for i, e in enumerate(cat[lst_name]):
                        if e["hash"] == entry_hash:
                            cat[lst_name].pop(i)
                            self._save()
                            return True
        return False

    def delete_many(self, hashes):
        count = 0
        with self._lock:
            for h in hashes:
                for cat in self.categories.values():
                    for lst_name in ("pinned", "entries"):
                        for i, e in enumerate(cat[lst_name]):
                            if e["hash"] == h:
                                cat[lst_name].pop(i)
                                count += 1
                                break
            if count:
                self._save()
        return count

    def clear(self):
        with self._lock:
            self._init_empty()
            self._save()

    def clear_type(self, entry_type):
        with self._lock:
            if entry_type in self.categories:
                self.categories[entry_type] = {"pinned": [], "entries": []}
                self._save()

    def clear_unpinned(self):
        with self._lock:
            for cat in self.categories.values():
                cat["entries"] = []
            self._save()

    def clear_type_unpinned(self, entry_type):
        with self._lock:
            if entry_type in self.categories:
                self.categories[entry_type]["entries"] = []
                self._save()

    # ---- search ----

    def search(self, keyword, entry_type=None):
        kw = keyword.lower()
        result = []
        with self._lock:
            cats = [entry_type] if entry_type else self.categories.keys()
            for key in cats:
                cat = self.categories.get(key, {"pinned": [], "entries": []})
                for lst_name in ("pinned", "entries"):
                    for e in cat[lst_name]:
                        if key in ("text", "url"):
                            if kw in e.get("content", "").lower():
                                result.append(e)
                        elif key == "image":
                            fn = e.get("filename", "").lower()
                            fmt = e.get("original_format", "").lower()
                            if kw in fn or kw in fmt:
                                result.append(e)
                        elif key == "file":
                            paths = " ".join(e.get("file_paths", [])).lower()
                            if kw in paths:
                                result.append(e)
        return result


# ===========================================================================
# ClipboardMonitor — event-driven (AddClipboardFormatListener), polling fallback.
# Routes image files to image category.
# ===========================================================================

class ClipboardMonitor(threading.Thread):
    """剪贴板监控线程。

    优先使用 Win32 AddClipboardFormatListener 事件驱动（只在剪贴板真正
    变化时才读取，带 150ms 防抖 + 重试），失败时回退到轮询。
    外部通过 consume_change() 在主线程安全地取走"有新内容"信号。
    """

    WM_DESTROY = 0x0002
    WM_QUIT = 0x0012
    WM_TIMER = 0x0113
    WM_CLIPBOARDUPDATE = 0x031D
    TIMER_ID = 1
    DEBOUNCE_MS = 150
    MAX_RETRIES = 3

    def __init__(self, store, callback=None):
        super().__init__(daemon=True)
        self.store = store
        self.callback = callback
        self._running = False
        self._change_event = threading.Event()
        self._thread_id = None
        self._use_events = False

    # ---- public API ----

    def stop(self):
        self._running = False
        if self._use_events and self._thread_id:
            try:
                user32.PostThreadMessageW(self._thread_id, self.WM_QUIT, 0, 0)
            except Exception:
                pass

    def consume_change(self):
        """线程安全：取走一次变化信号（GUI 主循环定时调用）。"""
        if self._change_event.is_set():
            self._change_event.clear()
            return True
        return False

    # ---- main ----

    def run(self):
        self._running = True
        self._init_baseline()
        try:
            self._run_event_loop()
        except Exception:
            # 事件监听不可用时回退轮询
            if self._running:
                self._run_polling()

    def _init_baseline(self):
        """记录启动时剪贴板内容作为基线，避免重复收录。"""
        try:
            ctype, data = get_clipboard_content()
        except Exception:
            ctype, data = None, None
        self._last_text = data if ctype == "text" else ""
        self._last_image_hash = self.store._image_hash(data) if (ctype == "image" and HAS_PIL) else ""
        self._last_file_hash = self.store._files_hash(data) if ctype == "file" else ""

    def _notify(self):
        self._change_event.set()
        if self.callback:
            try:
                self.callback()
            except Exception:
                pass

    def _process_clipboard(self):
        """读取一次剪贴板并收录新内容。返回 False 表示读取失败（供重试）。"""
        # 应用内复制（Enter/按钮）触发的剪贴板变化不重复收录
        if self.store.is_self_copy():
            return True

        try:
            ctype, data = get_clipboard_content()
        except Exception:
            return False
        if ctype is None:
            return False

        if ctype == "text":
            if data != self._last_text:
                # URL 智能识别：纯网址→仅存 url 分类；混合→text + url 双存
                urls = URL_PATTERN.findall(data)
                stripped = URL_PATTERN.sub('', data).strip()
                is_pure_url = bool(urls) and not stripped

                if is_pure_url:
                    # 纯网址内容：每个网址单独收录到 url 分类
                    for u in urls:
                        self.store.add_url(u)
                    self._notify()
                else:
                    # 正常收录到文字分类
                    if self.store.add_text(data):
                        self._notify()
                    # 混合内容中的网址也提取到 url 分类（文字中保留不删）
                    if urls:
                        for u in urls:
                            self.store.add_url(u)
                        self._notify()

                self._last_text = data
                self._last_image_hash = ""
                self._last_file_hash = ""

        elif ctype == "image":
            h = self.store._image_hash(data)
            if h != self._last_image_hash:
                self.store.add_image(data, h)
                self._notify()
                self._last_image_hash = h
                self._last_text = ""
                self._last_file_hash = ""

        elif ctype == "file":
            h = self.store._files_hash(data)
            if h != self._last_file_hash:
                # 如果全部是图片文件，尝试按图片收录
                if HAS_PIL and data and all(is_image_file_path(p) for p in data):
                    routed = False
                    for fp in data:
                        if os.path.exists(fp):
                            try:
                                img = Image.open(fp)
                                img.load()
                                src_name = os.path.basename(fp)
                                self.store.add_image(img, self.store._image_hash(img), source_name=src_name)
                                routed = True
                                break
                            except Exception:
                                continue
                    if not routed:
                        self.store.add_files(data, h)
                else:
                    self.store.add_files(data, h)
                self._notify()
                self._last_file_hash = h
                self._last_text = ""
                self._last_image_hash = ""
        return True

    # ---- Win32 event-driven loop ----

    def _run_event_loop(self):
        WNDPROC = ctypes.WINFUNCTYPE(
            ctypes.c_longlong, wintypes.HWND, wintypes.UINT,
            wintypes.WPARAM, wintypes.LPARAM)

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [
                ("style", wintypes.UINT),
                ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HANDLE),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR),
            ]

        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        class MSG(ctypes.Structure):
            _fields_ = [
                ("hwnd", wintypes.HWND), ("message", wintypes.UINT),
                ("wParam", wintypes.WPARAM), ("lParam", wintypes.LPARAM),
                ("time", wintypes.DWORD), ("pt", POINT),
            ]

        user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                          wintypes.WPARAM, wintypes.LPARAM]
        user32.DefWindowProcW.restype = ctypes.c_longlong
        user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
        user32.RegisterClassW.restype = wintypes.ATOM
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.ATOM, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), wintypes.HWND,
                                       wintypes.UINT, wintypes.UINT]
        user32.GetMessageW.restype = wintypes.BOOL
        user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT,
                                              wintypes.WPARAM, wintypes.LPARAM]
        user32.PostThreadMessageW.restype = wintypes.BOOL
        user32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t,
                                    wintypes.UINT, ctypes.c_void_p]
        user32.SetTimer.restype = ctypes.c_size_t
        user32.KillTimer.argtypes = [wintypes.HWND, ctypes.c_size_t]
        user32.AddClipboardFormatListener.argtypes = [wintypes.HWND]
        user32.AddClipboardFormatListener.restype = wintypes.BOOL
        user32.RemoveClipboardFormatListener.argtypes = [wintypes.HWND]
        user32.RemoveClipboardFormatListener.restype = wintypes.BOOL
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        kernel32.GetCurrentThreadId.argtypes = []
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD

        self._thread_id = kernel32.GetCurrentThreadId()
        retries = [0]

        def wndproc(hwnd, msg, wparam, lparam):
            if msg == self.WM_CLIPBOARDUPDATE:
                # 防抖：复制操作常连续触发多次，延迟合并处理
                user32.SetTimer(hwnd, self.TIMER_ID, self.DEBOUNCE_MS, None)
                return 0
            if msg == self.WM_TIMER:
                user32.KillTimer(hwnd, self.TIMER_ID)
                ok = self._process_clipboard()
                if not ok and retries[0] < self.MAX_RETRIES:
                    retries[0] += 1
                    user32.SetTimer(hwnd, self.TIMER_ID, 120, None)
                else:
                    retries[0] = 0
                return 0
            if msg == self.WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        # 必须持有回调引用，防止被垃圾回收
        self._wndproc_ref = WNDPROC(wndproc)

        hinstance = kernel32.GetModuleHandleW(None)
        cls_name = "ClipHistListener_%d" % os.getpid()
        wc = WNDCLASSW(0, self._wndproc_ref, 0, 0, hinstance,
                       None, None, None, None, cls_name)
        atom = user32.RegisterClassW(ctypes.byref(wc))
        if not atom:
            raise OSError("RegisterClassW failed")
        HWND_MESSAGE = wintypes.HWND(-3)
        hwnd = user32.CreateWindowExW(
            0, atom, "ClipHistListener", 0, 0, 0, 0, 0,
            HWND_MESSAGE, None, hinstance, None)
        if not hwnd:
            raise OSError("CreateWindowExW failed")
        if not user32.AddClipboardFormatListener(hwnd):
            user32.DestroyWindow(hwnd)
            raise OSError("AddClipboardFormatListener failed")
        self._use_events = True

        msg = MSG()
        while self._running:
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret <= 0:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        try:
            user32.RemoveClipboardFormatListener(hwnd)
            user32.DestroyWindow(hwnd)
        except Exception:
            pass

    # ---- polling fallback ----

    def _run_polling(self):
        while self._running:
            self._process_clipboard()
            time.sleep(POLL_INTERVAL)


# ===========================================================================
# 图标路径 & 系统托盘（pystray）
# ===========================================================================

def get_icon_path():
    """跨路径兼容：返回 You.ico 的绝对路径。
    - PyInstaller 打包后：读取 _MEIPASS 临时解压目录
    - 本地脚本调试：读取脚本同目录下的 You.ico
    """
    base = getattr(sys, "_MEIPASS", None)
    if base:
        p = os.path.join(base, "You.ico")
        if os.path.exists(p):
            return p
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.path.join(here, "You.ico"),
                 os.path.join(os.path.dirname(here), "logo", "You.ico")):
        if os.path.exists(cand):
            return cand
    return None


def get_app_icon():
    """返回 PIL.Image 图标对象，供 pystray 托盘 / tkinter 窗口统一使用。
    区分本地脚本调试 / PyInstaller 打包 exe 两种环境。
    """
    if not HAS_PIL:
        return None
    ico_path = get_icon_path()
    if ico_path:
        try:
            return Image.open(ico_path)
        except Exception:
            pass
    # 回退：纯色占位图（不应触发，仅保底）
    return Image.new("RGBA", (64, 64), (79, 157, 248, 255))


class TrayIcon:
    """系统托盘图标（pystray），右键菜单：显示主窗口 / 退出。"""

    def __init__(self, on_show=None, on_quit=None, title="YouBoard"):
        self._on_show = on_show
        self._on_quit = on_quit
        self._title = title
        self._icon = None
        self._thread = None

    def _create_menu(self):
        import pystray
        return pystray.Menu(
            pystray.MenuItem("显示主窗口", self._show_window, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出 YouBoard", self._quit_app),
        )

    def _show_window(self, icon=None, item=None):
        if self._on_show:
            self._on_show()

    def _quit_app(self, icon=None, item=None):
        if self._icon:
            self._icon.stop()
        if self._on_quit:
            self._on_quit()

    def start(self):
        """在后台线程启动托盘图标，强制传入 You.ico 图片对象。"""
        import pystray
        app_icon = get_app_icon()
        if app_icon is None:
            return
        self._icon = pystray.Icon(
            "youboard", app_icon, "YouBoard 剪贴板管理器", self._create_menu())
        self._thread = threading.Thread(target=self._icon.run, daemon=True)
        self._thread.start()

    def stop(self):
        if self._icon:
            try:
                self._icon.stop()
            except Exception:
                pass
