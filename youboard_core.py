#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
YouBoard Core — Win32 helpers, type detection, data store, monitor, snapshots,
config persistence and Windows autostart (registry).
"""

import copy
import ctypes
import base64
import hashlib
import json
import os
import re
import shutil
import struct
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta

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
# 大文本正文外置目录：超大内容不再塞进历史 JSON，避免每次复制都重写整份文件
CONTENT_DIR = os.path.join(_BASE_DIR, "content")
# 密库（3.3.1）：用户主动放进去的私密数据（密码 / 密钥 / 备注）单独落盘，
# 只由用户手动维护，不进剪贴板历史、不进手机传输、不进云同步
VAULT_FILE = os.path.join(_BASE_DIR, "youboard_vault.json")
# 密库带进来的图片文件（历史里的图片是磁盘文件，密库复制一份自己保管）
VAULT_FILES_DIR = os.path.join(_BASE_DIR, "vault_files")
# 密库单个字段的长度上限（名称 / 正文）
MAX_VAULT_FIELD_LEN = 4096
# 密库条目类型：和剪贴板历史一致的四类
VAULT_TYPES = ("text", "image", "file", "url")
# 单个物化文件大小上限：超过则跳过，避免一次性占用过大内存
MAX_FGD_FILE_SIZE = 500 * 1024 * 1024
# 超过该长度的文本改为「正文外置 + 历史只留头部」：历史文件保持小巧，复制不再卡顿
LARGE_TEXT_THRESHOLD = 256 * 1024
# 外置正文在历史里保留的头部长度（用于列表预览与关键词搜索）
CONTENT_HEAD_CHARS = 4096
# 历史写盘防抖：连续复制合并成一次落盘，且写盘放到后台线程，不阻塞界面
SAVE_DEBOUNCE_SEC = 0.8
# 关键词搜索时，外置正文超过该大小就不再逐字读取（避免搜索卡顿）
SEARCH_READ_LIMIT = 8 * 1024 * 1024
MAX_ENTRIES = None          # 无上限：不限制历史记录条数
# 标签 / 收藏（3.2.7）：单条记录最多挂多少个标签、单个标签最长多少字符
MAX_TAGS_PER_ENTRY = 20
MAX_TAG_LEN = 24
POLL_INTERVAL = 0.5
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
URL_PATTERN = re.compile(r'https?://\S+|www\.\S+')
# 缓存回收保护时间：刚写入的文件先保留，避免与并发新增记录竞争
CACHE_GC_MIN_AGE = 300
_IMAGE_CACHE_NAME = re.compile(r"^(?:thumb_)?[0-9a-f]{64}\.png$", re.I)

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
# 历史加密（对称加密落盘，防止剪贴板历史被直接明文读取）
# 密钥保存在数据目录 youboard.key；丢失密钥后历史无法解密（隐私设计）。
# ===========================================================================

KEY_FILE = os.path.join(_BASE_DIR, "youboard.key")

try:
    from cryptography.fernet import Fernet
    _HAS_FERNET = True
except Exception:
    _HAS_FERNET = False


def _load_or_create_key():
    """读取历史加密密钥；不存在则生成并保存到数据目录。"""
    if not _HAS_FERNET:
        return None
    try:
        if os.path.exists(KEY_FILE):
            with open(KEY_FILE, "rb") as f:
                key = f.read().strip()
            if key:
                return key
    except Exception:
        pass
    try:
        key = Fernet.generate_key()
        with open(KEY_FILE, "wb") as f:
            f.write(key)
        try:
            os.chmod(KEY_FILE, 0o600)
        except Exception:
            pass
        return key
    except Exception:
        return None


def _encrypt_data(raw):
    """加密字节串；未启用加密或加密失败时原样返回。"""
    if not _HAS_FERNET or not raw:
        return raw
    try:
        return Fernet(_load_or_create_key()).encrypt(raw)
    except Exception:
        return raw


def _decrypt_data(blob):
    """解密字节串；旧版明文内容（非 gAAAA 开头）原样返回。"""
    if not _HAS_FERNET or not blob:
        return blob
    if not blob.startswith(b"gAAAA"):
        return blob
    try:
        return Fernet(_load_or_create_key()).decrypt(blob)
    except Exception:
        return blob


def _atomic_write(path, data):
    """Write bytes to a temporary file, then atomically replace the target."""
    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(3):
            try:
                os.replace(tmp_path, path)
                break
            except PermissionError:
                if attempt == 2:
                    raise
                time.sleep(0.06)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


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
        raw = json.dumps(cfg, ensure_ascii=False, indent=2).encode("utf-8")
        _atomic_write(CONFIG_FILE, raw)
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
    # 其它常见的"复制文件"格式（不同程序发布文件时用的格式名不一样）
    CF_FILENAMEW = user32.RegisterClipboardFormatW("FileNameW")
    CF_FILENAME = user32.RegisterClipboardFormatW("FileName")
    CF_SHELLIDLIST = user32.RegisterClipboardFormatW("Shell IDList Array")
    CF_PREFERREDDROPEFFECT = user32.RegisterClipboardFormatW("Preferred DropEffect")


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


def get_clipboard_content(known_file_names=None):
    """Returns (type, data) tuple.
    type is 'text', 'image', 'file', or None.
    data is: str for text, PIL.Image for image, list[str] for files.

    known_file_names：本工具已记录过的文件名集合（可选）。用于识别
    "复制文件时剪贴板里附带的那段文件名文本"，避免它被当成普通文本收录。
    """
    if IS_MAC:
        return _mac_get_clipboard_content()

    file_intent = _clipboard_file_intent()
    has_hdrop = bool(user32.IsClipboardFormatAvailable(CF_HDROP))
    if HAS_PIL and (user32.IsClipboardFormatAvailable(CF_DIB)
                    or user32.IsClipboardFormatAvailable(2)          # CF_BITMAP
                    or has_hdrop):
        from PIL import Image, ImageGrab   # 懒加载：仅图片/文件拖放场景才载入 PIL
        result = ImageGrab.grabclipboard()
        if isinstance(result, Image.Image):
            return ("image", result)
        if isinstance(result, list):
            return ("file", result)

    # 剪贴板里带着文件列表（CF_HDROP）时，绝不能再把它当文本收录：
    # 否则"复制一个文件"会额外生成一条内容是文件名的文本记录。
    # ImageGrab 偶尔取不到文件列表（硬件/虚拟文件、剪贴板被占用等），
    # 这里自己解析一次 CF_HDROP 兜底。
    if has_hdrop:
        files = _read_hdrop_files()
        if files:
            return ("file", files)
        return (None, None)

    # 压缩包/压缩文件夹内部复制：无 CF_HDROP，走 FileGroupDescriptor 物化
    try:
        fgd_files = materialize_fgd_files()
    except Exception:
        fgd_files = None
    if fgd_files:
        return ("file", fgd_files)

    # 剪贴板里带有"文件类"格式（FileGroupDescriptorW / FileNameW / Shell ID List
    # Array / Preferred DropEffect 等）却拿不到路径时，同样不能退化成文本，
    # 否则资源管理器/压缩软件/网盘客户端复制文件时会多出一条文件名文本记录。
    if file_intent:
        return ("file", [])

    try:
        text = pyperclip.paste()
        if text and text.strip():
            files = _text_as_files(text, known_file_names)
            if files is not None:
                # 是文件路径 → 按文件收录；是已知文件名 → 不收录（复制文件的附带文本）
                return ("file", files)
            return ("text", text)
    except Exception:
        pass

    return (None, None)


_MEDIA_LIKE_EXTS = {
    # 媒体（视频/音频/图片/设计稿）
    ".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv", ".webm", ".m4v", ".ts",
    ".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg", ".wma",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff",
    ".psd", ".psb", ".ai", ".eps", ".svg", ".ico", ".raw", ".cr2", ".nef",
    # 压缩包 / 镜像 / 可执行
    ".zip", ".rar", ".7z", ".z01", ".z02", ".tar", ".gz", ".bz2", ".xz",
    ".iso", ".cab", ".exe", ".msi", ".dll", ".apk", ".dmg", ".pkg",
}


def _looks_like_file_name(name):
    """名字看起来是不是"文件名"：有主名 + 扩展名，且不含路径分隔符/非法字符。"""
    if not name:
        return False
    if any(ch in name for ch in '\\/:*?"<>|'):
        return False
    root, ext = os.path.splitext(name)
    return bool(root.strip()) and 2 <= len(ext) <= 9 and ext[1:].isalnum()


def _text_as_files(text, known_file_names=None):
    """判断剪贴板文本是不是"文件路径 / 文件名列表"。

    返回 路径列表（按文件收录） / 空列表（判定为复制文件的附带文本，不收录）
    / None（普通文本，按文本收录）。
    """
    try:
        lines = [ln.strip() for ln in str(text).splitlines()]
        lines = [ln for ln in lines if ln]
        if not lines or len(lines) > 50:
            return None
        # 1) 每一行都是真实存在的文件 → 这是"复制文件路径"
        if all(os.path.isfile(ln) for ln in lines):
            return lines
        # 2) 内容是"一个或多个文件名"（可能用 " | " 连接、名字里可以有空格）
        #    → 资源管理器/压缩软件/网盘客户端复制文件时附带的那段文件名文本，
        #    别把它收进文本分类。
        parts = []
        for ln in lines:
            parts.extend(p.strip() for p in ln.split("|") if p.strip())
        if parts:
            names = [os.path.basename(p) for p in parts]
            if all(_looks_like_file_name(n) for n in names):
                known = known_file_names or set()
                if any(n in known for n in names):
                    return []
                # 没在历史里出现过，但扩展名都是"媒体/压缩包/可执行"这类
                # 基本不可能出现在普通文字里的类型 → 同样判定为文件
                if all(os.path.splitext(n)[1].lower() in _MEDIA_LIKE_EXTS
                       for n in names):
                    return []
    except Exception:
        pass
    return None


def _clipboard_file_intent():
    """剪贴板里是否带有"复制文件"的迹象（除 CF_HDROP 之外的常见文件格式）。"""
    if not IS_WIN:
        return False
    try:
        if user32.IsClipboardFormatAvailable(CF_HDROP):
            return True
        for fmt in (CF_FILEGROUPDESCRIPTORW, CF_FILECONTENTS,
                    CF_FILENAMEW, CF_FILENAME,
                    CF_SHELLIDLIST, CF_PREFERREDDROPEFFECT):
            if fmt and user32.IsClipboardFormatAvailable(fmt):
                return True
    except Exception:
        pass
    return False


def _read_hdrop_files():
    """直接解析剪贴板里的 CF_HDROP 文件列表（不依赖 ImageGrab）。"""
    if not IS_WIN:
        return []
    try:
        if not user32.IsClipboardFormatAvailable(CF_HDROP):
            return []
        if not user32.OpenClipboard(None):
            return []
        try:
            hdrop = user32.GetClipboardData(CF_HDROP)
            if not hdrop:
                return []
            shell32 = ctypes.windll.shell32
            # wFlags = 0xFFFFFFFF 时返回文件个数
            count = int(shell32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0))
            out = []
            for i in range(count):
                need = int(shell32.DragQueryFileW(hdrop, i, None, 0))
                if need <= 0:
                    continue
                buf = ctypes.create_unicode_buffer(need + 1)
                shell32.DragQueryFileW(hdrop, i, buf, need + 1)
                if buf.value:
                    out.append(buf.value)
            return out
        finally:
            user32.CloseClipboard()
    except Exception:
        return []


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

# ---- 大文本正文外置 -------------------------------------------------------
# 背景：超大文本若整段塞进历史 JSON，每次复制都要把整份历史重新序列化 + 加密，
# 几十 MB 的内容就会让界面明显卡顿。解决办法是把正文单独写成一个文件，
# 历史里只留「头部片段 + 正文引用 + 长度」，复制时再按需读回全文。


def content_file_path(ref):
    """把正文引用（文件名或绝对路径）解析成实际文件路径。"""
    if not ref:
        return ""
    ref = str(ref)
    if os.path.isabs(ref):
        return ref
    return os.path.join(CONTENT_DIR, os.path.basename(ref))


def write_external_content(entry_hash, text):
    """把正文写到 content/<hash>.txt，返回引用名；失败返回空串。"""
    if not entry_hash:
        return ""
    try:
        os.makedirs(CONTENT_DIR, exist_ok=True)
        name = f"{entry_hash}.txt"
        _atomic_write(os.path.join(CONTENT_DIR, name),
                      text.encode("utf-8", "replace"))
        return name
    except (IOError, OSError):
        return ""


def read_external_content(ref):
    """读回外置正文；读不到返回空串。"""
    path = content_file_path(ref)
    if not path or not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except (IOError, OSError, UnicodeDecodeError):
        return ""


def read_external_head(ref, limit):
    """只读外置正文的前 limit 个字符（大内容预览/发送用，避免整文件读入）。"""
    path = content_file_path(ref)
    if not path or not os.path.exists(path) or limit <= 0:
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(limit)
    except (IOError, OSError, UnicodeDecodeError):
        return ""


def entry_full_text(entry):
    """取一条记录的完整文本：外置记录读正文文件，普通记录直接返回内容。"""
    if not isinstance(entry, dict):
        return ""
    ref = entry.get("content_ref")
    if ref:
        body = read_external_content(ref)
        if body:
            return body
    return entry.get("content", "") or ""


def normalize_tag(name):
    """标签归一化：去首尾空白与开头的 #、把连续空白压成一个空格、限长。

    空标签（只有空白 / # 的输入）返回 ""。
    """
    if name is None:
        return ""
    text = str(name).replace("\u3000", " ").strip()
    while text.startswith("#"):
        text = text[1:].lstrip()
    text = re.sub(r"\s+", " ", text)
    if len(text) > MAX_TAG_LEN:
        text = text[:MAX_TAG_LEN]
    return text


def normalize_tags(tags):
    """标签列表归一化：去空、去重（不区分大小写，保留先出现的写法）、限数量。"""
    out, seen = [], set()
    for raw in (tags or []):
        tag = normalize_tag(raw)
        if not tag:
            continue
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(tag)
        if len(out) >= MAX_TAGS_PER_ENTRY:
            break
    return out


def entry_tags(entry):
    """读一条记录的标签列表（老数据没有该字段时返回空列表）。"""
    if not isinstance(entry, dict):
        return []
    tags = entry.get("tags")
    return list(tags) if isinstance(tags, list) else []


def entry_is_fav(entry):
    """这条记录是否已收藏。"""
    return bool(isinstance(entry, dict) and entry.get("fav"))


# ---------------------------------------------------------------------------
# 密库主密码（v3.3.4）
#
# 以前密库和剪贴板历史共用一把自动生成的 youboard.key：能防"文件被单独拷走 /
# 云盘泄露"，但挡不住"能坐在这台电脑前的人"——打开密库直接就能看。
# 现在可以给密库单独设一把用户主密码：PBKDF2-HMAC-SHA256 从主密码派生独立密钥，
# 密库文件（以及 vault_files/ 里的图片）都用这把钥匙加密，和 youboard.key 无关；
# 打开密库要解锁，闲置 / 关窗自动锁定；主密码只在本机校验、不落盘。
# 边界要讲清楚：本地加密防的是"文件被拷走、云盘泄露、旁人翻程序"，挡不住本机上
# 正在运行的恶意程序（它可以在你输入主密码时截键盘）。
# ---------------------------------------------------------------------------

VAULT_ENVELOPE_FORMAT = "youboard-vault"
VAULT_KDF = "pbkdf2-hmac-sha256"
VAULT_KDF_ITER = 200_000
VAULT_MIN_PW_LEN = 6
_VAULT_MAGIC = b"gAAAA"                 # Fernet 令牌前缀：识别"加密过的文件"
_VAULT_SESSION = {"key": None}          # 已解锁时内存里的派生密钥（锁定即清空）


def vault_session_key():
    """当前解锁会话的主密码派生密钥（没解锁则为 None）。"""
    return _VAULT_SESSION.get("key")


def vault_open_cache_dir():
    """解锁后解密出来的图片副本放这里（锁定时整目录删掉）。"""
    return os.path.join(FILE_CACHE_DIR, "vault_open")


def vault_clear_open_cache():
    """锁定 / 退出时把"解密出来给人看"的图片副本清干净。"""
    try:
        shutil.rmtree(vault_open_cache_dir(), ignore_errors=True)
    except Exception:
        pass


def derive_vault_key(password, salt, iterations=VAULT_KDF_ITER):
    """用 PBKDF2 从主密码派生一把独立的 Fernet 密钥（和 youboard.key 无关）。"""
    raw = hashlib.pbkdf2_hmac("sha256", str(password).encode("utf-8"),
                              salt, int(iterations), dklen=32)
    return base64.urlsafe_b64encode(raw)


def _vault_file_encrypted(path):
    try:
        with open(path, "rb") as f:
            return f.read(5) == _VAULT_MAGIC
    except OSError:
        return False


def _vault_token_bytes(value):
    """信封里的 Fernet 令牌：存成 ASCII 字符串（JSON 装不下 bytes）。"""
    if isinstance(value, bytes):
        return value
    return str(value or "").encode("ascii", "ignore")


def _vault_token_text(value):
    if isinstance(value, bytes):
        return value.decode("ascii", "ignore")
    return str(value or "")


def _parse_vault_envelope(blob):
    """认出"带主密码"的密库文件；旧格式（youboard.key 加密或明文）返回 None。"""
    if not blob or blob[:1] != b"{":
        return None
    try:
        env = json.loads(blob.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if (not isinstance(env, dict)
            or env.get("format") != VAULT_ENVELOPE_FORMAT
            or not env.get("salt") or not env.get("data")):
        return None
    return env


def vault_image_path(entry):
    """密库条目里带的图片文件的完整路径（没有则返回空串）。

    v3.3.4 起密库可以设主密码：设了以后 vault_files/ 里的图片是加密存的，
    解锁状态下这里会先解密出一份副本（file_cache/vault_open/）再返回，
    查看 / AI / 导出都用那一份；锁定时只返回原路径（不泄露内容）。
    """
    name = str((entry or {}).get("image") or "")
    if not name:
        return ""
    path = os.path.join(VAULT_FILES_DIR, os.path.basename(name))
    key = vault_session_key()
    if not key or not _vault_file_encrypted(path):
        return path
    try:
        out_dir = vault_open_cache_dir()
        os.makedirs(out_dir, exist_ok=True)
        out = os.path.join(out_dir, os.path.basename(name))
        if (not os.path.exists(out)
                or os.path.getmtime(out) < os.path.getmtime(path)):
            with open(path, "rb") as f:
                data = Fernet(key).decrypt(f.read())
            _atomic_write(out, data)
        return out
    except Exception:
        return path


class VaultStore:
    """密库存储：用户主动放进去的私密内容。

    条目结构和剪贴板历史同构（文本 / 图片 / 文件 / 网址 + 可选名称）：
    - 「名称」可以不填：不填时界面直接按内容显示；
    - 文本、网址存正文；图片把文件复制进 vault_files/；文件存路径清单；
    - 单独一个文件（youboard_vault.json），Fernet 加密 + 原子写入；
    - 只由用户在密库窗口里手动增删改，剪贴板监控不会往里写；
    - 手机传输、云同步、快照回滚都只认剪贴板历史，密库天然不参与。
    """

    def __init__(self, path=VAULT_FILE, password=None):
        self.path = path
        self._lock = threading.Lock()
        self._entries = []
        self._protected = False      # 设过主密码
        self._locked = False         # 设了主密码但还没解锁
        self._meta = {}              # 主密码信封（salt / iter / check / data）
        self._wrong_password = False
        self._load(password)

    # ---- persistence ----

    @staticmethod
    def _norm(item):
        """归一化一条密库记录；没有任何内容时视为无效（返回 None）。

        兼容第一版密库（title / username / secret / url / note 那套表单）：
        老数据统一迁移成"文本"条目，名称取原「名称」，正文取密码 + 备注 + 链接，
        内容不会因为换结构而丢掉。
        """
        if not isinstance(item, dict):
            return None

        def field(key, limit=MAX_VAULT_FIELD_LEN):
            val = item.get(key, "")
            if val is None:
                val = ""
            return str(val).strip()[:limit]

        now = datetime.now().isoformat(timespec="seconds")
        kind = str(item.get("type") or "")
        if kind not in VAULT_TYPES:
            # 老版密库：拆成"名称 + 正文"
            parts = []
            if field("secret"):
                parts.append(str(item.get("secret")).strip())
            if field("note"):
                parts.append(str(item.get("note")).strip())
            if field("url") and not field("secret"):
                parts.append(str(item.get("url")).strip())
            content = "\n".join(p for p in parts if p)
            name = field("title") or field("username")
            kind = "url" if (content and len(content.splitlines()) == 1
                             and URL_PATTERN.fullmatch(content)) else "text"
            if not content and not name:
                return None
            return {"id": str(item.get("id") or uuid.uuid4().hex),
                    "type": kind, "name": name, "content": content,
                    "paths": [], "image": "", "size": 0,
                    "ts": "", "tags": [], "fav": False, "pinned": False,
                    "created": str(item.get("created") or now),
                    "updated": str(item.get("updated") or now)}

        content = field("content")
        name = field("name", 200)
        paths = [str(p) for p in (item.get("paths") or [])
                 if str(p or "").strip()][:50]
        image = os.path.basename(str(item.get("image") or ""))
        if kind in ("text", "url") and not content:
            return None
        if kind == "image" and not image:
            return None
        if kind == "file" and not paths:
            return None
        try:
            size = int(item.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        # 3.3.2：从历史"移入"的记录要记住它原来的时间 / 标签 / 收藏 / 置顶，
        # 这样"移出密库"时能按当初的时间归位，标记也不会丢。
        tags = normalize_tags(item.get("tags") or [])
        return {"id": str(item.get("id") or uuid.uuid4().hex),
                "type": kind, "name": name, "content": content,
                "paths": paths, "image": image, "size": size,
                "ts": str(item.get("ts") or ""),
                "tags": tags,
                "fav": bool(item.get("fav")),
                "pinned": bool(item.get("pinned")),
                "created": str(item.get("created") or now),
                "updated": str(item.get("updated") or now)}

    def _load(self, password=None):
        """读盘：带主密码的信封 → 需要解锁；旧格式 → 用 youboard.key 解（老行为）。"""
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "rb") as f:
                blob = f.read()
        except (IOError, OSError):
            return
        env = _parse_vault_envelope(blob)
        if env is not None:
            self._protected = True
            self._meta = env
            self._locked = True
            # 上次退出时可能留下解密出来的图片副本，启动就清掉
            vault_clear_open_cache()
            if password:
                self.unlock(password)
            return
        try:
            raw = _decrypt_data(blob)
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, IOError, OSError):
            return
        if not isinstance(data, dict):
            return
        self._entries = self._clean_entries(data.get("entries"))

    def _clean_entries(self, items):
        if not isinstance(items, list):
            return []
        clean = []
        for item in items:
            entry = self._norm(item)
            if entry is not None:
                clean.append(entry)
        return clean

    # ---- 主密码 ----

    def is_protected(self):
        """密库是否设了主密码。"""
        return bool(self._protected)

    def is_locked(self):
        """设了主密码但还没解锁。"""
        return bool(self._protected and self._locked)

    def wrong_password(self):
        """上一次解锁是不是密码不对（给界面提示用）。"""
        return bool(self._wrong_password)

    def kdf_iterations(self):
        try:
            return max(1000, int(self._meta.get("iter") or VAULT_KDF_ITER))
        except (TypeError, ValueError):
            return VAULT_KDF_ITER

    def _derive(self, password):
        salt = base64.b64decode(self._meta.get("salt") or b"")
        return derive_vault_key(password, salt, self.kdf_iterations())

    def verify_password(self, password):
        """只校验主密码对不对，不动状态。"""
        if not self._protected:
            return True
        try:
            Fernet(self._derive(password)).decrypt(
                _vault_token_bytes(self._meta.get("check")))
            return True
        except Exception:
            return False

    def unlock(self, password):
        """用主密码解锁；成功返回 True。"""
        if not self._protected:
            return True
        if not self.verify_password(password):
            self._wrong_password = True
            return False
        key = self._derive(password)
        try:
            raw = Fernet(key).decrypt(
                _vault_token_bytes(self._meta.get("data")))
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            self._wrong_password = True
            return False
        with self._lock:
            self._entries = self._clean_entries(
                (data or {}).get("entries") if isinstance(data, dict) else None)
        _VAULT_SESSION["key"] = key
        vault_clear_open_cache()
        self._locked = False
        self._wrong_password = False
        return True

    def lock(self):
        """锁定：内存里的明文也一起丢掉，解密副本清干净。"""
        self._wrong_password = False
        if not self._protected:
            return
        with self._lock:
            self._entries = []
        _VAULT_SESSION["key"] = None
        vault_clear_open_cache()
        self._locked = True

    def set_master_password(self, password):
        """第一次设置主密码：内容（含图片）改用主密码派生密钥加密。"""
        password = str(password or "")
        if self.is_locked() or len(password) < VAULT_MIN_PW_LEN:
            return False
        salt = os.urandom(16)
        key = derive_vault_key(password, salt)
        self._rekey_files(None, key)
        self._protected = True
        self._locked = False
        self._meta = {
            "salt": base64.b64encode(salt).decode("ascii"),
            "iter": VAULT_KDF_ITER,
            "check": _vault_token_text(Fernet(key).encrypt(b"youboard-vault")),
        }
        _VAULT_SESSION["key"] = key
        return bool(self._save())

    def change_master_password(self, old_password, new_password):
        """换主密码；new_password 为空 = 取消主密码（改回 youboard.key 加密）。"""
        if not self._protected or self.is_locked():
            return False
        if not self.verify_password(old_password):
            self._wrong_password = True
            return False
        old_key = _VAULT_SESSION.get("key") or self._derive(old_password)
        new_password = str(new_password or "")
        if not new_password:
            return self.remove_master_password(old_password)
        if len(new_password) < VAULT_MIN_PW_LEN:
            return False
        salt = os.urandom(16)
        new_key = derive_vault_key(new_password, salt)
        self._rekey_files(old_key, new_key)
        self._meta = {
            "salt": base64.b64encode(salt).decode("ascii"),
            "iter": VAULT_KDF_ITER,
            "check": _vault_token_text(
                Fernet(new_key).encrypt(b"youboard-vault")),
        }
        _VAULT_SESSION["key"] = new_key
        self._wrong_password = False
        return bool(self._save())

    def remove_master_password(self, password):
        """取消主密码：图片解回明文、清单改回 youboard.key 加密。"""
        if not self._protected or self.is_locked():
            return False
        if not self.verify_password(password):
            self._wrong_password = True
            return False
        old_key = _VAULT_SESSION.get("key") or self._derive(password)
        return self._drop_master_password(old_key)

    def disable_master_password(self):
        """已经解锁时直接关闭主密码（界面上的「关闭密码」按钮，不用再输一次）。"""
        if not self._protected or self.is_locked():
            return False
        key = _VAULT_SESSION.get("key")
        if key is None:
            return False
        return self._drop_master_password(key)

    def _drop_master_password(self, old_key):
        """取消主密码的公共部分：图片解回明文、清单改回 youboard.key 加密。"""
        self._rekey_files(old_key, None)
        self._protected = False
        self._locked = False
        self._meta = {}
        _VAULT_SESSION["key"] = None
        self._wrong_password = False
        return bool(self._save())

    def _rekey_files(self, old_key, new_key):
        """vault_files/ 里的图片跟着换钥匙（None = 明文存放）。"""
        try:
            names = os.listdir(VAULT_FILES_DIR)
        except OSError:
            return
        for name in names:
            path = os.path.join(VAULT_FILES_DIR, name)
            if not os.path.isfile(path):
                continue
            try:
                with open(path, "rb") as f:
                    blob = f.read()
                if blob.startswith(_VAULT_MAGIC):
                    if old_key is None:
                        continue            # 不知道旧钥匙，别把人家文件弄坏
                    data = Fernet(old_key).decrypt(blob)
                else:
                    data = blob
                if new_key is None:
                    _atomic_write(path, data)
                else:
                    _atomic_write(path, Fernet(new_key).encrypt(data))
            except Exception:
                continue

    def _save(self):
        """加密后原子落盘；返回是否写成功（失败时内存里的改动仍然生效）。"""
        if self.is_locked():
            # 锁着的时候内存里没有内容，写下去等于把密库清空 —— 绝对不写
            return False
        with self._lock:
            payload = {"version": 1, "entries": self._entries}
            raw = json.dumps(payload, ensure_ascii=False,
                             indent=2).encode("utf-8")
        try:
            if self._protected:
                key = _VAULT_SESSION.get("key")
                if key is None:
                    return False
                env = {"format": VAULT_ENVELOPE_FORMAT, "version": 2,
                       "kdf": VAULT_KDF, "iter": self.kdf_iterations(),
                       "salt": self._meta.get("salt"),
                       "check": self._meta.get("check"),
                       "data": _vault_token_text(Fernet(key).encrypt(raw))}
                self._meta.update(env)
                _atomic_write(self.path, json.dumps(
                    env, ensure_ascii=False, indent=2).encode("utf-8"))
            else:
                _atomic_write(self.path, _encrypt_data(raw))
            return True
        except (IOError, OSError, TypeError, ValueError):
            return False

    def flush(self):
        return self._save()

    # ---- query ----

    def entries(self):
        """全部记录，按更新时间倒序（最近改过的在最上面）。"""
        with self._lock:
            items = [dict(e) for e in self._entries]
        items.sort(key=lambda e: (e.get("updated") or "", e.get("created") or ""),
                   reverse=True)
        return items

    def count(self):
        with self._lock:
            return len(self._entries)

    def get(self, uid):
        with self._lock:
            for e in self._entries:
                if e.get("id") == uid:
                    return dict(e)
        return None

    def search(self, keyword, kind=None):
        """按类型 + 关键词筛选（关键词匹配名称 / 正文 / 文件路径）。"""
        kw = (keyword or "").strip().lower()
        out = []
        for e in self.entries():
            if kind and kind != "all" and e.get("type") != kind:
                continue
            if not kw:
                out.append(e)
                continue
            hay = " ".join((str(e.get("name", "")), str(e.get("content", "")),
                            os.path.basename(vault_image_path(e)),
                            " ".join(os.path.basename(p)
                                     for p in (e.get("paths") or [])))).lower()
            if kw in hay:
                out.append(e)
        return out

    def counts(self):
        """各分类条数（给密库窗口的分类胶囊显示数量用）。"""
        out = {"all": 0, "text": 0, "image": 0, "file": 0, "url": 0}
        for e in self.entries():
            out["all"] += 1
            kind = e.get("type")
            if kind in out:
                out[kind] += 1
        return out

    def store_image_file(self, src_path):
        """把一张图片复制进密库自己的目录，返回文件名（失败返回空串）。

        设了主密码就以加密形式落盘（解锁状态下查看会自动解密出副本）；
        锁着的时候不落盘，免得留下没人认领的孤儿文件。
        """
        try:
            if self.is_locked() or not src_path or not os.path.exists(src_path):
                return ""
            os.makedirs(VAULT_FILES_DIR, exist_ok=True)
            name = uuid.uuid4().hex + os.path.splitext(src_path)[1].lower()
            dest = os.path.join(VAULT_FILES_DIR, name)
            if self._protected:
                key = _VAULT_SESSION.get("key")
                if key is None:
                    return ""
                with open(src_path, "rb") as f:
                    data = f.read()
                _atomic_write(dest, Fernet(key).encrypt(data))
            else:
                shutil.copyfile(src_path, dest)
            return name
        except (IOError, OSError, shutil.Error):
            return ""

    # ---- mutate ----

    def add(self, kind="text", content="", paths=None, image="",
            name="", size=0, ts="", tags=None, fav=False, pinned=False):
        """新增一条；返回新记录（没有任何内容则不写入，返回 None）。

        ts / tags / fav / pinned 是"从历史移入"时带过来的来源信息：
        移出密库时按 ts 归位，标签与收藏也一起还回去。
        """
        if self.is_locked():
            return None
        now = datetime.now().isoformat(timespec="seconds")
        entry = self._norm({
            "id": uuid.uuid4().hex, "type": kind, "content": content,
            "paths": list(paths or []), "image": image, "name": name,
            "size": size, "created": now, "updated": now,
            "ts": ts, "tags": list(tags or []), "fav": fav, "pinned": pinned,
        })
        if entry is None:
            return None
        with self._lock:
            self._entries.append(entry)
        self._save()
        return dict(entry)

    def add_text(self, content, name=""):
        """便捷入口：按内容自动判定是「文本」还是「网址」。"""
        body = str(content or "")
        if not body.strip():
            return None
        kind = "url" if (len(body.splitlines()) == 1
                         and URL_PATTERN.fullmatch(body.strip())) else "text"
        return self.add(kind=kind, content=body, name=name)

    def rename(self, uid, name):
        """只改「名称」（留空 = 回到按内容显示）。"""
        if self.is_locked():
            return False
        changed = False
        with self._lock:
            for entry in self._entries:
                if entry.get("id") != uid:
                    continue
                entry["name"] = str(name or "").strip()[:200]
                entry["updated"] = datetime.now().isoformat(timespec="seconds")
                changed = True
                break
        if changed:
            self._save()
        return changed

    def update(self, uid, content=None, name=None):
        """改名称 / 改正文（图片、文件条目只改名称，内容靠重新加入）。"""
        if self.is_locked():
            return False
        changed = False
        with self._lock:
            for entry in self._entries:
                if entry.get("id") != uid:
                    continue
                if name is not None:
                    entry["name"] = str(name or "").strip()[:200]
                if content is not None and entry.get("type") in ("text", "url"):
                    body = str(content)
                    if not body.strip():
                        return False
                    entry["content"] = body[:MAX_VAULT_FIELD_LEN]
                    entry["type"] = ("url" if (len(body.splitlines()) == 1
                                               and URL_PATTERN.fullmatch(
                                                   body.strip()))
                                     else "text")
                entry["updated"] = datetime.now().isoformat(timespec="seconds")
                changed = True
                break
        if changed:
            self._save()
        return changed

    def delete(self, uid):
        """删除一条；如果是带图片的条目，把密库里的图片文件一起清掉。"""
        if self.is_locked():
            return False
        image = ""
        removed = False
        with self._lock:
            keep = []
            for entry in self._entries:
                if entry.get("id") == uid:
                    image = entry.get("image") or ""
                    removed = True
                    continue
                keep.append(entry)
            self._entries = keep
        if not removed:
            return False
        self._save()
        if image:
            try:
                path = os.path.join(VAULT_FILES_DIR, os.path.basename(image))
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass
        return removed

    def wipe(self):
        """清空密库（记录 + 图片文件 + 主密码）：忘记主密码时唯一出路。"""
        with self._lock:
            self._entries = []
        try:
            if os.path.exists(self.path):
                os.remove(self.path)
        except OSError:
            pass
        try:
            for name in os.listdir(VAULT_FILES_DIR):
                path = os.path.join(VAULT_FILES_DIR, name)
                if os.path.isfile(path):
                    os.remove(path)
        except OSError:
            pass
        self._protected = False
        self._locked = False
        self._meta = {}
        self._wrong_password = False
        _VAULT_SESSION["key"] = None
        vault_clear_open_cache()
        return True


class ClipboardStore:
    def __init__(self, path=HISTORY_FILE, max_entries=MAX_ENTRIES):
        self.path = path
        self.snapshots_path = SNAPSHOTS_FILE
        self.max_entries = max_entries
        self.categories = {}
        self._snapshots = None            # 懒加载：首次访问时才读盘，降低常驻内存
        self._lock = threading.Lock()
        self._self_copy_time = 0.0      # 应用内复制时间戳（防重复收录）
        # 写盘防抖 + 后台落盘：避免每次复制都同步重写整份历史
        self._save_timer = None
        self._save_timer_lock = threading.Lock()
        self._save_lock = threading.Lock()
        self._dirty = False
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
            with open(self.path, "rb") as f:
                raw = _decrypt_data(f.read())
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, IOError, OSError):
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
            self._save_now()
            return

        cats = data.get("categories", {})
        self.categories = {
            "text":  cats.get("text",  {"pinned": [], "entries": []}),
            "image": cats.get("image", {"pinned": [], "entries": []}),
            "file":  cats.get("file",  {"pinned": [], "entries": []}),
            "url":   cats.get("url",   {"pinned": [], "entries": []}),
        }
        # 旧数据迁移：历史里存着的超大文本转移到 content/ 正文文件
        self._migrate_large_content()

    def _save(self):
        """请求落盘：防抖合并 + 后台线程写，界面线程不再被加密写盘阻塞。"""
        self._dirty = True
        with self._save_timer_lock:
            if self._save_timer is not None:
                try:
                    self._save_timer.cancel()
                except Exception:
                    pass
            timer = threading.Timer(SAVE_DEBOUNCE_SEC, self._save_now)
            timer.daemon = True
            self._save_timer = timer
            try:
                timer.start()
            except RuntimeError:
                self._save_timer = None
                self._save_now()

    def _save_now(self):
        """立即落盘（内部方法，可在任意线程调用）。"""
        with self._save_lock:
            try:
                with self._lock:
                    payload = {"version": 2, "categories": self.categories}
                    raw = json.dumps(payload, ensure_ascii=False,
                                     indent=2).encode("utf-8")
                # 加密与写文件放在锁外，缩短其它线程的等待时间
                data = _encrypt_data(raw)
                _atomic_write(self.path, data)
                self._dirty = False
            except (IOError, OSError, TypeError, ValueError):
                pass

    def flush(self):
        """取消未触发的防抖任务并立刻落盘（退出程序前调用）。"""
        with self._save_timer_lock:
            if self._save_timer is not None:
                try:
                    self._save_timer.cancel()
                except Exception:
                    pass
                self._save_timer = None
        self._save_now()

    def _migrate_large_content(self):
        """历史迁移：把超大文本正文搬到 content/，历史里只留头部 + 引用。"""
        changed = False
        for key in ("text", "url"):
            cat = self.categories.get(key) or {}
            for lst_name in ("pinned", "entries"):
                for e in cat.get(lst_name, []) or []:
                    if not isinstance(e, dict) or e.get("content_ref"):
                        continue
                    body = e.get("content", "") or ""
                    if len(body) < LARGE_TEXT_THRESHOLD:
                        continue
                    ref = write_external_content(
                        e.get("hash") or self._text_hash(body), body)
                    if not ref:
                        continue
                    e["content"] = body[:CONTENT_HEAD_CHARS]
                    e["content_ref"] = ref
                    e["content_external"] = True
                    e["content_size"] = len(body)
                    changed = True
        if changed:
            self._save_now()
        return changed

    def get_text(self, entry):
        """取一条记录的完整文本：外置正文按需读回，普通记录直接返回。"""
        return entry_full_text(entry)

    # ---- snapshots ----

    def _load_snapshots(self):
        if not os.path.exists(self.snapshots_path):
            self._snapshots = []
            return
        try:
            with open(self.snapshots_path, "rb") as f:
                raw = _decrypt_data(f.read())
            self._snapshots = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, IOError, OSError):
            self._snapshots = []

    def _save_snapshots(self):
        try:
            raw = json.dumps(self._snapshots,
                             ensure_ascii=False, indent=2).encode("utf-8")
            _atomic_write(self.snapshots_path, _encrypt_data(raw))
        except (IOError, OSError):
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
                self.flush()
                return True
        return False

    def clear_snapshots(self):
        self._snapshots = []
        self._save_snapshots()

    def prune_snapshots(self, keep_ids):
        """仅保留指定 id 的快照（临时会话清场用）；返回删除数量。"""
        keep = set(keep_ids or ())
        with self._lock:
            self._ensure_snapshots()
            before = len(self._snapshots)
            kept = [s for s in self._snapshots if s.get("id") in keep]
            if len(kept) != before:
                self._snapshots = kept
                self._save_snapshots()
            return before - len(kept)

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
        # 「预览形态」的文本不入库：内容预览列会把换行写成 " ⏎ "、超长还会截断加 …，
        # 这种文字如果被复制、又被收录，就会和原文形成一条"重复记录"。
        # 预览形态一定是"单行"的（所有换行都被替换成了 ⏎），带真换行的原文不受影响
        if " ⏎ " in text and "\n" not in text and self._looks_like_preview_form(text):
            return False
        h = self._text_hash(text)
        entry = {
            "hash": h, "type": "text", "content": text,
            "timestamp": datetime.now().isoformat(), "length": len(text),
        }
        # 超大文本：正文单独落盘，历史里只保留头部，避免整份历史被反复重写
        if len(text) >= LARGE_TEXT_THRESHOLD:
            ref = write_external_content(h, text)
            if ref:
                entry["content"] = text[:CONTENT_HEAD_CHARS]
                entry["content_ref"] = ref
                entry["content_external"] = True
                entry["content_size"] = len(text)
        with self._lock:
            cat = self.categories["text"]
            self._carry_meta_locked(entry, h)
            cat["entries"] = [e for e in cat["entries"] if e["hash"] != h]
            cat["entries"].insert(0, entry)
            if self.max_entries and len(cat["entries"]) > self.max_entries:
                cat["entries"] = cat["entries"][:self.max_entries]
            self._save()
        return True

    def add_image(self, pil_image, image_hash, source_name=None):
        images_dir = IMAGES_DIR
        os.makedirs(images_dir, exist_ok=True)

        save_path = os.path.join(images_dir, f"{image_hash}.png")
        if not os.path.exists(save_path):
            # 中等压缩：相近的 PNG 体积，明显低于默认高压缩级别的编码耗时
            pil_image.save(save_path, "PNG", compress_level=3, optimize=False)

        thumb_path = os.path.join(images_dir, f"thumb_{image_hash}.png")
        if not os.path.exists(thumb_path):
            from PIL import Image
            src_w, src_h = pil_image.size
            scale = min(1.0, 320.0 / max(1, src_w), 320.0 / max(1, src_h))
            if scale < 1.0:
                thumb = pil_image.resize(
                    (max(1, int(src_w * scale)), max(1, int(src_h * scale))),
                    Image.LANCZOS)
            else:
                thumb = pil_image.copy()
            thumb.save(thumb_path, "PNG")

        fmt = pil_image.format or "PNG"
        with self._lock:
            cat = self.categories["image"]
            new_entry = {
                "hash": image_hash, "type": "image",
                "filename": f"images/{image_hash}.png",
                "original_format": fmt,
                "source_name": source_name or "",
                "width": pil_image.width, "height": pil_image.height,
                "file_size": os.path.getsize(save_path),
                "timestamp": datetime.now().isoformat(),
            }
            # 重新复制同一张图时，先继承旧记录的标签 / 收藏，再替换
            self._carry_meta_locked(new_entry, image_hash)
            cat["entries"] = [e for e in cat["entries"] if e["hash"] != image_hash]
            cat["entries"].insert(0, new_entry)
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
            new_entry = {
                "hash": files_hash, "type": "file",
                "file_paths": list(file_paths),
                "file_sizes": file_sizes,
                "file_count": len(file_paths),
                "timestamp": datetime.now().isoformat(),
            }
            self._carry_meta_locked(new_entry, files_hash)
            cat["entries"] = [e for e in cat["entries"] if e["hash"] != files_hash]
            cat["entries"].insert(0, new_entry)
            if self.max_entries and len(cat["entries"]) > self.max_entries:
                cat["entries"] = cat["entries"][:self.max_entries]
            self._save()
        return True

    def replace_text(self, entry_hash, new_text):
        """把一条文本记录的正文换成 new_text。

        「用 AI 结果替换本条」走这里：标签 / 收藏 / 时间戳都保留，列表位置也不变，
        不会像"新增一条再删旧的"那样跑到列表最前面。
        """
        text = (new_text or "").strip()
        if not text:
            return False
        new_hash = self._text_hash(text)
        with self._lock:
            found = self._scan_locked(entry_hash)
            if not found:
                return False
            cat, list_name, index, old = found
            entry = dict(old)
            for key in ("content_ref", "content_external", "content_size"):
                entry.pop(key, None)
            entry["hash"] = new_hash
            entry["content"] = text
            entry["length"] = len(text)
            entry["edited_at"] = datetime.now().isoformat()
            if len(text) >= LARGE_TEXT_THRESHOLD:
                ref = write_external_content(new_hash, text)
                if ref:
                    entry["content"] = text[:CONTENT_HEAD_CHARS]
                    entry["content_ref"] = ref
                    entry["content_external"] = True
                    entry["content_size"] = len(text)
            # 同分类里若已经有同一份新正文（hash 相同），先去掉，避免出现两条同 hash
            siblings = [e for e in cat[list_name]
                        if e.get("hash") not in (entry_hash, new_hash)]
            siblings.insert(min(index, len(siblings)), entry)
            cat[list_name] = siblings
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
            new_entry = {
                "hash": h, "type": "url", "content": url,
                "timestamp": datetime.now().isoformat(), "length": len(url),
            }
            self._carry_meta_locked(new_entry, h)
            cat["entries"] = [e for e in cat["entries"] if e["hash"] != h]
            cat["entries"].insert(0, new_entry)
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

    # ---- tags / favorites（3.2.7：纯本地，标签与收藏都存在历史里） ----

    def _scan_locked(self, entry_hash):
        """在持锁状态下按 hash 找记录，返回 (cat, 列表名, 下标, 记录)；找不到返回 None。"""
        if not entry_hash:
            return None
        for cat in self.categories.values():
            for name in ("pinned", "entries"):
                entries = cat.get(name) or []
                for i, e in enumerate(entries):
                    if e.get("hash") == entry_hash:
                        return cat, name, i, e
        return None

    def _carry_meta_locked(self, entry, entry_hash):
        """同 hash 重新收录时，把标签 / 收藏状态继承过来（重复复制同一内容不丢标签）。"""
        found = self._scan_locked(entry_hash)
        if not found:
            return
        prev = found[3]
        tags = normalize_tags(entry_tags(prev))
        if tags:
            entry["tags"] = tags
        if prev.get("fav"):
            entry["fav"] = True

    def set_tags(self, entry_hash, tags):
        """整条替换某记录的标签；标签为空则清掉该字段。返回是否命中记录。"""
        clean = normalize_tags(tags)
        with self._lock:
            found = self._scan_locked(entry_hash)
            if not found:
                return False
            entry = found[3]
            if clean:
                entry["tags"] = clean
            else:
                entry.pop("tags", None)
            self._save()
            return True

    def add_tags(self, hashes, tags):
        """给多条记录追加标签（已有的不重复加），返回实际改动的条数。"""
        clean = normalize_tags(tags)
        if not clean:
            return 0
        changed = 0
        with self._lock:
            for h in (hashes or []):
                found = self._scan_locked(h)
                if not found:
                    continue
                entry = found[3]
                cur = entry_tags(entry)
                merged = normalize_tags(cur + clean)
                if merged != cur:
                    entry["tags"] = merged
                    changed += 1
        if changed:
            self._save()
        return changed

    def remove_tags(self, hashes, tags):
        """从多条记录里移除指定标签（不区分大小写），返回实际改动的条数。"""
        drop = {normalize_tag(t).lower() for t in (tags or [])}
        drop.discard("")
        if not drop:
            return 0
        changed = 0
        with self._lock:
            for h in (hashes or []):
                found = self._scan_locked(h)
                if not found:
                    continue
                entry = found[3]
                cur = entry_tags(entry)
                kept = [t for t in cur if t.lower() not in drop]
                if len(kept) != len(cur):
                    changed += 1
                    if kept:
                        entry["tags"] = kept
                    else:
                        entry.pop("tags", None)
        if changed:
            self._save()
        return changed

    def all_tags(self, entry_type=None):
        """全部标签，按记录数多的在前、同数量按名称排。"""
        return [tag for tag, _ in self.tag_counts(entry_type)]

    def tag_counts(self, entry_type=None):
        """[(标签, 记录数)]：供筛选按钮显示数量用。"""
        counts = {}
        with self._lock:
            for e in self._iter_locked(entry_type):
                for tag in entry_tags(e):
                    counts[tag] = counts.get(tag, 0) + 1
        return sorted(counts.items(),
                      key=lambda kv: (-kv[1], kv[0].lower()))

    def tag_count(self, tag):
        want = normalize_tag(tag).lower()
        if not want:
            return 0
        with self._lock:
            return sum(1 for e in self._iter_locked(None)
                       if any(t.lower() == want for t in entry_tags(e)))

    def entries_with_tag(self, tag, entry_type=None):
        """带某标签的记录（顺序与列表页一致：置顶在前、其余按时间倒序）。"""
        want = normalize_tag(tag).lower()
        if not want:
            return []
        with self._lock:
            return [e for e in self._iter_locked(entry_type)
                    if any(t.lower() == want for t in entry_tags(e))]

    def toggle_fav(self, entry_hash):
        """切换收藏，返回切换后的状态；记录不存在时返回 None。"""
        with self._lock:
            found = self._scan_locked(entry_hash)
            if not found:
                return None
            entry = found[3]
            flag = not bool(entry.get("fav"))
            if flag:
                entry["fav"] = True
            else:
                entry.pop("fav", None)
            self._save()
            return flag

    def set_fav(self, entry_hash, flag):
        """设置收藏状态，返回是否命中记录（值没变也算命中）。"""
        with self._lock:
            found = self._scan_locked(entry_hash)
            if not found:
                return False
            entry = found[3]
            if flag:
                entry["fav"] = True
            else:
                entry.pop("fav", None)
            self._save()
            return True

    def set_fav_many(self, hashes, flag):
        """批量设置收藏状态，返回实际改动的条数。"""
        changed = 0
        with self._lock:
            for h in (hashes or []):
                found = self._scan_locked(h)
                if not found:
                    continue
                entry = found[3]
                if bool(entry.get("fav")) != bool(flag):
                    if flag:
                        entry["fav"] = True
                    else:
                        entry.pop("fav", None)
                    changed += 1
        if changed:
            self._save()
        return changed

    def is_fav(self, entry_hash):
        with self._lock:
            found = self._scan_locked(entry_hash)
            return bool(found and found[3].get("fav"))

    def fav_count(self, entry_type=None):
        with self._lock:
            return sum(1 for e in self._iter_locked(entry_type) if e.get("fav"))

    def get_favorites(self, entry_type=None):
        with self._lock:
            return [e for e in self._iter_locked(entry_type) if e.get("fav")]

    def _iter_locked(self, entry_type=None):
        """在持锁状态下遍历记录（置顶在前）；不传类型就遍历四个分类。"""
        keys = [entry_type] if entry_type else list(self.categories.keys())
        for key in keys:
            cat = self.categories.get(key) or {}
            for name in ("pinned", "entries"):
                for e in (cat.get(name) or []):
                    yield e

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

    def export_history(self):
        """导出完整历史（分类 + 快照）的深拷贝，供云同步打包。"""
        with self._lock:
            return (copy.deepcopy(self.categories),
                    copy.deepcopy(self._ensure_snapshots()))

    def merge_history(self, categories, snapshots=None):
        """合并云端历史：条目按 hash 去重（保留时间较新者），快照按 id 去重。"""
        with self._lock:
            for cat_name in ("text", "image", "file", "url"):
                incoming = (categories or {}).get(cat_name, {}) or {}
                if not isinstance(incoming, dict):
                    incoming = {}
                local_cat = self.categories[cat_name]
                merged = {"pinned": [], "entries": []}
                for lst in ("pinned", "entries"):
                    seen = {}
                    for e in list(local_cat.get(lst, [])) + list(incoming.get(lst, []) or []):
                        h = e.get("hash")
                        if not h:
                            continue
                        cur = seen.get(h)
                        if cur is None:
                            seen[h] = e
                            continue
                        if ((e.get("timestamp", "") or "")
                                >= (cur.get("timestamp", "") or "")):
                            newer, older = e, cur
                        else:
                            newer, older = cur, e
                        # 合并同一条记录：较新的一份留正文 / 时间，
                        # 但两边的标签与收藏状态都要保住（本地打过标签的不能被云端覆盖掉）
                        if older.get("fav"):
                            newer["fav"] = True
                        tags = normalize_tags(entry_tags(newer) + entry_tags(older))
                        if tags:
                            newer["tags"] = tags
                        seen[h] = newer
                    merged[lst] = sorted(seen.values(),
                                         key=lambda x: x.get("timestamp", "") or "",
                                         reverse=True)
                # 置顶优先：同一条记录（同 hash）不能既留在「置顶」又在「普通」里
                # 出现第二份——导入时本机置顶的那条正好被别人也复制过，就会出现
                # 这种"两条一样的记录"（一条带置顶标记、一条没有）
                pinned_hashes = {e.get("hash") for e in merged["pinned"]}
                if pinned_hashes:
                    merged["entries"] = [e for e in merged["entries"]
                                         if e.get("hash") not in pinned_hashes]
                self.categories[cat_name] = merged
            if snapshots:
                self._ensure_snapshots()
                by_id = {s.get("id"): s for s in self._snapshots if s.get("id")}
                for s in snapshots:
                    if s.get("id"):
                        by_id[s["id"]] = s
                self._snapshots = sorted(by_id.values(),
                                         key=lambda x: x.get("time", "") or "",
                                         reverse=True)
            self._save_snapshots()
        self.flush()
        return True

    def _looks_like_preview_form(self, text):
        """这段文字是不是既有文本记录"预览形态"的产物（换行被换成 ⏎、可能被截断）。"""
        try:
            norm = text.replace(" ⏎ ", "\n")
            if norm.endswith("…"):
                norm = norm[:-1]
            if len(norm) < 20:
                return False
            with self._lock:
                entries = list(self.categories["text"]["entries"])[:50]
            for e in entries:
                body = e.get("content", "") or ""
                if len(body) < 20:
                    continue
                # 预览是原文的前缀（被截断），或原文是这条预览的前缀
                if body.startswith(norm) or norm.startswith(body):
                    return True
        except Exception:
            pass
        return False

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

    def known_file_names(self, limit=300):
        """已记录过的文件名集合。

        资源管理器 / 压缩软件 / 网盘客户端"复制文件"时，剪贴板里常会附带
        一段文件名文本；用它来判断那段文本其实是文件而不是普通文本。
        """
        names = set()
        try:
            with self._lock:
                cat = self.categories.get("file", {}) or {}
                entries = list(cat.get("pinned", [])) + list(cat.get("entries", []))
            for e in entries[:limit]:
                for p in self._norm_paths(e):
                    n = os.path.basename(p)
                    if n:
                        names.add(n)
        except Exception:
            pass
        return names

    def unpinned_count(self, entry_type=None):
        with self._lock:
            if entry_type:
                return len(self.categories.get(entry_type, {}).get("entries", []))
            return sum(len(c["entries"]) for c in self.categories.values())

    # ---- delete ----

    def insert_entry(self, entry, pinned=False):
        """把一条完整记录插回历史（保留它自己的时间戳，按时间归位）。

        「移出密库」用它：记录当初是几点复制的，放回来还是那个时间，
        在按时间排序的列表里就回到原来的位置；标签 / 收藏 / 置顶也一起带回来。
        """
        if not isinstance(entry, dict) or not entry.get("hash"):
            return False
        etype = entry.get("type")
        if etype not in ("text", "image", "file", "url"):
            etype = "text"
        item = dict(entry)
        item["type"] = etype
        with self._lock:
            cat = self.categories.setdefault(etype, {"pinned": [], "entries": []})
            for name in ("pinned", "entries"):
                cat[name] = [e for e in cat.get(name, [])
                             if e.get("hash") != item["hash"]]
            if pinned:
                item.pop("pinned", None)      # 置顶由所在列表表示，不写字段
                cat["pinned"].append(item)
            else:
                cat["entries"].append(item)
            for name in ("pinned", "entries"):
                cat[name].sort(key=lambda x: x.get("timestamp", "") or "",
                               reverse=True)
            if self.max_entries and len(cat["entries"]) > self.max_entries:
                cat["entries"] = cat["entries"][:self.max_entries]
            self._save()
        return True

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
        """按 hash 删除若干条（返回实际删除条数）。"""
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

    @staticmethod
    def _cache_path_in_dir(path, base_dir):
        """判断一个记录路径是否位于指定缓存目录内。"""
        try:
            normalized = os.path.normcase(os.path.abspath(path))
            normalized_dir = os.path.normcase(os.path.abspath(base_dir))
            return (normalized == normalized_dir or
                    normalized.startswith(normalized_dir + os.sep))
        except (OSError, ValueError):
            return False

    @staticmethod
    def _collect_entry_cache_refs(entry, image_refs, cache_refs,
                                  content_refs=None):
        """收集单个条目引用的图片文件、物化文件缓存与外置正文。"""
        filename = entry.get("filename", "") if isinstance(entry, dict) else ""
        if filename:
            name = os.path.basename(str(filename))
            if name:
                image_refs.add(name)
                image_refs.add("thumb_" + name)
        if isinstance(entry, dict) and entry.get("type") == "file":
            for path in ClipboardStore._norm_paths(entry):
                if ClipboardStore._cache_path_in_dir(path, FILE_CACHE_DIR):
                    cache_refs.add(os.path.normcase(os.path.abspath(path)))
        if isinstance(entry, dict) and content_refs is not None:
            ref = entry.get("content_ref")
            if ref:
                content_refs.add(os.path.basename(str(ref)))

    @staticmethod
    def _collect_state_cache_refs(state, image_refs, cache_refs,
                                  content_refs=None):
        """收集分类状态（历史或快照）中的全部缓存引用。"""
        if not isinstance(state, dict):
            return
        for cat in state.values():
            if not isinstance(cat, dict):
                continue
            for list_name in ("pinned", "entries"):
                for entry in cat.get(list_name, []) or []:
                    ClipboardStore._collect_entry_cache_refs(
                        entry, image_refs, cache_refs, content_refs)

    def garbage_collect(self):
        """删除不再被当前历史或快照引用的图片与物化文件缓存。

        回收在后台线程执行；删除时持有存储锁，并跳过最近写入的文件，
        避免与正在捕获的剪贴板内容发生竞争。
        """
        removed_files = 0
        removed_bytes = 0
        now = time.time()
        with self._lock:
            image_refs = set()
            cache_refs = set()
            content_refs = set()
            self._collect_state_cache_refs(self.categories, image_refs,
                                           cache_refs, content_refs)
            for snap in self._ensure_snapshots():
                self._collect_state_cache_refs(
                    snap.get("state", {}), image_refs, cache_refs, content_refs)
            # 用户选择的背景图可能也落在 images/，同样不能被回收
            try:
                cfg = load_config()
                bg_paths = [cfg.get("bg_image", "")]
                bg_paths.extend(cfg.get("bg_history", []) or [])
                for bg_path in bg_paths:
                    if bg_path and self._cache_path_in_dir(bg_path, IMAGES_DIR):
                        image_refs.add(os.path.basename(str(bg_path)))
                        image_refs.add("thumb_" + os.path.basename(
                            str(bg_path)))
            except (OSError, ValueError, TypeError):
                pass

            if os.path.isdir(IMAGES_DIR):
                try:
                    image_names = os.listdir(IMAGES_DIR)
                except OSError:
                    image_names = []
                for name in image_names:
                    if name in image_refs or not _IMAGE_CACHE_NAME.match(name):
                        continue
                    path = os.path.join(IMAGES_DIR, name)
                    try:
                        if not os.path.isfile(path):
                            continue
                        size = os.path.getsize(path)
                        if now - os.path.getmtime(path) < CACHE_GC_MIN_AGE:
                            continue
                        os.remove(path)
                    except OSError:
                        continue
                    removed_files += 1
                    removed_bytes += size

            if os.path.isdir(FILE_CACHE_DIR):
                for root, dirs, files in os.walk(FILE_CACHE_DIR, topdown=False):
                    for name in files:
                        path = os.path.join(root, name)
                        key = os.path.normcase(os.path.abspath(path))
                        if key in cache_refs:
                            continue
                        try:
                            size = os.path.getsize(path)
                            if now - os.path.getmtime(path) < CACHE_GC_MIN_AGE:
                                continue
                            os.remove(path)
                        except OSError:
                            continue
                        removed_files += 1
                        removed_bytes += size
                    for name in dirs:
                        try:
                            os.rmdir(os.path.join(root, name))
                        except OSError:
                            pass

            # 外置正文：没有任何记录（含快照）引用时回收
            if os.path.isdir(CONTENT_DIR):
                try:
                    content_names = os.listdir(CONTENT_DIR)
                except OSError:
                    content_names = []
                for name in content_names:
                    if name in content_refs or not name.lower().endswith(".txt"):
                        continue
                    path = os.path.join(CONTENT_DIR, name)
                    try:
                        if not os.path.isfile(path):
                            continue
                        size = os.path.getsize(path)
                        if now - os.path.getmtime(path) < CACHE_GC_MIN_AGE:
                            continue
                        os.remove(path)
                    except OSError:
                        continue
                    removed_files += 1
                    removed_bytes += size
        return removed_files, removed_bytes

    @staticmethod
    def _parse_time(value):
        """解析历史时间或保留策略时间，统一转成本地无时区时间。"""
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone().replace(tzinfo=None)
        return parsed

    def _prune_state_before(self, state, cutoff):
        """删除状态中早于 cut-off 的非置顶记录，返回删除数量。"""
        removed = 0
        if not isinstance(state, dict):
            return 0
        for cat in state.values():
            if not isinstance(cat, dict):
                continue
            kept = []
            for entry in cat.get("entries", []) or []:
                stamp = self._parse_time(entry.get("timestamp", ""))
                if stamp is not None and stamp < cutoff:
                    removed += 1
                else:
                    kept.append(entry)
            cat["entries"] = kept
        return removed

    def apply_retention(self):
        """按配置的保留策略清理历史；返回当前历史中删除的记录数。"""
        policy = load_config().get("history_retention") or {}
        mode = policy.get("mode", "forever")
        if mode not in ("age", "expire"):
            return 0

        now = datetime.now()
        if mode == "age":
            try:
                hours = float(policy.get("hours", 0) or 0)
            except (TypeError, ValueError):
                return 0
            if hours <= 0:
                return 0
            cutoff = now - timedelta(hours=hours)
            with self._lock:
                removed = self._prune_state_before(self.categories, cutoff)
                snap_removed = 0
                for snap in self._ensure_snapshots():
                    snap_removed += self._prune_state_before(
                        snap.get("state", {}), cutoff)
                if removed:
                    self._save()
                if snap_removed:
                    self._save_snapshots()
            return removed

        expire_at = self._parse_time(policy.get("expire_at", ""))
        if expire_at is None or now < expire_at:
            return 0
        with self._lock:
            removed = sum(
                len(cat.get("pinned", [])) + len(cat.get("entries", []))
                for cat in self.categories.values())
            self._init_empty()
            self._snapshots = []
            self._save()
            self._save_snapshots()
        cfg = load_config()
        cfg["history_retention"] = {"mode": "forever"}
        save_config(cfg)
        return removed

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
                        # 标签也算命中：搜标签名就能把打了这条标签的记录捞出来
                        if any(kw in str(t).lower() for t in entry_tags(e)):
                            result.append(e)
                            continue
                        if key in ("text", "url"):
                            head = e.get("content", "") or ""
                            if kw in head.lower():
                                result.append(e)
                                continue
                            # 外置正文：只在文件不大的时候深入读取，避免搜索卡顿
                            ref = e.get("content_ref")
                            if ref:
                                try:
                                    size = os.path.getsize(content_file_path(ref))
                                except OSError:
                                    size = 0
                                if 0 < size <= SEARCH_READ_LIMIT:
                                    if kw in read_external_content(ref).lower():
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
# 其它 YouBoard 安装的数据识别与移植（用户主动选择后才执行）
# ===========================================================================

_HISTORY_NAMES = (".youboard.json", ".clipboard_history.json")
_KEY_NAMES = ("youboard.key", ".clipboard.key")


def _foreign_history_path(folder):
    for name in _HISTORY_NAMES:
        path = os.path.join(folder, name)
        if os.path.exists(path):
            return path
    return ""


def _foreign_key(folder):
    for name in _KEY_NAMES:
        path = os.path.join(folder, name)
        try:
            if os.path.exists(path):
                with open(path, "rb") as f:
                    key = f.read().strip()
                if key:
                    return key
        except Exception:
            continue
    return None


def read_foreign_history(folder):
    """解密另一个 YouBoard 安装的历史文件，返回 categories（失败返回 None）。

    该安装可能使用自己的 youboard.key，因此这里显式用它的密钥解密。
    """
    if not folder:
        return None
    folder = os.path.abspath(str(folder))
    if os.path.isfile(folder):
        folder = os.path.dirname(folder)
    hist_path = _foreign_history_path(folder)
    if not hist_path:
        return None
    try:
        with open(hist_path, "rb") as f:
            blob = f.read()
    except (IOError, OSError):
        return None
    data = None
    if blob.startswith(b"gAAAA") and _HAS_FERNET:
        key = _foreign_key(folder)
        if key:
            try:
                data = json.loads(Fernet(key).decrypt(blob).decode("utf-8"))
            except Exception:
                data = None
        if data is None:
            return None
    else:
        try:
            data = json.loads(blob.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, TypeError):
            return None
    if not isinstance(data, dict):
        return None
    if data.get("version", 1) == 1:
        return {
            "text": {"pinned": data.get("pinned", []),
                     "entries": [e for e in (data.get("entries") or [])
                                 if isinstance(e, dict)]},
            "image": {"pinned": [], "entries": []},
            "file": {"pinned": [], "entries": []},
            "url": {"pinned": [], "entries": []},
        }
    cats = data.get("categories")
    if not isinstance(cats, dict):
        return None
    out = {}
    for key in ("text", "image", "file", "url"):
        cat = cats.get(key) or {}
        if not isinstance(cat, dict):
            cat = {}
        pinned = [e for e in (cat.get("pinned") or []) if isinstance(e, dict)]
        entries = [e for e in (cat.get("entries") or []) if isinstance(e, dict)]
        out[key] = {"pinned": pinned, "entries": entries}
    return out


def inspect_installation(folder):
    """检查一个目录是否是可移植的 YouBoard 数据目录，返回统计信息或 None。"""
    if not folder:
        return None
    folder = os.path.abspath(str(folder))
    if os.path.isfile(folder):
        folder = os.path.dirname(folder)
    if not _foreign_history_path(folder):
        return None
    cats = read_foreign_history(folder)
    if cats is None:
        return {
            "path": folder, "readable": False, "total": 0,
            "counts": {}, "images": 0, "content": 0,
            "modified": 0.0, "has_key": bool(_foreign_key(folder)),
        }
    counts = {k: len(v["pinned"]) + len(v["entries"]) for k, v in cats.items()}
    images_dir = os.path.join(folder, "images")
    content_dir = os.path.join(folder, "content")

    def _count(dir_path, suffix=None):
        if not os.path.isdir(dir_path):
            return 0
        try:
            names = os.listdir(dir_path)
        except OSError:
            return 0
        if suffix:
            names = [n for n in names if n.lower().endswith(suffix)]
        return len(names)

    try:
        modified = os.path.getmtime(_foreign_history_path(folder))
    except OSError:
        modified = 0.0
    return {
        "path": folder,
        "readable": True,
        "total": sum(counts.values()),
        "counts": counts,
        "images": _count(images_dir, ".png"),
        "content": _count(content_dir, ".txt"),
        "modified": modified,
        "has_key": bool(_foreign_key(folder)),
        "categories": cats,
    }


def find_installations(extra_dirs=None):
    """扫描本机可能存在的其它 YouBoard 数据目录（只做识别，不做任何修改）。"""
    registry_dirs = []
    home = os.path.expanduser("~")
    scan_roots = []
    # 1) 注册表卸载项里的安装位置（覆盖安装版）
    if IS_WIN:
        try:
            import winreg
            roots = [
                (winreg.HKEY_CURRENT_USER,
                 r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
                (winreg.HKEY_LOCAL_MACHINE,
                 r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
                (winreg.HKEY_LOCAL_MACHINE,
                 r"Software\WOW6432Node\Microsoft\Windows"
                 r"\CurrentVersion\Uninstall"),
            ]
            for hive, sub in roots:
                try:
                    with winreg.OpenKey(hive, sub) as root_key:
                        for i in range(winreg.QueryInfoKey(root_key)[0]):
                            try:
                                name = winreg.EnumKey(root_key, i)
                                with winreg.OpenKey(root_key, name) as sub_key:
                                    disp = ""
                                    try:
                                        disp = str(winreg.QueryValueEx(
                                            sub_key, "DisplayName")[0])
                                    except OSError:
                                        pass
                                    if "youboard" not in disp.lower():
                                        continue
                                    for value_name in ("InstallLocation",
                                                       "UninstallString",
                                                       "DisplayIcon"):
                                        try:
                                            val = str(winreg.QueryValueEx(
                                                sub_key, value_name)[0])
                                        except OSError:
                                            continue
                                        if not val:
                                            continue
                                        val = val.strip('"').strip()
                                        if value_name == "InstallLocation":
                                            registry_dirs.append(val)
                                        else:
                                            registry_dirs.append(
                                                os.path.dirname(val))
                            except OSError:
                                continue
                except OSError:
                    continue
        except Exception:
            pass
    # 2) 常见目录与盘符根目录（便携版解压位置）
    common = list(registry_dirs) + [
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        os.path.join(home, "Desktop"), os.path.join(home, "Downloads"),
        os.path.join(home, "Documents"),
        os.path.join(home, "AppData", "Local", "Programs"),
        os.path.join(home, "AppData", "Roaming"),
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                     "YouBoard"),
        os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                     "YouBoard"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "YouBoard"),
    ]
    if IS_WIN:
        try:
            import string
            for letter in string.ascii_uppercase:
                drive = f"{letter}:\\"
                if os.path.isdir(drive):
                    common.append(os.path.join(drive, "YouBoard"))
                    scan_roots.append(drive)
        except Exception:
            pass
    # 盘符根目录做一次浅层扫描（限制深度 + 时间预算），覆盖「解压到别处」的便携副本
    common.extend(_shallow_scan_data_dirs(scan_roots))
    for item in (extra_dirs or []):
        if item:
            common.append(item)

    seen = set()
    results = []
    current = os.path.normcase(os.path.abspath(_BASE_DIR))
    for base in common:
        if not base:
            continue
        try:
            base = os.path.abspath(base)
        except (OSError, ValueError):
            continue
        probes = [base]
        # 目标目录本身、以及它下面一层的同名子目录都检查一遍
        try:
            if os.path.isdir(base):
                for name in os.listdir(base)[:400]:
                    full = os.path.join(base, name)
                    if os.path.isdir(full) and "youboard" in name.lower():
                        probes.append(full)
        except OSError:
            pass
        for probe in probes:
            key = os.path.normcase(os.path.abspath(probe))
            if key in seen:
                continue
            seen.add(key)
            if key == current:
                continue
            info = inspect_installation(probe)
            if info and info.get("readable"):
                results.append(info)
    results.sort(key=lambda x: (-int(x.get("total", 0)),
                                -(x.get("modified") or 0)))
    return results


def _shallow_scan_data_dirs(roots, max_depth=3, budget_sec=8.0):
    """在各盘符里浅层查找含 .youboard.json 的目录（限深度和时间，避免拖慢启动）。"""
    found = []
    if not roots:
        return found
    skip = {"windows", "program files", "program files (x86)", "programdata",
            "$recycle.bin", "system volume information", "recovery",
            "perflogs", "msocache", "$windows.~ws", "$windows.~bt",
            "python312", "python311", "python310", "node_modules",
            "windowsapps", "packages", "steam", "steamapps", "epic games",
            "venv", ".venv", "env", "site-packages", "__pycache__",
            "temp", "tmp", "cache", "caches", "logs"}
    deadline = time.time() + budget_sec
    for root in roots:
        try:
            base_depth = os.path.abspath(root).rstrip("\\/").count(os.sep)
        except (OSError, ValueError):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            if time.time() > deadline:
                return found
            dirnames[:] = [d for d in dirnames
                           if not d.startswith(".")
                           and d.lower() not in skip]
            try:
                depth = os.path.abspath(dirpath).count(os.sep) - base_depth
            except (OSError, ValueError):
                depth = 0
            if any(f.lower() in _HISTORY_NAMES for f in filenames):
                found.append(dirpath)
            if depth >= max_depth:
                dirnames[:] = []
    return found


def copy_installation_assets(folder, categories, progress=None):
    """把另一个安装里被引用到的图片与正文文件复制过来，返回复制数量。"""
    if not folder or not categories:
        return 0
    folder = os.path.abspath(str(folder))
    src_images = os.path.join(folder, "images")
    src_content = os.path.join(folder, "content")
    copied = 0
    try:
        os.makedirs(IMAGES_DIR, exist_ok=True)
        os.makedirs(CONTENT_DIR, exist_ok=True)
    except OSError:
        pass
    for key in ("image", "text", "url"):
        cat = categories.get(key) or {}
        for lst in ("pinned", "entries"):
            for e in cat.get(lst) or []:
                if not isinstance(e, dict):
                    continue
                if key == "image":
                    names = [os.path.basename(str(e.get("filename") or ""))]
                    if not names[0]:
                        continue
                    names.append("thumb_" + names[0])
                    for name in names:
                        if not name:
                            continue
                        if os.path.basename(name) != name:
                            continue
                        dst = os.path.join(IMAGES_DIR, name)
                        if os.path.exists(dst):
                            continue
                        src = os.path.join(src_images, name)
                        if os.path.isfile(src):
                            try:
                                shutil.copy2(src, dst)
                                copied += 1
                            except (IOError, OSError):
                                pass
                else:
                    ref = e.get("content_ref")
                    if not ref:
                        continue
                    name = os.path.basename(str(ref))
                    dst = os.path.join(CONTENT_DIR, name)
                    if os.path.exists(dst):
                        continue
                    src = os.path.join(src_content, name)
                    if os.path.isfile(src):
                        try:
                            shutil.copy2(src, dst)
                            copied += 1
                        except (IOError, OSError):
                            pass
                if progress is not None:
                    try:
                        progress()
                    except Exception:
                        pass
    return copied


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
        self._last_file_hash = (self.store._files_hash(data)
                                if (ctype == "file" and data) else "")

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
            ctype, data = get_clipboard_content(self.store.known_file_names())
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
            if not data:
                # 判定为"复制文件"（剪贴板里是文件格式，或只是附带的文件名文本），
                # 但没有可用的路径：不收录，也不退化成文本记录。
                return True
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
