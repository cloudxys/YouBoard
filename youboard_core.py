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
from datetime import datetime

import pyperclip

IS_WIN = (sys.platform == "win32")
IS_MAC = (sys.platform == "darwin")
if IS_WIN:
    from ctypes import wintypes

try:
    import PIL  # noqa: F401  # 轻量探测；PIL.Image/ImageGrab 按需在调用点懒加载
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

# ===========================================================================
# Constants
# ===========================================================================

# 数据目录：Windows 打包为 EXE 后数据落在 EXE 所在目录（便携版，可直接拷贝给朋友用）；
# macOS 打包为 .app 后 Bundle 内目录只读，数据落在 ~/Library/Application Support/YouBoard；
# 开发运行时落在脚本目录。
if getattr(sys, "frozen", False):
    if IS_MAC:
        _BASE_DIR = os.path.join(os.path.expanduser("~"), "Library",
                                 "Application Support", "YouBoard")
        try:
            os.makedirs(_BASE_DIR, exist_ok=True)
        except OSError:
            pass
    else:
        _BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))

HISTORY_FILE = os.path.join(_BASE_DIR, ".youboard.json")
SNAPSHOTS_FILE = os.path.join(_BASE_DIR, ".youboard_snapshots.json")
CONFIG_FILE = os.path.join(_BASE_DIR, "youboard_config.json")
IMAGES_DIR = os.path.join(_BASE_DIR, "images")
# 压缩包内部复制的文件物化目录（FileGroupDescriptor 内容落地缓存）
FILE_CACHE_DIR = os.path.join(_BASE_DIR, "file_cache")
# 单个物化文件大小上限：超过则跳过，避免一次性占用过大内存
MAX_FGD_FILE_SIZE = 500 * 1024 * 1024
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
# 开机自启动（Windows: HKCU\...\Run 注册表项；macOS: LaunchAgent plist）
# ===========================================================================

if IS_MAC:
    _LAUNCH_AGENT_LABEL = "com.youboard.app"

    def _mac_launch_agent_path():
        return os.path.join(os.path.expanduser("~"), "Library",
                            "LaunchAgents", _LAUNCH_AGENT_LABEL + ".plist")

    def _mac_autostart_command():
        """开机启动命令：打包后用 .app 内可执行文件；开发运行用 python + 脚本。"""
        if getattr(sys, "frozen", False):
            return [os.path.abspath(sys.executable)]
        script = (os.path.abspath(sys.argv[0]) if (sys.argv and sys.argv[0])
                  else os.path.abspath(__file__))
        return [sys.executable, script]

    def get_autostart():
        """当前是否已注册开机自启动（LaunchAgent）。"""
        return os.path.exists(_mac_launch_agent_path())

    def set_autostart(enabled):
        """开启/关闭开机自启动（写入/删除 LaunchAgent plist），成功返回 True。"""
        path = _mac_launch_agent_path()
        try:
            if not enabled:
                if os.path.exists(path):
                    os.remove(path)
                return True
            import plistlib
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                plistlib.dump({
                    "Label": _LAUNCH_AGENT_LABEL,
                    "ProgramArguments": _mac_autostart_command(),
                    "RunAtLoad": True,
                }, f)
            return True
        except Exception:
            return False

else:
    AUTOSTART_REG_NAME = "YouBoard"
    _RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

    def _autostart_command():
        """开机启动命令：打包后用 EXE 自身路径；开发运行用 pythonw + 脚本。"""
        if getattr(sys, "frozen", False):
            return '"%s"' % os.path.abspath(sys.executable)
        pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        if not os.path.exists(pythonw):
            pythonw = sys.executable
        script = (os.path.abspath(sys.argv[0]) if (sys.argv and sys.argv[0])
                  else os.path.abspath(__file__))
        return '"%s" "%s"' % (pythonw, script)

    def get_autostart():
        """当前是否已注册开机自启动。"""
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                                winreg.KEY_READ) as k:
                winreg.QueryValueEx(k, AUTOSTART_REG_NAME)
            return True
        except OSError:
            return False

    def set_autostart(enabled):
        """开启/关闭开机自启动，成功返回 True。"""
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                                winreg.KEY_SET_VALUE) as k:
                if enabled:
                    winreg.SetValueEx(k, AUTOSTART_REG_NAME, 0,
                                      winreg.REG_SZ, _autostart_command())
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

if IS_WIN:
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
    kernel32.GlobalSize.argtypes = [ctypes.c_void_p]
    kernel32.GlobalSize.restype = ctypes.c_size_t

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
    user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
    user32.RegisterClipboardFormatW.restype = wintypes.UINT

    # 剪贴板"虚拟文件"格式：在压缩包/压缩文件夹内部复制文件时
    # （7-Zip、WinRAR、Bandizip、资源管理器打开 zip 等，覆盖
    # 7z/rar/zip/tar/gz/bz2/lzma/xz），剪贴板里没有 CF_HDROP 路径，
    # 只有 FileGroupDescriptorW（文件清单）+ FileContents（按需取内容）。
    CF_FILEGROUPDESCRIPTORW = user32.RegisterClipboardFormatW("FileGroupDescriptorW")
    CF_FILECONTENTS = user32.RegisterClipboardFormatW("FileContents")


# ===========================================================================
# Clipboard type detection
# ===========================================================================

def is_image_file_path(path):
    return os.path.splitext(path)[1].lower() in IMAGE_EXTENSIONS


# ===========================================================================
# 剪贴板"虚拟文件"物化（压缩包/压缩文件夹内部复制）
#
# 在 7-Zip、WinRAR、Bandizip、资源管理器打开 zip 等场景内部复制文件时
# （覆盖 7z/rar/zip/tar/gz/bz2/lzma/xz 等所有压缩格式），剪贴板里没有
# CF_HDROP 路径，只有 FileGroupDescriptorW（文件清单）+ FileContents
# （文件内容，按需渲染）。这类复制几乎都通过 OleSetClipboard 发布，
# 必须用 OLE IDataObject::GetData(lindex=文件序号) 才能按序号取到内容。
# ===========================================================================

if IS_WIN:
    ole32 = ctypes.windll.ole32
    ole32.OleInitialize.argtypes = [ctypes.c_void_p]
    ole32.OleInitialize.restype = ctypes.c_long
    ole32.OleGetClipboard.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
    ole32.OleGetClipboard.restype = ctypes.c_long

    class FORMATETC(ctypes.Structure):
        _fields_ = [("cfFormat", wintypes.UINT),
                    ("ptd", ctypes.c_void_p),
                    ("dwAspect", wintypes.DWORD),
                    ("lindex", ctypes.c_long),
                    ("tymed", wintypes.DWORD)]

    class STGMEDIUM(ctypes.Structure):
        _fields_ = [("tymed", wintypes.DWORD),
                    ("hGlobal", ctypes.c_void_p),      # union: HGLOBAL 或 IStream*
                    ("pUnkForRelease", ctypes.c_void_p)]
else:
    # macOS 不使用 OLE/FileGroupDescriptor，仅占位避免引用报错
    ole32 = None

    class FORMATETC:
        pass

    class STGMEDIUM:
        pass


TYMED_HGLOBAL = 1
TYMED_ISTREAM = 2
DVASPECT_CONTENT = 1

_com_state = threading.local()


def _ensure_com():
    if getattr(_com_state, "ok", False):
        return
    hr = ole32.OleInitialize(None)              # STA + OLE 剪贴板支持
    if hr >= 0 or hr == -2147417850:            # S_OK / S_FALSE / 已初始化
        _com_state.ok = True


def _vt_call(obj, index, proto, *args):
    vt = ctypes.c_void_p.from_address(obj).value
    fn = ctypes.c_void_p.from_address(vt + 8 * index).value
    return proto(fn)(obj, *args)


def _idataobj_get_data(obj, fmt, lindex):
    """向 IDataObject 按序号请求数据，返回 bytes 或 None（支持 HGLOBAL / IStream）。"""
    fe = FORMATETC(fmt, None, DVASPECT_CONTENT, lindex, TYMED_HGLOBAL | TYMED_ISTREAM)
    stm = STGMEDIUM(0, None, None)
    proto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p,
                               ctypes.POINTER(FORMATETC), ctypes.POINTER(STGMEDIUM))
    hr = _vt_call(obj, 3, proto, ctypes.byref(fe), ctypes.byref(stm))  # GetData
    if hr != 0:
        return None
    try:
        if stm.tymed == TYMED_HGLOBAL:
            h = stm.hGlobal
            if not h:
                return None
            ptr = kernel32.GlobalLock(h)
            if not ptr:
                return None
            try:
                size = kernel32.GlobalSize(h)
                if size > MAX_FGD_FILE_SIZE:
                    return None
                return ctypes.string_at(ptr, size)
            finally:
                kernel32.GlobalUnlock(h)
        if stm.tymed == TYMED_ISTREAM:
            stream = stm.hGlobal
            if not stream:
                return None
            out = bytearray()
            step = 256 * 1024
            buf = ctypes.create_string_buffer(step)
            got = wintypes.DWORD(0)
            read_proto = ctypes.WINFUNCTYPE(
                ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p,
                wintypes.DWORD, ctypes.POINTER(wintypes.DWORD))
            while True:
                hr2 = _vt_call(stream, 3, read_proto, buf, step, ctypes.byref(got))
                if got.value:
                    out += buf.raw[:got.value]
                if hr2 != 0 or got.value == 0:
                    break
                if len(out) > MAX_FGD_FILE_SIZE:
                    return None
            return bytes(out)
        return None
    finally:
        if stm.pUnkForRelease:
            rel_proto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)
            _vt_call(stm.pUnkForRelease, 2, rel_proto)                # Release
        elif stm.tymed == TYMED_HGLOBAL and stm.hGlobal:
            kernel32.GlobalFree(stm.hGlobal)


def _fgd_raw_single_content():
    """非 OLE 老式源（WM_RENDERFORMAT 延迟渲染）兜底：只能取到渲染出的数据。"""
    if not user32.OpenClipboard(None):
        return None
    try:
        h = user32.GetClipboardData(CF_FILECONTENTS)
        if not h:
            return None
        ptr = kernel32.GlobalLock(h)
        if not ptr:
            return None
        try:
            size = kernel32.GlobalSize(h)
            if size > MAX_FGD_FILE_SIZE:
                return None
            return ctypes.string_at(ptr, size)
        finally:
            kernel32.GlobalUnlock(h)
    except Exception:
        return None
    finally:
        user32.CloseClipboard()


def _sanitize_rel_path(name):
    """把 FileGroupDescriptor 里的相对路径清洗成合法的 Windows 相对路径。"""
    name = name.replace("/", "\\")
    parts = []
    for seg in name.split("\\"):
        seg = "".join("_" if ch in '<>:"|?*' else ch for ch in seg).strip(" .")
        if seg:
            parts.append(seg)
    return "\\".join(parts)


# (头部长, 描述符步长) 候选布局。标准 FILEGROUPDESCRIPTORW = 4 字节头 +
# 连续 556 字节 FILEDESCRIPTORW；Windows 资源管理器 zip 压缩文件夹的
# Shell 数据对象使用 40 字节头 + 592 字节描述符（556 字节标准区 +
# 36 字节附加区）。两种布局的标准区字段位置一致：
# flags@0, FILETIME*3@4..27, sizeHigh@28, sizeLow@32, name@36。
_FGD_LAYOUTS = [(4, 556), (40, 592), (4, 592), (8, 556), (8, 592),
                (20, 556), (20, 592), (36, 556), (36, 592)]


def _parse_fgd(blob):
    """解析 FileGroupDescriptorW 数据，返回条目列表。

    每条目为 dict(flags, size, name, index)。自动尝试多种头部/步长布局，
    取第一个所有文件名都合法的布局。解析失败返回 None。
    """
    if not blob or len(blob) < 8:
        return None
    count = struct.unpack_from("<I", blob, 0)[0]
    if count <= 0 or count > 1024:
        return None

    def _name_at(off):
        if off + 556 > len(blob):
            return None
        try:
            nm = blob[off + 36: off + 556].decode("utf-16-le", errors="strict")
        except Exception:
            return None
        nm = nm.split("\0", 1)[0].strip()
        if not nm or len(nm) > 400 or any(ord(ch) < 32 for ch in nm):
            return None
        return nm

    for hdr, stride in _FGD_LAYOUTS:
        if hdr + count * stride > len(blob):
            continue
        entries = []
        ok = True
        for i in range(count):
            off = hdr + i * stride
            nm = _name_at(off)
            if nm is None:
                ok = False
                break
            flags = struct.unpack_from("<I", blob, off)[0]
            hi, lo = struct.unpack_from("<II", blob, off + 28)
            entries.append({"flags": flags, "size": (hi << 32) | lo,
                            "name": nm, "index": i})
        if ok:
            return entries
    return None


def _detect_dir_indices(entries):
    """推断哪些条目是目录：名字以 \\ 结尾，或是其他条目路径的前缀。"""
    dirs = set()
    norm = [e["name"].replace("/", "\\").rstrip("\\").lower() for e in entries]
    for i, e in enumerate(entries):
        nm = e["name"].replace("/", "\\")
        if nm.endswith("\\"):
            dirs.add(i)
            continue
        pref = norm[i] + "\\"
        for j, other in enumerate(norm):
            if j != i and other.startswith(pref):
                dirs.add(i)
                break
    return dirs


def materialize_fgd_files():
    """剪贴板为 FileGroupDescriptor（压缩包/压缩文件夹内复制）时，
    向源程序请求文件内容并物化到 FILE_CACHE_DIR，返回真实文件路径列表。

    缓存目录名取描述符内容哈希，保证同一次剪贴板内容重复读取时
    路径稳定（监控线程去重依赖稳定路径）。失败/不可用返回 None。
    """
    if not CF_FILEGROUPDESCRIPTORW or not CF_FILECONTENTS:
        return None
    # 只要求 FGD 存在：Shell zip 数据对象并不单独登记 FileContents 格式
    if not user32.IsClipboardFormatAvailable(CF_FILEGROUPDESCRIPTORW):
        return None

    # ---- 读取并解析文件清单 ----
    if not user32.OpenClipboard(None):
        return None
    try:
        hfgd = user32.GetClipboardData(CF_FILEGROUPDESCRIPTORW)
        if not hfgd:
            return None
        ptr = kernel32.GlobalLock(hfgd)
        if not ptr:
            return None
        try:
            blob = ctypes.string_at(ptr, kernel32.GlobalSize(hfgd))
        finally:
            kernel32.GlobalUnlock(hfgd)
    except Exception:
        return None
    finally:
        user32.CloseClipboard()

    entries = _parse_fgd(blob)
    if not entries:
        return None
    dirs = _detect_dir_indices(entries)
    files = [e for e in entries if e["index"] not in dirs]
    if not files:
        return None

    folder = os.path.join(FILE_CACHE_DIR, hashlib.sha1(blob).hexdigest()[:16])
    os.makedirs(folder, exist_ok=True)

    # ---- 取 OLE 数据对象（按序号取文件内容）----
    _ensure_com()
    ole_obj = ctypes.c_void_p()
    have_ole = ole32.OleGetClipboard(ctypes.byref(ole_obj)) == 0 and ole_obj.value
    # 部分源（Shell 压缩文件夹等）的 FileContents 序号只计文件不计目录，
    # 两种约定都尝试
    file_only_index = {e["index"]: k for k, e in enumerate(files)}

    paths = []
    seen = set()
    try:
        for e in files:
            fsize = e["size"]
            rel = _sanitize_rel_path(e["name"])
            if not rel or (fsize and fsize > MAX_FGD_FILE_SIZE):
                continue
            dest = os.path.join(folder, rel)
            if dest.lower() in seen:  # 同名冲突追加序号
                stem, ext = os.path.splitext(dest)
                n = 1
                while f"{stem} ({n}){ext}".lower() in seen:
                    n += 1
                dest = f"{stem} ({n}){ext}"
            seen.add(dest.lower())

            if fsize and os.path.exists(dest) and os.path.getsize(dest) == fsize:
                paths.append(dest)    # 同一次剪贴板重复读取：已物化，跳过
                continue
            if not fsize and os.path.exists(dest):
                paths.append(dest)    # 源未提供大小时以存在为准
                continue

            data = None
            if have_ole:
                for lindex in (e["index"], file_only_index.get(e["index"])):
                    if lindex is None:
                        continue
                    try:
                        data = _idataobj_get_data(
                            ole_obj.value, CF_FILECONTENTS, lindex)
                    except Exception:
                        data = None
                    if data is not None:
                        break
            if data is None and len(entries) == 1:
                data = _fgd_raw_single_content()   # 老式延迟渲染源兜底
            if data is None:
                continue

            try:
                ddir = os.path.dirname(dest)
                if ddir:
                    os.makedirs(ddir, exist_ok=True)
                tmp = dest + ".part"
                with open(tmp, "wb") as f:
                    f.write(data)
                os.replace(tmp, dest)
            except OSError:
                continue
            paths.append(dest)
    finally:
        if have_ole:
            rel_proto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)
            _vt_call(ole_obj.value, 2, rel_proto)   # IDataObject::Release

    return paths or None


def _mac_get_clipboard_content():
    """macOS 剪贴板读取（NSPasteboard）：文件 / 图片 / 文本。"""
    try:
        from AppKit import (NSPasteboard, NSPasteboardTypeString,
                            NSPasteboardTypePNG, NSPasteboardTypeTIFF,
                            NSPasteboardTypeFileURL)
    except Exception:
        # PyObjC 不可用时退回纯文本（pyperclip 内部走 pbcopy/pbpaste）
        try:
            text = pyperclip.paste()
            if text and text.strip():
                return ("text", text)
        except Exception:
            pass
        return (None, None)

    pb = NSPasteboard.generalPasteboard()
    # 文件（file:// URL 文本行，macOS 标准文件粘贴格式）
    try:
        file_text = pb.stringForType_(NSPasteboardTypeFileURL)
        if file_text:
            from urllib.parse import urlparse, unquote
            paths = []
            for line in file_text.splitlines():
                line = line.strip()
                if not line:
                    continue
                if line.startswith("file://"):
                    paths.append(unquote(urlparse(line).path))
                else:
                    paths.append(line)
            if paths:
                return ("file", paths)
    except Exception:
        pass

    # 图片（PNG 优先，TIFF 兜底）
    if HAS_PIL:
        try:
            raw = pb.dataForType_(NSPasteboardTypePNG)
            if raw is None:
                raw = pb.dataForType_(NSPasteboardTypeTIFF)
            if raw:
                import io
                from PIL import Image
                img = Image.open(io.BytesIO(bytes(raw)))
                img.load()
                return ("image", img)
        except Exception:
            pass

    # 文本
    try:
        text = pb.stringForType_(NSPasteboardTypeString)
        if text and text.strip():
            return ("text", text)
    except Exception:
        pass
    return (None, None)


def get_clipboard_content():
    """Returns (type, data) tuple.
    type is 'text', 'image', 'file', or None.
    data is: str for text, PIL.Image for image, list[str] for files.
    """
    if IS_MAC:
        return _mac_get_clipboard_content()

    if HAS_PIL and (user32.IsClipboardFormatAvailable(CF_DIB)
                    or user32.IsClipboardFormatAvailable(2)          # CF_BITMAP
                    or user32.IsClipboardFormatAvailable(CF_HDROP)):
        from PIL import Image, ImageGrab   # 懒加载：仅图片/文件拖放场景才载入 PIL
        result = ImageGrab.grabclipboard()
        if isinstance(result, Image.Image):
            return ("image", result)
        if isinstance(result, list):
            return ("file", result)

    # 压缩包/压缩文件夹内部复制：无 CF_HDROP，走 FileGroupDescriptor 物化
    try:
        fgd_files = materialize_fgd_files()
    except Exception:
        fgd_files = None
    if fgd_files:
        return ("file", fgd_files)

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
    if IS_MAC:
        try:
            import io
            from AppKit import NSPasteboard, NSPasteboardTypePNG
            buf = io.BytesIO()
            pil_image.convert("RGB").save(buf, format="PNG")
            pb = NSPasteboard.generalPasteboard()
            pb.clearContents()
            pb.setData_forType_(buf.getvalue(), NSPasteboardTypePNG)
            return True
        except Exception:
            return False

    from PIL import Image
    img = pil_image.convert("RGB")
    width, height = img.size
    row_size = ((width * 3 + 3) // 4) * 4

    # CF_DIB 24 位像素字节序为 BGR，PIL 的 RGB 需交换通道，否则粘贴后红蓝互换
    r, g, b = img.split()
    bgr = Image.merge("RGB", (b, g, r))
    flipped = bgr.transpose(Image.FLIP_TOP_BOTTOM)
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
    if IS_MAC:
        try:
            from AppKit import NSPasteboard, NSURL
            urls = [NSURL.fileURLWithPath_(os.path.abspath(p)) for p in file_paths]
            pb = NSPasteboard.generalPasteboard()
            pb.clearContents()
            pb.writeObjects_(urls)
            return True
        except Exception:
            return False

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
        self._snapshots = None            # 懒加载：首次访问时才读盘，降低常驻内存
        self._lock = threading.Lock()
        self._self_copy_time = 0.0      # 应用内复制时间戳（防重复收录）
        self._init_empty()
        self._load()

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

    def _ensure_snapshots(self):
        """懒加载：首次访问时才从磁盘读取快照列表。"""
        if self._snapshots is None:
            self._load_snapshots()
        return self._snapshots

    def save_snapshot(self, description):
        snap = {
            "id": uuid.uuid4().hex[:12],
            "desc": description,
            "time": datetime.now().isoformat(),
            "state": copy.deepcopy(self.categories),
        }
        self._ensure_snapshots().append(snap)
        self._save_snapshots()
        return snap

    def get_snapshots(self):
        return list(self._ensure_snapshots())

    def restore_snapshot(self, snapshot_id):
        for snap in self._ensure_snapshots():
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
            from PIL import Image
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

    @staticmethod
    def _norm_paths(entry):
        paths = entry.get("file_paths", []) or []
        if isinstance(paths, str):
            paths = [paths]
        return [p for p in paths if isinstance(p, str)]

    @staticmethod
    def file_entry_missing(entry):
        """文件条目所有路径均不存在时视为已失效；无路径信息时无法判断，返回 False。"""
        paths = ClipboardStore._norm_paths(entry)
        if not paths:
            return False
        return not any(os.path.exists(p) for p in paths)

    def purge_missing_files(self):
        """一键清理失效文件条目（仅清理有路径且全部不存在的），返回清理数量。"""
        n = 0
        with self._lock:
            cat = self.categories.get("file")
            if cat:
                keep = []
                for e in cat["entries"]:
                    paths = self._norm_paths(e)
                    if paths and not any(os.path.exists(p) for p in paths):
                        n += 1
                    else:
                        keep.append(e)
                cat["entries"] = keep
                if n:
                    self._save()
        return n

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
        # 隐私免记录模式：开启后剪贴板内容不入库
        self.privacy_mode = False

    # ---- public API ----

    def stop(self):
        self._running = False
        if IS_WIN and self._use_events and self._thread_id:
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
        if IS_WIN:
            try:
                self._run_event_loop()
            except Exception:
                # 事件监听不可用时回退轮询
                if self._running:
                    self._run_polling()
        else:
            # macOS：无 Win32 事件，直接轮询 NSPasteboard
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
        # 隐私免记录模式：敏感内容（如密码）不入库
        if getattr(self, "privacy_mode", False):
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
                    from PIL import Image
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
                try:
                    ok = self._process_clipboard()
                except Exception:
                    ok = True  # 单条异常丢弃，不中断监听
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
        mac_pb = None
        mac_last = None
        if IS_MAC:
            try:
                from AppKit import NSPasteboard
                mac_pb = NSPasteboard.generalPasteboard()
                mac_last = mac_pb.changeCount()
            except Exception:
                mac_pb = None
        while self._running:
            try:
                if mac_pb is not None:
                    cc = mac_pb.changeCount()
                    if cc == mac_last:
                        time.sleep(POLL_INTERVAL)
                        continue
                    mac_last = cc
                self._process_clipboard()
            except Exception:
                pass
            time.sleep(POLL_INTERVAL)


# ===========================================================================
# 图标路径 & 系统托盘（pystray）
# ===========================================================================

def get_icon_path():
    """跨路径兼容：返回 YouBoard.ico 的绝对路径。
    - PyInstaller 打包后：读取 _MEIPASS 临时解压目录
    - 本地脚本调试：读取脚本同目录下的 YouBoard.ico
    """
    base = getattr(sys, "_MEIPASS", None)
    if base:
        p = os.path.join(base, "YouBoard.ico")
        if os.path.exists(p):
            return p
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.path.join(here, "YouBoard.ico"),
                 os.path.join(here, "You.ico"),
                 os.path.join(os.path.dirname(here), "logo", "YouBoard.ico")):
        if os.path.exists(cand):
            return cand
    return None


def get_app_icon():
    """返回 PIL.Image 图标对象，供 pystray 托盘 / tkinter 窗口统一使用。
    区分本地脚本调试 / PyInstaller 打包 exe 两种环境。
    """
    if not HAS_PIL:
        return None
    from PIL import Image
    ico_path = get_icon_path()
    if ico_path:
        try:
            img = Image.open(ico_path)
            # 强制转为 RGBA 64x64，确保 pystray 兼容
            img = img.convert("RGBA")
            if img.size != (64, 64):
                img = img.resize((64, 64), Image.LANCZOS)
            return img
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
        """在后台线程启动托盘图标，强制传入 YouBoard.ico 图片对象。"""
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
