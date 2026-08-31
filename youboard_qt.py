#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
YouBoard v2.8.0 — 剪贴板历史管理器 / Clipboard History Manager
PyQt6 重构版：透明毛玻璃背景、QPropertyAnimation 动效、原生系统托盘。
"""

import colorsys
import ctypes
import gc
import locale
import math
import os
import queue
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from html import escape as _html_escape

IS_WIN = (sys.platform == "win32")
IS_MAC = (sys.platform == "darwin")
if IS_WIN:
    import ctypes.wintypes

# ---------------------------------------------------------------------------
# High DPI setup (must precede QApplication creation)
# ---------------------------------------------------------------------------
if IS_WIN:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    APP_USER_MODEL_ID = "YouBoard.ClipboardHistory.2.1"
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            APP_USER_MODEL_ID)
    except Exception:
        pass

try:
    locale.setlocale(locale.LC_COLLATE, '')
except Exception:
    pass

# ---------------------------------------------------------------------------
# PyQt6 imports
# ---------------------------------------------------------------------------
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView,
    QLabel, QPushButton, QLineEdit, QComboBox, QSplitter,
    QSystemTrayIcon, QMenu, QDialog, QScrollArea, QFrame,
    QFileDialog, QMessageBox, QAbstractItemView, QSizePolicy,
    QGraphicsOpacityEffect, QSpacerItem, QGroupBox,
    QCheckBox, QTextEdit, QListView, QListWidget, QListWidgetItem,
    QStyle, QProgressDialog, QStyledItemDelegate, QStyleOptionViewItem,
)
from PyQt6.QtCore import (
    Qt, QTimer, QPropertyAnimation, QEasingCurve, pyqtSignal,
    QThread, QObject, QSize, QRect, QRectF, QPoint, QEvent,
    QAbstractNativeEventFilter, QUrl,
)
from PyQt6.QtGui import (
    QIcon, QPixmap, QImage, QPainter, QColor, QFont,
    QAction, QActionGroup, QKeySequence, QShortcut, QBrush, QPen,
    QPalette, QLinearGradient, QPainterPath, QCursor, QMovie, QTextDocument,
    QTextCharFormat, QTextCursor,
)
try:
    import PIL  # noqa: F401  # 轻量探测；PIL.Image 按需在调用点懒加载，降低常驻内存
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import keyboard as _keyboard_lib
    HAS_KEYBOARD = True
except Exception:
    # keyboard 仅支持 Windows/Linux，macOS 上 import 会抛 OSError
    HAS_KEYBOARD = False

from youboard_core import (
    ClipboardStore, ClipboardMonitor, HISTORY_FILE, TIME_FORMAT,
    IMAGES_DIR, FILE_CACHE_DIR,
    set_clipboard_text, set_clipboard_image, set_clipboard_files,
    load_config, save_config, get_autostart, set_autostart,
    get_icon_path, get_app_icon,
)
from youboard_phone import (
    PhoneTransferServer, get_lan_ip, get_lan_ips, make_qr_pil,
    pick_free_port,
)
from youboard_sync import (
    SyncError, GistSyncClient, WebDAVSyncClient,
    encrypt_bundle, decrypt_bundle, protect_secret, unprotect_secret,
)

# ===========================================================================
# Constants
# ===========================================================================
APP_NAME = "YouBoard"
APP_VERSION = "2.8.0"
LOGO_ICO = get_icon_path()
DISPLAY_LIMIT = 400
HIST_DISPLAY = 60
PREVIEW_MAX = 1600
TAB_ICONS = {"text": "\u270e", "image": "\u25a3", "file": "\u25a0", "url": "\u25c9"}
TAB_ICON_FILES = {"text": "wenben.ico", "image": "tupian.ico",
                  "file": "wenjian.ico", "url": "wangzhi.ico"}


def _res_icon(name):
    """Return absolute path for an icon in the res/ folder."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        p = os.path.join(base, "res", name)
        if os.path.exists(p):
            return p
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "res", name)


_HUICHE_PM = None


def _huiche_pixmap(size=14):
    """文本预览里内联显示的换行图标（res/huiche.png），只加载一次。"""
    global _HUICHE_PM
    if _HUICHE_PM is None:
        p = _res_icon("huiche.png")
        pm = QPixmap(p) if p and os.path.exists(p) else QPixmap()
        if not pm.isNull():
            pm = pm.scaled(size, size,
                           Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
        _HUICHE_PM = pm
    return _HUICHE_PM


def _text_preview_html(text, max_len=120):
    """把文本预览转成 HTML：换行处内联 huiche 图标，制表符转空格。"""
    html_src = _html_escape(text[:max_len])
    html_src = html_src.replace(
        "\n", '<img src="huiche" width="14" height="14" '
              'style="vertical-align:middle; margin:0 2px;">')
    html_src = html_src.replace("\t", "&nbsp;&nbsp;")
    if len(text) > max_len:
        html_src += "…"
    return html_src


class _InlineImageDelegate(QStyledItemDelegate):
    """在单元格里渲染带内联图片的 HTML（文本预览的换行图标）。"""

    HTML_ROLE = Qt.ItemDataRole.UserRole + 1

    def paint(self, painter, option, index):
        html = index.data(self.HTML_ROLE)
        if not html:
            super().paint(painter, option, index)
            return
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        opt.icon = QIcon()
        widget = option.widget
        style = widget.style() if widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem,
                          opt, painter, widget)
        painter.save()
        doc = QTextDocument()
        doc.setDefaultFont(opt.font)
        doc.setDocumentMargin(0)
        doc.addResource(QTextDocument.ResourceType.ImageResource,
                        QUrl("huiche"), _huiche_pixmap())
        doc.setHtml(html)
        # 文字颜色跟随主题（QTextDocument 默认黑字，不继承 QSS）
        fmt = QTextCharFormat()
        fmt.setForeground(QBrush(QColor(C['TEXT'])))
        cursor = QTextCursor(doc)
        cursor.select(QTextCursor.SelectionType.Document)
        cursor.mergeCharFormat(fmt)
        doc.setTextWidth(100000.0)  # 保持单行，超宽交给视图裁剪
        doc_h = doc.size().height()
        y = option.rect.top() + max(0.0, (option.rect.height() - doc_h) / 2.0)
        painter.translate(option.rect.left() + 3, y)
        doc.drawContents(painter, QRectF(
            0, 0, max(1.0, option.rect.width() - 6), option.rect.height()))
        painter.restore()


def _build_tray_icon():
    """Build a multi-size tray QIcon from YouBoard.ico.

    YouBoard.ico 只有单张 512x512，直接转小尺寸 HICON 在部分系统/DPI 下
    会产生空白占位图标。这里预渲染常用托盘尺寸的清晰 pixmap 加入 QIcon，
    让 Windows 托盘取到合适的小尺寸位图。ico 文件本身不做任何修改。
    """
    if not LOGO_ICO or not os.path.exists(LOGO_ICO):
        return QIcon()
    base = QPixmap(LOGO_ICO)
    if base.isNull():
        img = QImage(LOGO_ICO)
        if img.isNull():
            return QIcon()
        base = QPixmap.fromImage(img)
    icon = QIcon()
    for s in (16, 20, 24, 32, 40, 48, 64):
        pm = base.scaled(s, s,
                         Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
        icon.addPixmap(pm, QIcon.Mode.Normal, QIcon.State.Off)
    return icon


ICO_MIN = _res_icon("zuixiao.ico")
ICO_MAX = _res_icon("zuida.ico")
ICO_RESTORE = _res_icon("zuidahuifu.ico")
ICO_CLOSE = _res_icon("guanbi.ico")
ICO_SETTINGS = _res_icon("shezhi.ico")


def _force_square_corners(widget):
    """Win11 会给无边框窗口自动加圆角（四角留缝），用 DWM 强制直角贴合屏幕。"""
    if not IS_WIN:
        return
    try:
        hwnd = int(widget.winId())
        pref = ctypes.c_int(1)  # DWMWCP_DONOTROUND
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 33, ctypes.byref(pref), ctypes.sizeof(pref))  # DWMWA_WINDOW_CORNER_PREFERENCE
    except Exception:
        pass


def _open_path(path):
    """跨平台打开文件/文件夹/图片（Windows 用 os.startfile，macOS 用 open）。"""
    if IS_MAC:
        subprocess.Popen(["open", os.path.abspath(path)])
    else:
        os.startfile(path)


def _checkmark_png_path():
    """Draw a white checkmark to a cached temp PNG and return its path.

    Call only after a QApplication exists (needs QGuiApplication for QPixmap).
    """
    import tempfile
    path = os.path.join(tempfile.gettempdir(), "youb_check.png")
    if not os.path.exists(path):
        pm = QPixmap(24, 24)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#ffffff"), 3.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.drawLine(5, 13, 10, 18)
        p.drawLine(10, 18, 19, 6)
        p.end()
        pm.save(path)
    return path

# ===========================================================================
# Theme colors
# ===========================================================================
THEME_DARK = {
    "BG": "#1e2128", "SURFACE": "#262a33", "SURFACE2": "#2f343f",
    "SURFACE3": "#3a404c", "ROW_ALT": "#282c36", "BORDER": "#414855",
    "BORDER_LT": "#525b6b", "TEXT": "#eef0f5", "TEXT_SEC": "#b3b9c6",
    "TEXT_MUTED": "#7d8598", "ACCENT": "#4f9df8", "ACCENT_HV": "#6cb0ff",
    "ACCENT_DIM": "#2c3d57", "TEAL": "#3fd0b6", "AMBER": "#f2b54d",
    "PIN_BG": "#2a2517", "DANGER": "#f16a5c", "SUCCESS": "#45d18c",
    "FLASH_BG": "#1e3a2c",
    "PANEL_ALPHA": "rgba(40, 45, 56, 165)", "PANEL_ALPHA2": "rgba(48, 54, 66, 150)",
    "HEADER_ALPHA": "rgba(32, 36, 45, 135)",
}

THEME_LIGHT = {
    "BG": "#f5f6fa", "SURFACE": "#ffffff", "SURFACE2": "#eef0f5",
    "SURFACE3": "#e2e5ec", "ROW_ALT": "#f0f2f7", "BORDER": "#d4d8e0",
    "BORDER_LT": "#c0c5d0", "TEXT": "#1a1d26", "TEXT_SEC": "#4a5062",
    "TEXT_MUTED": "#8b92a5", "ACCENT": "#2b7de9", "ACCENT_HV": "#1a6ad4",
    "ACCENT_DIM": "#dbeafe", "TEAL": "#0d9488", "AMBER": "#d97706",
    "PIN_BG": "#fef3c7", "DANGER": "#dc2626", "SUCCESS": "#16a34a",
    "FLASH_BG": "#d1fae5",
    "PANEL_ALPHA": "rgba(255, 255, 255, 115)", "PANEL_ALPHA2": "rgba(240, 242, 248, 105)",
    "HEADER_ALPHA": "rgba(250, 251, 254, 85)",
}

C = {}


def apply_theme(name="dark"):
    """Set the active theme palette into global C dict."""
    global C
    C = THEME_LIGHT if name == "light" else THEME_DARK


apply_theme(load_config().get("theme", "dark"))


def apply_global_palette(name="dark"):
    """把 QApplication 的整体调色板也设为主题色。

    QComboBox 的弹出浮层（以及部分系统控件）不看 QSS，而是依赖 QPalette。
    不设的话它们会沿用系统浅色，导致下拉弹层出现白底。这里统一按主题配色。
    """
    app = QApplication.instance()
    if app is None:
        return
    c = THEME_LIGHT if name == "light" else THEME_DARK
    pal = app.palette()
    for role, key in [
        (QPalette.ColorRole.Window, "SURFACE2"),
        (QPalette.ColorRole.Base, "SURFACE2"),
        (QPalette.ColorRole.AlternateBase, "SURFACE"),
        (QPalette.ColorRole.Text, "TEXT"),
        (QPalette.ColorRole.WindowText, "TEXT"),
        (QPalette.ColorRole.Button, "SURFACE2"),
        (QPalette.ColorRole.ButtonText, "TEXT"),
        (QPalette.ColorRole.Highlight, "ACCENT_DIM"),
        (QPalette.ColorRole.HighlightedText, "TEXT"),
        (QPalette.ColorRole.ToolTipBase, "SURFACE2"),
        (QPalette.ColorRole.ToolTipText, "TEXT"),
        (QPalette.ColorRole.PlaceholderText, "TEXT_MUTED"),
    ]:
        pal.setColor(role, QColor(c[key]))
    app.setPalette(pal)


def _is_light_theme():
    """当前是否亮色主题（用于亮色下标题栏、按钮图标等做适配）。"""
    return C.get("BG") == THEME_LIGHT.get("BG")


class _ThemeTitleBarFilter(QObject):
    """顶层窗口显示时自动套用主题标题栏，覆盖 QMessageBox 等原生标题栏弹窗。"""
    def eventFilter(self, obj, event):
        return super().eventFilter(obj, event)


def _enable_dark_title_bar(widget):
    """让 Windows 系统标题栏跟随主题：暗色主题下标题栏也变深色（DWM）。"""
    if not IS_WIN:
        return
    try:
        import ctypes
        from ctypes import wintypes
        hwnd = int(widget.winId())
        val = ctypes.c_int(0 if _is_light_theme() else 1)  # 1 = dark title bar
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (20/19)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    wintypes.HWND(hwnd), attr,
                    ctypes.byref(val), ctypes.sizeof(val)) == 0:
                break
    except Exception:
        pass


def _get_wallpaper():
    """读取当前 Windows 桌面壁纸路径；失败返回空串。"""
    if not IS_WIN:
        return ""
    # 优先：Windows 把"当前屏幕壁纸"转码保存的文件（普通/幻灯片/聚焦壁纸的当前画面）
    tw = os.path.join(os.environ.get("APPDATA", ""), "Microsoft",
                      "Windows", "Themes", "TranscodedWallpaper")
    if os.path.exists(tw):
        return tw
    try:
        import ctypes
        SPI_GETDESKWALLPAPER = 0x0073
        buf = ctypes.create_unicode_buffer(1024)
        ctypes.windll.user32.SystemParametersInfoW(SPI_GETDESKWALLPAPER, 1024, buf, 0)
        p = buf.value
        if p and os.path.exists(p):
            return p
    except Exception:
        pass
    # 兜底：Windows 聚焦（Spotlight）壁纸缓存的图片，取最近一张存在的
    try:
        base = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Packages")
        if os.path.isdir(base):
            cands = []
            for name in os.listdir(base):
                if ("MicrosoftWindows.Client.CBS" in name
                        or "Microsoft.Windows.ContentDeliveryManager" in name):
                    folder = os.path.join(base, name, "LocalCache", "Microsoft")
                    for root, _dirs, files in os.walk(folder):
                        for f in files:
                            if f.lower().endswith((".jpg", ".jpeg", ".png")):
                                fp = os.path.join(root, f)
                                try:
                                    cands.append((os.path.getmtime(fp), fp))
                                except OSError:
                                    pass
            if cands:
                cands.sort(reverse=True)
                return cands[0][1]
    except Exception:
        pass
    return ""
def _short_display_name(name):
    """裁剪过长的背景文件名显示：含中文取前 6 字，英文/数字取前 10 位，末尾补 … 并保留扩展名。"""
    if not name:
        return name
    s = str(name)
    root, ext = os.path.splitext(s)
    has_cjk = any('\u4e00' <= ch <= '\u9fff' for ch in root)
    keep = 6 if has_cjk else 10
    if len(root) <= keep:
        return s
    return root[:keep] + "…" + ext


def _find_wallpaper_hwnds():
    """返回候选壁纸层窗口列表（多个 WorkerW + Progman），按优先级排序，逐个尝试抓取。"""
    if not IS_WIN:
        return []
    user32 = ctypes.windll.user32
    progman = user32.FindWindowW("Progman", None)
    if progman:
        # WM_SPAWN_WORKERW = 0x052C：让系统把壁纸层（WorkerW）创建到桌面图标下层
        try:
            user32.SendMessageTimeoutW(progman, 0x052C, 0, 0, 0, 1000, None)
        except Exception:
            pass
    workers = []
    for _ in range(20):  # 等待 WorkerW 创建完成
        workers = []
        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
        def _cb(hwnd, _):
            buf = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(hwnd, buf, 64)
            if buf.value == "WorkerW":
                workers.append(hwnd)
            return True
        user32.EnumWindows(_cb, 0)
        if workers:
            break
        time.sleep(0.02)
    defview_idx = -1
    for i, h in enumerate(workers):
        if user32.FindWindowExW(h, 0, "SHELLDLL_DefView", None):
            defview_idx = i
            break
    ordered = []
    if defview_idx >= 0:  # 图标层之后的 WorkerW 通常是壁纸层
        ordered.extend(workers[defview_idx + 1:])
    for h in workers:  # 带子窗口的优先（Wallpaper Engine 常渲染到子窗口）
        if h not in ordered and user32.GetWindow(h, 5):
            ordered.append(h)
    for h in workers:
        if h not in ordered:
            ordered.append(h)
    if progman:
        ordered.append(progman)
    return ordered


def _grab_window(hwnd, out_path):
    """把指定窗口绘制为图片保存到 out_path；成功返回 True。"""
    if not hwnd:
        return False
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    r = ctypes.wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return False
    w, h = r.right - r.left, r.bottom - r.top
    if w <= 0 or h <= 0:
        return False
    hwnd_dc = user32.GetWindowDC(hwnd)
    if not hwnd_dc:
        return False
    mfc = gdi32.CreateCompatibleDC(hwnd_dc)
    bmp = gdi32.CreateCompatibleBitmap(hwnd_dc, w, h)
    old = gdi32.SelectObject(mfc, bmp)
    try:
        # PW_RENDERFULLCONTENT = 2：抓取含 DirectComposition / 硬件加速渲染的内容
        user32.PrintWindow(hwnd, mfc, 2)
        from PIL import Image

        class _BIH(ctypes.Structure):
            _fields_ = [('biSize', ctypes.c_uint32), ('biWidth', ctypes.c_int32),
                        ('biHeight', ctypes.c_int32), ('biPlanes', ctypes.c_uint16),
                        ('biBitCount', ctypes.c_uint16), ('biCompression', ctypes.c_uint32),
                        ('biSizeImage', ctypes.c_uint32), ('biXPelsPerMeter', ctypes.c_int32),
                        ('biYPelsPerMeter', ctypes.c_int32), ('biClrUsed', ctypes.c_uint32),
                        ('biClrImportant', ctypes.c_uint32)]
        bmi = _BIH()
        bmi.biSize = ctypes.sizeof(_BIH)
        bmi.biWidth, bmi.biHeight, bmi.biPlanes = w, -h, 1
        bmi.biBitCount, bmi.biCompression = 32, 0
        buf = ctypes.create_string_buffer(w * h * 4)
        got = gdi32.GetDIBits(mfc, bmp, 0, h, buf, ctypes.byref(bmi), 0)
        if got:
            img = Image.frombuffer("RGBA", (w, h), buf.raw, "raw", "BGRA", 0, 1).convert("RGB")
            img.save(out_path)
            return True
    except Exception:
        pass
    finally:
        gdi32.SelectObject(mfc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mfc)
        user32.ReleaseDC(hwnd, hwnd_dc)
    return False


def _capture_wallpaper():
    """抓取当前桌面壁纸层（不含图标/任务栏），成功返回图片文件路径，失败返回空串。

    用于识别 Wallpaper Engine 等动态壁纸正在播放的"当前帧"——系统注册表里只有底层静态图，
    动态壁纸没有可读的静态文件，只能从壁纸层的屏幕缓冲抓取。
    """
    if not IS_WIN:
        return ""
    out = os.path.join(IMAGES_DIR, "_cur_wallpaper.png")
    try:
        os.makedirs(IMAGES_DIR, exist_ok=True)
    except OSError:
        pass
    for hwnd in _find_wallpaper_hwnds():
        if hwnd and _grab_window(hwnd, out):
            try:
                from PIL import Image
                im = Image.open(out).convert("L")
                w, h = im.size
                # 抽样判断是否几乎纯黑（硬件加速内容未被 PrintWindow 捕获时会得到黑图）
                data = list(im.getdata())
                total = w * h
                dark = sum(1 for v in data if v < 8)
                if total > 0 and dark / total < 0.98:
                    return out
            except Exception:
                return out
    return ""


def _hide_desktop_overlay():
    """临时隐藏桌面图标列表与任务栏，返回记录以便恢复（尽量只露出壁纸）。"""
    if not IS_WIN:
        return []
    user32 = ctypes.windll.user32
    hidden = []
    taskbar = user32.FindWindowW("Shell_TrayWnd", None)
    if taskbar:
        hidden.append(("Shell_TrayWnd", taskbar))
        user32.ShowWindow(taskbar, 0)  # SW_HIDE
    progman = user32.FindWindowW("Progman", None)
    defview = (user32.FindWindowExW(progman, 0, "SHELLDLL_DefView", None)
               if progman else 0)
    iconlist = (user32.FindWindowExW(defview, 0, "SysListView32", None)
                if defview else 0)
    if iconlist:
        hidden.append(("SysListView32", iconlist))
        user32.ShowWindow(iconlist, 0)  # SW_HIDE
    return hidden


def _show_desktop_overlay(hidden):
    """恢复被 _hide_desktop_overlay 隐藏的桌面图标/任务栏。"""
    if not IS_WIN or not hidden:
        return
    user32 = ctypes.windll.user32
    for _cls, h in hidden:
        if h:
            user32.ShowWindow(h, 5)  # SW_SHOW


def _image_is_mostly_black(img):
    """判断截图是否几乎全黑（通常是抓到未隐藏的应用暗色窗口或抓取失败），避免设成黑壁纸。"""
    try:
        w, h = img.width(), img.height()
        if w <= 0 or h <= 0:
            return True
        total = w * h
        step = max(1, total // 4096)
        cnt = dark = 0
        for i in range(0, total, step):
            x = i % w
            y = i // w
            c = img.pixelColor(x, y)
            if c.red() + c.green() + c.blue() < 30:
                dark += 1
            cnt += 1
        if cnt == 0:
            return True
        return dark / cnt > 0.97
    except Exception:
        return False


def _tint_icon(png_path, color):
    """把单色图标重染成指定颜色（亮色主题下把白色窗口按钮变深色）。"""
    pm = QPixmap(png_path)
    if pm.isNull():
        img = QImage(png_path)
        if img.isNull():
            return QIcon(png_path)
        pm = QPixmap.fromImage(img)
    out = QPixmap(pm.size())
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.drawPixmap(0, 0, pm)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    p.fillRect(out.rect(), color)
    p.end()
    return QIcon(out)


# ===========================================================================
# QSS stylesheet generation
# ===========================================================================

def build_qss(theme_name="dark", flush=False):
    """Generate a complete QSS stylesheet for the given theme.

    flush=True（窗口最大化时）：面板与搜索框左侧改直角，
    内容贴合屏幕左缘，消除圆角在屏幕边缘形成的缺口观感。
    """
    apply_theme(theme_name)
    c = C
    pane_radius = "0" if flush else "8px"
    flush_rules = ""
    if flush:
        flush_rules = ("QLineEdit#searchEdit { border-top-left-radius: 0; border-bottom-left-radius: 0; }"
                       "QTabBar::tab:first:selected { border-top-left-radius: 0; }")
    return f"""
    QMainWindow, QDialog {{ background-color: {c['BG']}; }}
    QWidget {{ color: {c['TEXT']}; font-family: "Microsoft YaHei UI","Segoe UI",sans-serif; font-size: 13px; }}
    QToolTip {{ background: #f7f7f7; color: #1f2329; border: 1px solid {c['BORDER']};
        padding: 4px 8px; border-radius: 4px; }}
    QTabWidget::pane {{ border: none; background: {c['PANEL_ALPHA']}; border-radius: {pane_radius}; }}
    QTabBar::tab {{ background: transparent; color: {c['TEXT_SEC']}; padding: 8px 18px; margin-right: 2px;
        border-top-left-radius: 6px; border-top-right-radius: 6px; font-weight: bold; }}
    QTabBar::tab:selected {{ background: {c['PANEL_ALPHA']}; color: {c['ACCENT']}; }}
    QTabBar::tab:hover:!selected {{ color: {c['TEXT']}; background: {c['SURFACE3']}; }}
    QTableWidget {{ background: transparent; alternate-background-color: rgba(128,128,128,18);
        border: none; gridline-color: rgba(128,128,128,30);
        selection-background-color: {c['ACCENT_DIM']}; selection-color: {c['TEXT']}; }}
    QTableWidget::item {{ padding: 6px 8px; border-bottom: 1px solid rgba(128,128,128,25); }}
    QHeaderView::section {{ background: rgba(128,128,128,22); color: {c['TEXT_MUTED']}; padding: 6px 8px;
        border: none; border-bottom: 2px solid {c['BORDER']}; font-weight: bold; font-size: 11px; }}
    QPushButton {{ background: {c['SURFACE2']}; color: {c['TEXT_SEC']}; border: 1px solid {c['BORDER']};
        border-radius: 6px; padding: 6px 14px; font-size: 12px; }}
    QPushButton:hover {{ background: {c['SURFACE3']}; color: {c['TEXT']}; border-color: {c['BORDER_LT']}; }}
    QPushButton[cssClass="accent"] {{ background: {c['ACCENT']}; color: #fff; border: none; font-weight: bold; }}
    QPushButton[cssClass="accent"]:hover {{ background: {c['ACCENT_HV']}; }}
    QPushButton[cssClass="danger"] {{ color: {c['DANGER']}; border-color: {c['DANGER']}; }}
    QPushButton[cssClass="danger"]:hover {{ background: {c['DANGER']}; color: #fff; }}
    QLineEdit {{ background: {c['SURFACE2']}; color: {c['TEXT']}; border: 1px solid {c['BORDER']};
        border-radius: 6px; padding: 6px 10px; font-size: 12px; }}
    QLineEdit:focus {{ border-color: {c['ACCENT']}; }}
    QComboBox {{ background: {c['SURFACE2']}; color: {c['TEXT_SEC']}; border: 1px solid {c['BORDER']};
        border-radius: 6px; padding: 4px 10px; font-size: 11px; }}
    QComboBox::drop-down {{ border: none; width: 20px; }}
    QComboBox QAbstractItemView {{ background: {c['PANEL_ALPHA']}; color: {c['TEXT']};
        selection-background-color: {c['ACCENT_DIM']}; border: 1px solid {c['BORDER']};
        border-radius: 8px; }}
    QSplitter::handle {{ background: {c['BORDER']}; width: 2px; height: 2px; }}
    QLabel {{ background: transparent; }}
    QTextEdit {{ background: transparent; color: {c['TEXT']}; border: none;
        font-family: "Consolas","Microsoft YaHei UI",monospace; font-size: 12px; }}
    QListWidget {{ background: transparent; border: none; font-size: 11px; outline: none; }}
    QListWidget::item {{ padding: 7px 10px; margin: 1px 2px; border-radius: 6px;
        color: {c['TEXT_SEC']}; background: transparent; }}
    QListWidget::item:selected {{ background: {c['ACCENT_DIM']}; color: {c['TEXT']}; }}
    QListWidget::item:hover:!selected {{ background: {c['SURFACE3']}; color: {c['TEXT']}; }}
    QMenu {{ background: {c['SURFACE2']}; color: {c['TEXT']}; border: 1px solid {c['BORDER']};
        border-radius: 8px; padding: 4px; }}
    QMenu::item {{ padding: 6px 24px; border-radius: 4px; }}
    QMenu::item:selected {{ background: {c['ACCENT_DIM']}; color: {c['ACCENT']}; }}
    QMenu::separator {{ height: 1px; background: {c['BORDER']}; margin: 4px 8px; }}
    QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0; }}
    QScrollBar::handle:vertical {{ background: {c['BORDER_LT']}; border-radius: 4px; min-height: 30px; }}
    QScrollBar::handle:vertical:hover {{ background: {c['TEXT_MUTED']}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar:horizontal {{ background: transparent; height: 8px; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
    QScrollBar::handle:horizontal {{ background: {c['BORDER_LT']}; border-radius: 4px; min-width: 30px; }}
    QCheckBox {{ color: {c['TEXT']}; spacing: 8px; }}
    QCheckBox::indicator {{ width: 18px; height: 18px; border: 2px solid {c['BORDER_LT']};
        border-radius: 4px; background: {c['SURFACE2']}; }}
    QCheckBox::indicator:checked {{ background: {c['ACCENT']}; border-color: {c['ACCENT']}; }}
    QGroupBox {{ background: {c['PANEL_ALPHA2']}; border: 1px solid {c['BORDER']}; border-radius: 8px;
        margin-top: 12px; padding-top: 16px; font-weight: bold; font-size: 11px; color: {c['TEXT_MUTED']}; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 14px; padding: 0 6px; }}
    QScrollArea {{ background: transparent; border: none; }}
    QFrame[cssClass="glass"] {{ background: {c['PANEL_ALPHA']}; border: 1px solid {c['BORDER']};
        border-radius: 10px; }}
    QFrame[cssClass="glass2"] {{ background: {c['PANEL_ALPHA2']}; border: 1px solid {c['BORDER']};
        border-radius: 8px; }}
    {flush_rules}
    """

# ===========================================================================
# i18n — complete STRINGS dictionary (zh + en)
# ===========================================================================
STRINGS = {
    "zh": {
        "win_title": "YouBoard · 剪贴板历史", "brand_sub": "剪贴板历史",
        "manage": " 管理 ▾ ", "settings_btn": " ⚙ 设置 ",
        "total_records": "共 {n} 条记录", "monitor_live": "实时监控中",
        "monitor_off": "未监控", "monitor_stopped": "监控已停止",
        "type_text": "文本", "type_image": "图片", "type_file": "文件", "type_url": "网址",
        "panel_preview": " 预览 ", "panel_snapshots": " 历史快照 ", "panel_urls": " 网址 ",
        "preview_placeholder": "选择一条记录\n即可预览",
        "btn_restore": "恢复选中状态", "btn_clear_history": "清空历史",
        "sort_default": "默认(时间最新)", "sort_oldest": "时间(最早)",
        "sort_name_az": "文件名(A-Z)", "sort_name_za": "文件名(Z-A)",
        "sort_fmt_az": "格式(A-Z)", "sort_fmt_za": "格式(Z-A)",
        "sort_size_desc": "大小(最大)", "sort_size_asc": "大小(最小)",
        "btn_copy": "复制  Enter", "btn_pin": "置顶", "btn_unpin": "取消置顶",
        "btn_delete": "删除  Del", "btn_export": "导出", "btn_open": "打开  双击",
        "col_time": "时间", "col_preview": "内容预览", "col_filename": "文件名",
        "col_format": "格式", "col_dims": "尺寸", "col_size": "大小",
        "col_count": "数量", "col_files": "文件列表", "col_url": "网址",
        "empty_state": "还没有记录\n复制任意内容即可自动捕获",
        "count_total": "共 {total} 条 · 置顶 {pinned}",
        "count_shown": "显示 {shown} / {total} 条 · 置顶 {pinned}",
        "count_match": "匹配 {n} 条", "selected_n": "已选 {n} 项", "no_ext": "无后缀",
        "hint_text": "Enter/双击 复制 · Space 置顶 · Del 删除 · Ctrl+A 全选 · F5 刷新",
        "hint_image": "Enter 复制图片 · Ctrl+O 打开 · Ctrl+E 导出",
        "hint_file": "双击 打开文件 · Enter 复制文件 · Ctrl+O 打开 · 右键查看更多",
        "hint_url": "双击/Enter 在浏览器打开 · Space 置顶 · Del 删除 · Ctrl+A 全选",
        "st_refreshed": "已刷新", "st_captured": "捕获到新的剪贴板内容",
        "st_nothing_to_copy": "没有可复制的记录",
        "st_copied_chars": "已复制（{n} 字符）", "st_image_copied": "图片已复制到剪贴板",
        "st_image_missing": "图片文件未找到", "st_files_copied": "已复制 {n} 个文件",
        "st_paths_missing": "记录的文件路径已不存在",
        "st_opened_viewer": "已用默认看图软件打开", "st_opened_url": "已在浏览器中打开网址",
        "st_path_missing": "文件路径已不存在", "st_opened_file": "已打开文件",
        "st_revealed": "已在资源管理器中定位（共 {n} 个文件）",
        "st_already_pinned": "选中的都已置顶", "st_pinned": "已置顶 {n} 条",
        "st_not_pinned": "选中的都未置顶", "st_unpinned": "已取消置顶 {n} 条",
        "st_pin_toggled": "置顶 {a} 条 · 取消 {b} 条", "st_deleted": "已删除 {n} 条",
        "st_no_type_records": "没有{t}记录可清空", "st_cleared_type": "已清空{t}",
        "st_no_unpinned_type": "没有{t}非置顶记录可清除",
        "st_cleared_unpinned_type": "已清除{t}非置顶记录",
        "st_no_unpinned": "没有非置顶记录可清除", "st_cleared_unpinned": "已清除全部非置顶记录",
        "st_nothing_to_clear": "没有可清空的内容", "st_cleared_all": "已全部清空",
        "st_nothing_to_export": "没有可导出的记录", "st_exported": "已导出至 {name}",
        "st_export_files_hint": "文件记录引用的是外部路径，可用「复制路径」",
        "st_copied_image_path": "已复制图片文件路径", "st_copied_paths": "已复制文件路径列表",
        "st_copied_preview": "已复制预览文本（{n} 字符）",
        "st_autostart_on": "已开启开机自启动", "st_autostart_off": "已关闭开机自启动",
        "st_autostart_failed": "设置开机自启动失败",
        "st_restored": "已恢复历史状态", "st_history_cleared": "历史记录已清空",
        "snap_select_first": "请先选择一条历史快照", "snap_empty": "历史记录为空",
        "snap_pin": "置顶 {n} 条（{t}）", "snap_unpin": "取消置顶 {n} 条（{t}）",
        "snap_toggle_pin": "切换置顶（{t}）", "snap_delete": "删除 {n} 条（{t}）",
        "snap_clear_type": "清空分类：{t}（{n} 条）",
        "snap_clear_type_unpinned": "清空{t}非置顶（{n} 条）",
        "snap_clear_unpinned": "清除非置顶（{n} 条）",
        "snap_clear_all": "清空全部（{n} 条）", "snap_before_restore": "恢复前：当前状态",
        "preview_truncated": "\n\n…（内容过长，已截断显示）",
        "chip_chars": " {n} 字符 ", "chip_lines": " {n} 行 ",
        "preview_unavailable": "（预览不可用）",
        "preview_dblclick_viewer": "双击用默认看图软件打开",
        "preview_dblclick_url": "双击在浏览器中打开",
        "chip_files": " {n} 个文件 ", "preview_dblclick_open": "双击打开文件",
        "dlg_error": "错误", "dlg_info": "提示",
        "dlg_confirm_delete": "确认删除", "dlg_confirm_clear": "确认清空",
        "dlg_confirm_restore": "确认恢复", "dlg_confirm_remove": "确认清除",
        "msg_copy_failed": "复制失败：{err}", "msg_open_failed": "打开失败：{err}",
        "msg_file_not_found": "文件未找到：\n{path}",
        "msg_delete_confirm": "确定要删除选中的 {n} 条记录吗？",
        "msg_clear_type": "确定要清空全部{t}记录（{n} 条）吗？",
        "msg_clear_type_unpinned": "确定要清除{t}分类的非置顶记录吗？\n（删除 {n} 条，保留置顶）",
        "msg_clear_unpinned": "确定要清除全部非置顶记录吗？\n（删除 {n} 条，保留置顶）",
        "msg_clear_all": "确定要清空全部剪贴板历史吗？（共 {n} 条）",
        "msg_clear_history": "确定要清空全部 {n} 条历史记录吗？",
        "msg_restore_confirm": "确定要恢复到以下状态吗？\n\n{ts}\n{desc}\n\n当前状态将先存入历史。",
        "m_copy_content": "复制内容  (Enter)", "m_export_txt": "导出为 .txt…",
        "m_copy_image": "复制图片到剪贴板  (Enter)",
        "m_open_viewer": "用默认看图软件打开  (Ctrl+O)",
        "m_open_viewer_plain": "用默认看图软件打开",
        "m_open_folder": "打开所在文件夹", "m_copy_path": "复制文件路径",
        "m_export_image": "导出图片…  (Ctrl+E)",
        "m_copy_files": "复制文件到剪贴板  (Enter)",
        "m_open_locate": "打开 / 定位文件  (Ctrl+O)", "m_copy_paths": "复制路径列表",
        "m_toggle_pin": "置顶 / 取消置顶  (Space)", "m_delete": "删除  (Del)",
        "m_delete_n": "删除（{n} 条）  (Del)",
        "m_copy_selection": "复制选中文本", "m_copy_all": "复制全部内容",
        "m_select_all": "全选", "m_open_url": "在浏览器中打开网址",
        "m_refresh": "刷新列表  (F5)",
        "m_clear_type": "清空「{t}」分类…", "m_clear_type_unpinned": "清除「{t}」非置顶…",
        "m_clear_unpinned": "清除全部非置顶…", "m_clear_all": "清空全部…",
        "settings_title": "YouBoard · 设置",
        "set_language": "语言 / LANGUAGE", "set_lang_zh": "简体中文", "set_lang_en": "English",
        "set_lang_note": "切换语言后应用将立即重启",
        "set_general": "通用 / GENERAL", "set_autostart": "开机自启动",
        "set_autostart_desc": "登录 Windows 后自动启动 YouBoard 并监听剪贴板",
        "set_theme": "主题 / THEME", "set_theme_dark": "暗色", "set_theme_light": "亮色",
        "set_theme_note": "切换主题后应用将立即重启",
        "set_bg": "背景 / BACKGROUND", "set_bg_select": "选择背景图片",
        "set_bg_wallpaper": "使用当前壁纸",
        "set_bg_history": "历史壁纸",
        "bg_h_use": "设为背景",
        "bg_h_del": "删除该背景",
        "set_bg_wall_err": "无法获取系统壁纸，请先在系统里设置桌面壁纸",
        "set_bg_clear": "恢复默认",
        "set_bg_hint": "推荐 1920×1080 或更大，支持 PNG / JPG / BMP / GIF（动态）",
        "set_bg_current": "当前背景：默认",
        "set_about": "关于 / ABOUT", "set_data_location": "数据位置",
        "btn_save": "保存", "btn_cancel": "取消",
        "ft_text": "文本文件", "ft_all": "所有文件",
        "cli_empty": "（空）没有剪贴板记录",
        "cli_h_pin": "置顶", "cli_h_type": "类型", "cli_h_time": "时间",
        "cli_h_preview": "预览",
        "cli_not_found": "未找到匹配「{kw}」的记录",
        "cli_found": "找到 {n} 条匹配记录：",
        "cli_cleared": "已清空全部剪贴板历史",
        "cli_daemon_started": "YouBoard 后台守护已启动",
        "cli_history_file": "历史文件：{path}",
        "cli_ctrl_c": "按 Ctrl+C 停止",
        "cli_stopped": "已停止",
        "tray_show": "显示 YouBoard",
        "tray_quit": "退出",
        "tray_session": "临时会话（退出即清空）",
        "tray_phone": "发送到手机…",
        "tray_phone_stop": "停止手机传输（端口 {port}）",
        "set_session_title": "临时会话",
        "set_session_desc": "开启后剪贴板内容照常记录（正常使用）；退出应用或关闭开关时，本次开启后产生的记录（历史、快照、图片与文件缓存）将全部清除",
        "session_started": "临时会话已开启：本次运行记录退出即清空",
        "session_cleared": "临时会话已关闭，本次运行记录已清除",
        "btn_purge_missing": "清理失效",
        "file_missing": "已失效",
        "purge_done": "已清理 {n} 条失效记录",
        "set_hotkey_title": "全局快捷键",
        "set_hotkey_desc": "按下快捷键显示/隐藏 YouBoard（如 alt+q、ctrl+shift+v）",
        "set_hotkeys_entry": "快捷键设置",
        "set_hotkeys_title": "动作快捷键",
        "hk_copy": "复制选中",
        "hk_delete": "删除选中",
        "hk_pin": "置顶 / 取消置顶",
        "hk_next_tab": "下一个分类",
        "hk_prev_tab": "上一个分类",
        "hk_change": "更改",
        "hk_dialog_title": "设置快捷键",
        "hk_dialog_hint": "点击下方按钮后，按下新的快捷键组合（支持 Ctrl/Alt/Shift/Win + 字母、数字、F1-F12 及 Tab/Enter/Del/Space）",
        "btn_ok": "确定",
        "set_widget_title": "桌面小组件",
        "set_widget_desc": "在桌面显示一个置顶小窗口，实时更新当前剪贴板内容与最近记录，点击条目即可复制（默认开启）",
        "widget_title": "剪贴板 · 实时",
        "widget_empty": "暂无剪贴内容",
        "widget_history": "最近记录（点击可复制）",
        "widget_click_copy": "点击复制回剪贴板",
        "st_copied": "已复制回剪贴板",
        "set_phone": "手机传输 / PHONE",
        "set_phone_desc": "用手机扫码后，可在手机浏览器中查看 / 复制剪贴板历史，或把手机上的文字一键发回电脑（手机与电脑需在同一 Wi-Fi / 局域网）",
        "set_phone_open": "打开传输窗口",
        "phone_title": "发送到手机",
        "phone_starting": "正在启动服务…",
        "phone_generating": "正在生成二维码…",
        "phone_ip_label": "IP 地址",
        "phone_scan_hint": "用手机相机 / 微信扫码",
        "phone_url_hint": "或在手机浏览器中打开：",
        "phone_status_running": "服务运行中 · 端口 {port}",
        "phone_status_clients": "已连接设备 {n} 台",
        "phone_copy_url": "复制链接",
        "phone_refresh": "刷新二维码",
        "phone_close": "关闭",
        "phone_same_lan": "请确保手机与电脑连接同一 Wi-Fi / 局域网",
        "phone_firewall": "若手机仍无法访问：首次监听时 Windows 防火墙会弹窗，请选择「允许访问」；应用也会尝试自动放行",
        "phone_still_running": "关闭本窗口后传输仍会继续运行，托盘菜单可随时停止",
        "phone_hint_same_wifi": "对方设备请连接同一个 Wi-Fi，不要用访客网络 / 手机热点",
        "phone_hint_vpn": "对方设备若开启 VPN / 梯子，请先关闭（本地局域网不走代理）",
        "phone_stopped": "手机传输已停止",
        "phone_copied": "链接已复制",
        "phone_no_qr": "缺少 qrcode 组件，无法生成二维码",
        "phone_start_failed": "传输服务启动失败：{err}",
        "phone_received": "已收到来自手机的文字",
        "set_sync": "云同步 / CLOUD SYNC",
        "set_sync_desc": "把加密后的剪贴板历史同步到云端（GitHub Gist / WebDAV），换设备输入同一同步密码即可恢复",
        "set_sync_open": "打开同步窗口",
        "set_sync_backend": "后端",
        "set_sync_off": "不使用",
        "set_sync_gist_token": "GitHub Token（需 gist 权限）",
        "set_sync_dav_url": "WebDAV 目录地址",
        "set_sync_dav_user": "账号",
        "set_sync_dav_pass": "密码",
        "set_sync_pass": "同步密码（加密用，至少 4 位）",
        "btn_sync_upload": "上传到云端",
        "btn_sync_download": "从云端下载",
        "btn_sync_clear": "清除云配置",
        "sync_uploaded": "已上传到云端",
        "sync_downloaded": "已从云端下载并合并",
        "sync_syncing": "同步中…",
        "sync_pass_hint": "请先填写同步密码（至少 4 位）",
        "sync_last": "上次同步：{time}",
        "sync_never": "尚未同步",
        "sync_cleared": "云同步配置已清除",
        "sync_gist_id": "已关联 Gist：{gid}",
        "set_check_update": "检查更新",
        "upd_title": "检查更新",
        "upd_latest": "已是最新版本 v{v}",
        "upd_new_title": "发现新版本",
        "upd_new_msg": "当前版本: v{cur}\n最新版本: v{new} ({name})\n\n是否立即更新？（将下载并替换当前程序）",
        "upd_failed": "检查失败: {e}",
        "upd_network_err": "无法连接到更新服务器，请检查网络后重试",
        "upd_rate_limit": "请求过于频繁，请稍后再试（GitHub 限流）",
    },
    "en": {
        "win_title": "YouBoard · Clipboard History", "brand_sub": "Clipboard History",
        "manage": " Manage ▾ ", "settings_btn": " ⚙ Settings ",
        "total_records": "{n} records", "monitor_live": "Live monitoring",
        "monitor_off": "Not monitoring", "monitor_stopped": "Monitor stopped",
        "type_text": "Text", "type_image": "Images", "type_file": "Files", "type_url": "URLs",
        "panel_preview": " Preview ", "panel_snapshots": " Snapshots ", "panel_urls": " URLs ",
        "preview_placeholder": "Select a record\nto preview",
        "btn_restore": "Restore selected", "btn_clear_history": "Clear history",
        "sort_default": "Default (newest)", "sort_oldest": "Oldest first",
        "sort_name_az": "Name (A-Z)", "sort_name_za": "Name (Z-A)",
        "sort_fmt_az": "Format (A-Z)", "sort_fmt_za": "Format (Z-A)",
        "sort_size_desc": "Size (largest)", "sort_size_asc": "Size (smallest)",
        "btn_copy": "Copy  Enter", "btn_pin": "Pin", "btn_unpin": "Unpin",
        "btn_delete": "Delete  Del", "btn_export": "Export", "btn_open": "Open  Dbl-click",
        "col_time": "Time", "col_preview": "Preview", "col_filename": "Filename",
        "col_format": "Format", "col_dims": "Dimensions", "col_size": "Size",
        "col_count": "Count", "col_files": "Files", "col_url": "URL",
        "empty_state": "No records yet\nCopy anything and it will be captured",
        "count_total": "{total} records · {pinned} pinned",
        "count_shown": "Showing {shown} / {total} · {pinned} pinned",
        "count_match": "{n} matched", "selected_n": "{n} selected", "no_ext": "no ext",
        "hint_text": "Enter/double-click copy · Space pin · Del delete · Ctrl+A select all · F5 refresh",
        "hint_image": "Enter copy image · Ctrl+O open · Ctrl+E export",
        "hint_file": "Double-click open file · Enter copy files · Ctrl+O open · Right-click for more",
        "hint_url": "Double-click/Enter open in browser · Space pin · Del delete · Ctrl+A select all",
        "st_refreshed": "Refreshed", "st_captured": "New clipboard content captured",
        "st_nothing_to_copy": "Nothing to copy",
        "st_copied_chars": "Copied ({n} chars)", "st_image_copied": "Image copied to clipboard",
        "st_image_missing": "Image file not found", "st_files_copied": "Copied {n} file(s)",
        "st_paths_missing": "Recorded file paths no longer exist",
        "st_opened_viewer": "Opened in default viewer", "st_opened_url": "Opened URL in browser",
        "st_path_missing": "File path no longer exists", "st_opened_file": "File opened",
        "st_revealed": "Revealed in Explorer ({n} files)",
        "st_already_pinned": "Selection already pinned", "st_pinned": "Pinned {n}",
        "st_not_pinned": "Selection not pinned", "st_unpinned": "Unpinned {n}",
        "st_pin_toggled": "Pinned {a} · Unpinned {b}", "st_deleted": "Deleted {n}",
        "st_no_type_records": "No {t} records to clear", "st_cleared_type": "Cleared {t}",
        "st_no_unpinned_type": "No unpinned {t} records to remove",
        "st_cleared_unpinned_type": "Removed unpinned {t} records",
        "st_no_unpinned": "No unpinned records to remove",
        "st_cleared_unpinned": "Removed all unpinned records",
        "st_nothing_to_clear": "Nothing to clear", "st_cleared_all": "All cleared",
        "st_nothing_to_export": "Nothing to export", "st_exported": "Exported to {name}",
        "st_export_files_hint": "File records reference external paths - use 'Copy path list'",
        "st_copied_image_path": "Image path copied", "st_copied_paths": "File path list copied",
        "st_copied_preview": "Copied preview text ({n} chars)",
        "st_autostart_on": "Start with Windows enabled", "st_autostart_off": "Start with Windows disabled",
        "st_autostart_failed": "Failed to change autostart setting",
        "st_restored": "Snapshot restored", "st_history_cleared": "Snapshots cleared",
        "snap_select_first": "Select a snapshot first", "snap_empty": "No snapshots",
        "snap_pin": "Pinned {n} ({t})", "snap_unpin": "Unpinned {n} ({t})",
        "snap_toggle_pin": "Toggled pin ({t})", "snap_delete": "Deleted {n} ({t})",
        "snap_clear_type": "Cleared {t} ({n})",
        "snap_clear_type_unpinned": "Removed unpinned {t} ({n})",
        "snap_clear_unpinned": "Removed unpinned ({n})",
        "snap_clear_all": "Cleared all ({n})", "snap_before_restore": "Before restore: current state",
        "preview_truncated": "\n\n…(content too long, truncated)",
        "chip_chars": " {n} chars ", "chip_lines": " {n} lines ",
        "preview_unavailable": "(Preview unavailable)",
        "preview_dblclick_viewer": "Double-click to open with default viewer",
        "preview_dblclick_url": "Double-click to open in browser",
        "chip_files": " {n} files ", "preview_dblclick_open": "Double-click to open file",
        "dlg_error": "Error", "dlg_info": "Notice",
        "dlg_confirm_delete": "Confirm delete", "dlg_confirm_clear": "Confirm clear",
        "dlg_confirm_restore": "Confirm restore", "dlg_confirm_remove": "Confirm remove",
        "msg_copy_failed": "Copy failed: {err}", "msg_open_failed": "Open failed: {err}",
        "msg_file_not_found": "File not found:\n{path}",
        "msg_delete_confirm": "Delete {n} selected record(s)?",
        "msg_clear_type": "Clear all {t} records ({n})?",
        "msg_clear_type_unpinned": "Remove unpinned {t} records?\n({n} will be deleted, pinned ones are kept)",
        "msg_clear_unpinned": "Remove all unpinned records?\n({n} will be deleted, pinned ones are kept)",
        "msg_clear_all": "Clear the entire clipboard history? ({n} records)",
        "msg_clear_history": "Clear all {n} snapshots?",
        "msg_restore_confirm": "Restore to the following state?\n\n{ts}\n{desc}\n\nThe current state will be saved to history first.",
        "m_copy_content": "Copy content  (Enter)", "m_export_txt": "Export as .txt…",
        "m_copy_image": "Copy image to clipboard  (Enter)",
        "m_open_viewer": "Open in default viewer  (Ctrl+O)",
        "m_open_viewer_plain": "Open in default viewer",
        "m_open_folder": "Open containing folder", "m_copy_path": "Copy file path",
        "m_export_image": "Export image…  (Ctrl+E)",
        "m_copy_files": "Copy files to clipboard  (Enter)",
        "m_open_locate": "Open / locate files  (Ctrl+O)", "m_copy_paths": "Copy path list",
        "m_toggle_pin": "Pin / Unpin  (Space)", "m_delete": "Delete  (Del)",
        "m_delete_n": "Delete ({n})  (Del)",
        "m_copy_selection": "Copy selection", "m_copy_all": "Copy all",
        "m_select_all": "Select all", "m_open_url": "Open URL in browser",
        "m_refresh": "Refresh list  (F5)",
        "m_clear_type": "Clear '{t}'…", "m_clear_type_unpinned": "Remove unpinned '{t}'…",
        "m_clear_unpinned": "Remove all unpinned…", "m_clear_all": "Clear all…",
        "settings_title": "YouBoard · Settings",
        "set_language": "Language / 语言", "set_lang_zh": "简体中文", "set_lang_en": "English",
        "set_lang_note": "The app restarts immediately after switching language",
        "set_general": "General / 通用", "set_autostart": "Start with Windows",
        "set_autostart_desc": "Automatically start YouBoard and monitor the clipboard when you sign in",
        "set_theme": "Theme / 主题", "set_theme_dark": "Dark", "set_theme_light": "Light",
        "set_theme_note": "The app restarts immediately after switching theme",
        "set_bg": "Background / 背景", "set_bg_select": "Choose background image",
        "set_bg_wallpaper": "Use current wallpaper",
        "set_bg_history": "Wallpaper history",
        "bg_h_use": "Use as background",
        "bg_h_del": "Delete this background",
        "set_bg_wall_err": "Cannot get the system wallpaper. Set a desktop wallpaper first.",
        "set_bg_clear": "Reset to default",
        "set_bg_hint": "Recommended 1920×1080 or larger, PNG / JPG / BMP / GIF (animated)",
        "set_bg_current": "Current: Default",
        "set_about": "About / 关于", "set_data_location": "Data location",
        "btn_save": "Save", "btn_cancel": "Cancel",
        "ft_text": "Text files", "ft_all": "All files",
        "cli_empty": "(empty) No clipboard records",
        "cli_h_pin": "Pin", "cli_h_type": "Type", "cli_h_time": "Time",
        "cli_h_preview": "Preview",
        "cli_not_found": "No records matching '{kw}'",
        "cli_found": "Found {n} matching record(s):",
        "cli_cleared": "All clipboard history cleared",
        "cli_daemon_started": "YouBoard daemon started",
        "cli_history_file": "History file: {path}",
        "cli_ctrl_c": "Press Ctrl+C to stop",
        "cli_stopped": "Stopped",
        "tray_show": "Show YouBoard",
        "tray_quit": "Quit",
        "tray_session": "Temporary Session (clear on exit)",
        "tray_phone": "Send to Phone…",
        "tray_phone_stop": "Stop Phone Transfer (port {port})",
        "set_session_title": "Temporary Session",
        "set_session_desc": "While on, clipboard content is recorded normally; when you quit the app or turn it off, everything recorded since enabling (history, snapshots, image & file cache) is wiped",
        "session_started": "Temporary session on: this run's records will be cleared on exit",
        "session_cleared": "Temporary session off, this run's records cleared",
        "btn_purge_missing": "Purge Missing",
        "file_missing": "missing",
        "purge_done": "Purged {n} missing entries",
        "set_hotkey_title": "HOTKEY",
        "set_hotkey_desc": "Press shortcut to show/hide YouBoard (e.g. alt+q, ctrl+shift+v)",
        "set_hotkeys_entry": "Hotkey Settings",
        "set_hotkeys_title": "Action Hotkeys",
        "hk_copy": "Copy",
        "hk_delete": "Delete",
        "hk_pin": "Pin / Unpin",
        "hk_next_tab": "Next tab",
        "hk_prev_tab": "Previous tab",
        "hk_change": "Change",
        "hk_dialog_title": "Set Shortcut",
        "hk_dialog_hint": "Click below, then press the new key combination (Ctrl/Alt/Shift/Win + letter, digit, F1-F12, Tab/Enter/Del/Space)",
        "btn_ok": "OK",
        "set_widget_title": "Desktop Widget",
        "set_widget_desc": "Show a small always-on-top window with live clipboard content and recent history; click an item to copy it (on by default)",
        "widget_title": "Clipboard · Live",
        "widget_empty": "Clipboard is empty",
        "widget_history": "Recent (click to copy)",
        "widget_click_copy": "Click to copy back to clipboard",
        "st_copied": "Copied back to clipboard",
        "set_phone": "Phone Transfer / PHONE",
        "set_phone_desc": "Scan the QR code with your phone to browse / copy clipboard history in the browser, or send text from your phone to the PC (phone and PC must be on the same Wi-Fi / LAN)",
        "set_phone_open": "Open Transfer Window",
        "phone_title": "Send to Phone",
        "phone_starting": "Starting server…",
        "phone_generating": "Generating QR code…",
        "phone_ip_label": "IP Address",
        "phone_scan_hint": "Scan with phone camera / WeChat",
        "phone_url_hint": "Or open in phone browser:",
        "phone_status_running": "Server running · Port {port}",
        "phone_status_clients": "{n} device(s) connected",
        "phone_copy_url": "Copy link",
        "phone_refresh": "Refresh QR",
        "phone_close": "Close",
        "phone_same_lan": "Make sure your phone and PC are on the same Wi-Fi / LAN",
        "phone_firewall": "If your phone still can't access: when Windows Firewall prompts on first listen, choose \"Allow access\"; the app also tries to add a rule automatically",
        "phone_still_running": "Transfer keeps running after closing this window (stop it from the tray menu)",
        "phone_hint_same_wifi": "The other device must join the same Wi-Fi — not a guest network or phone hotspot",
        "phone_hint_vpn": "Turn off VPN / proxy on the other device (local LAN traffic should not go through a proxy)",
        "phone_stopped": "Phone transfer stopped",
        "phone_copied": "Link copied",
        "phone_no_qr": "qrcode component missing, cannot generate QR",
        "phone_start_failed": "Failed to start transfer server: {err}",
        "phone_received": "Received text from phone",
        "set_sync": "Cloud Sync / CLOUD SYNC",
        "set_sync_desc": "Sync your encrypted clipboard history to the cloud (GitHub Gist / WebDAV); restore on another device with the same sync passphrase",
        "set_sync_open": "Open Sync Window",
        "set_sync_backend": "Backend",
        "set_sync_off": "Off",
        "set_sync_gist_token": "GitHub Token (gist scope)",
        "set_sync_dav_url": "WebDAV directory URL",
        "set_sync_dav_user": "Username",
        "set_sync_dav_pass": "Password",
        "set_sync_pass": "Sync passphrase (encryption, min 4 chars)",
        "btn_sync_upload": "Upload",
        "btn_sync_download": "Download",
        "btn_sync_clear": "Clear config",
        "sync_uploaded": "Uploaded to cloud",
        "sync_downloaded": "Downloaded and merged",
        "sync_syncing": "Syncing…",
        "sync_pass_hint": "Enter a sync passphrase (min 4 chars) first",
        "sync_last": "Last sync: {time}",
        "sync_never": "Never synced",
        "sync_cleared": "Cloud sync config cleared",
        "sync_gist_id": "Linked Gist: {gid}",
        "set_check_update": "Check Update",
        "upd_title": "检查更新 · Check Update",
        "upd_latest": "已是最新版本 v{v}\nAlready on the latest version v{v}",
        "upd_new_title": "发现新版本 · New Version",
        "upd_new_msg": "当前版本: v{cur}\n最新版本: v{new} ({name})\n是否立即更新？（将下载并替换当前程序）\n\nCurrent: v{cur} → Latest: v{new} ({name})\nUpdate now? (Will download and replace the program)",
        "upd_failed": "检查失败: {e}\nCheck failed: {e}",
        "upd_network_err": "Cannot connect to update server. Please check your network and try again.\n无法连接到更新服务器，请检查网络后重试",
        "upd_rate_limit": "Too many requests. Please try again later (GitHub rate limit).\n请求过于频繁，请稍后再试（GitHub 限流）",
    },
}

LANG = "zh"

# ===========================================================================
# Utility functions
# ===========================================================================

def tr(key, **kw):
    """Translate a key using the current language, with optional format kwargs."""
    s = STRINGS.get(LANG, STRINGS["zh"]).get(key)
    if s is None:
        s = STRINGS["zh"].get(key, key)
    if kw:
        try:
            return s.format(**kw)
        except (KeyError, IndexError, ValueError):
            return s
    return s


def apply_language(lang):
    """Set the active language for tr()."""
    global LANG
    LANG = lang if lang in STRINGS else "zh"


def fmt_size(n):
    """Format a byte count into a human-readable string."""
    if n < 1024:
        return f"{n} B"
    elif n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def fmt_image_type(fmt_str):
    """Normalise image format strings for display."""
    fmt = fmt_str.upper()
    if fmt == "WEBP":
        return "Webp"
    if fmt == "DIB":
        return "PNG"
    return fmt


def _lerp_color(hex1, hex2, t):
    """Linearly interpolate between two hex colors. t in [0,1]."""
    r1, g1, b1 = int(hex1[1:3], 16), int(hex1[3:5], 16), int(hex1[5:7], 16)
    r2, g2, b2 = int(hex2[1:3], 16), int(hex2[3:5], 16), int(hex2[5:7], 16)
    r = int(r1 + (r2 - r1) * t)
    g = int(g1 + (g2 - g1) * t)
    b = int(b1 + (b2 - b1) * t)
    return f"#{r:02x}{g:02x}{b:02x}"


def _filename_sort_key(name):
    """Sort key for filenames: CJK first, then alpha, then digits, then symbols."""
    if not name:
        return (4, "")
    ch = name[0]
    if '\u4e00' <= ch <= '\u9fff' or '\u3400' <= ch <= '\u4dbf':
        group = 0
    elif ch.isalpha():
        group = 1
    elif ch.isdigit():
        group = 2
    else:
        group = 3
    try:
        return (group, locale.strxfrm(name))
    except Exception:
        return (group, name.lower())


def _extract_extensions(paths):
    """Extract a comma-separated set of file extensions from a path list."""
    seen = set()
    for p in paths:
        ext = os.path.splitext(p)[1].lstrip(".").upper()
        seen.add(ext if ext else tr("no_ext"))
    return ", ".join(sorted(seen)) if seen else "?"


# ===========================================================================
# AmbientLightBar — full-width ambient light strip (QWidget + paintEvent)
# ===========================================================================
_KEY_ROW_POS = {}
for _i, _ch in enumerate("1234567890-=qwertyuiop[]asdfghjkl;'zxcvbnm,./"):
    _KEY_ROW_POS[_ch] = _i / 44.0


class AmbientLightBar(QWidget):
    """Breathing hue drift + key ripple pulses + action surge light bar."""

    SEG_W = 9
    HEIGHT = 4

    def __init__(self, parent=None, theme="dark"):
        super().__init__(parent)
        self._theme = theme
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._pulses = []
        self._surge = 0.0
        self._surge_hue = 210.0
        self._t0 = time.perf_counter()
        self._last_tick = self._t0
        self.suppress_until = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)  # ~30 fps

    def pause(self):
        """Stop the animation timer (window hidden) to save CPU."""
        self._timer.stop()

    def resume(self):
        """Restart the animation timer (window shown)."""
        if not self._timer.isActive():
            self._last_tick = time.perf_counter()
            self._timer.start(33)

    def pulse(self, hue, x=None, strength=1.0):
        """Emit an expanding ring pulse at normalised position x."""
        if x is None:
            x = random.uniform(0.15, 0.85)
        self._pulses.append([float(x), float(hue) % 360.0,
                             time.perf_counter() - self._t0, strength])
        if len(self._pulses) > 14:
            del self._pulses[:len(self._pulses) - 14]

    def surge(self, hue, amount=1.0):
        """Light up the whole bar with exponential decay."""
        self._surge = max(self._surge, min(1.0, amount))
        self._surge_hue = float(hue) % 360.0

    def key_light(self, keysym, char):
        """Map a keypress to a position on the bar and emit a pulse."""
        ch = (char or "").lower()
        x = _KEY_ROW_POS.get(ch)
        if x is None:
            x = {"space": 0.5, "return": 0.94, "backspace": 0.06,
                 "delete": 0.97, "escape": 0.02, "tab": 0.04}.get(keysym.lower())
        if x is None:
            x = random.uniform(0.05, 0.95)
        hue = (abs(hash(keysym)) * 137.508) % 360.0
        self.pulse(hue, x, strength=0.95)

    def _tick(self):
        """Advance animation state; skip if suppressed (e.g. during scroll)."""
        now = time.perf_counter()
        if now < self.suppress_until:
            self._last_tick = now
            return
        # Pause when window is minimized
        win = self.window()
        if win and win.isMinimized():
            self._last_tick = now
            return
        dt = now - self._last_tick
        self._last_tick = now
        t = now - self._t0
        # Surge decay
        if self._surge > 0.001:
            self._surge *= math.exp(-dt * 2.6)
        else:
            self._surge = 0.0
        # Expire old pulses
        life = 1.15
        self._pulses = [p for p in self._pulses if t - p[2] < life]
        self.update()

    def paintEvent(self, event):
        """Draw colored rectangles for each segment."""
        w = self.width()
        if w < 20:
            return
        n = max(8, (w + self.SEG_W - 1) // self.SEG_W)
        t = time.perf_counter() - self._t0
        # Breathing: ~4.2s period
        breath = 0.5 + 0.5 * math.sin(t * 2.0 * math.pi / 4.2)
        if self._theme == "light":
            base_l, base_s = 0.52 + 0.13 * breath, 0.82
        else:
            base_l, base_s = 0.13 + 0.11 * breath, 0.62
        # Hue drifts along x-axis
        drift = t * 9.0
        surge_rgb = None
        if self._surge > 0.0:
            surge_rgb = colorsys.hls_to_rgb(self._surge_hue / 360.0, 0.55, 0.9)
        p = QPainter(self)
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(n):
            x = i / (n - 1) if n > 1 else 0.5
            hue = (drift + x * 46.0) % 360.0
            r, g, b = colorsys.hls_to_rgb(hue / 360.0, base_l, base_s)
            # Pulse rings with gaussian falloff
            for px, phue, pt0, pstr in self._pulses:
                age = t - pt0
                ring = age * 1.5
                d = abs(x - px)
                glow = math.exp(-((d - ring) ** 2) / 0.0162) * (1.0 - age / 1.15) * pstr
                if glow > 0.02:
                    pr, pg, pb = colorsys.hls_to_rgb(phue / 360.0, 0.58, 0.95)
                    r += pr * glow * 0.85
                    g += pg * glow * 0.85
                    b += pb * glow * 0.85
            # Surge overlay
            if surge_rgb:
                k = self._surge * 0.8
                r += surge_rgb[0] * k
                g += surge_rgb[1] * k
                b += surge_rgb[2] * k
            p.setBrush(QColor(min(255, int(r * 255)), min(255, int(g * 255)),
                              min(255, int(b * 255))))
            p.drawRect(i * self.SEG_W, 0, self.SEG_W + 1, self.HEIGHT)
        p.end()


# ===========================================================================
# ImageLoader — background thread for loading preview images
# ===========================================================================
class ImageLoader(QThread):
    finished = pyqtSignal(int, str, object)

    def __init__(self, path, gen, parent=None):
        super().__init__(parent)
        self.path = path
        self.gen = gen

    def run(self):
        try:
            from PIL import Image as PILImage
            img = PILImage.open(self.path)
            img.load()
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGB")
            if max(img.size) > PREVIEW_MAX:
                ratio = PREVIEW_MAX / max(img.size)
                img = img.resize((max(1, int(img.width * ratio)),
                                  max(1, int(img.height * ratio))), PILImage.LANCZOS)
            self.finished.emit(self.gen, self.path, img)
        except Exception:
            pass


# ===========================================================================
# Update Downloader (multi-threaded segmented download, maximizes bandwidth)
# ===========================================================================
class _DownloadWorker(QThread):
    """Download using parallel Range segments (like IDM) to saturate bandwidth.
    Falls back to mirror racing if server doesn't support Range requests."""
    progress = pyqtSignal(int, int)   # received_bytes, total_bytes
    status = pyqtSignal(str)          # status line
    finished_ok = pyqtSignal(str)     # downloaded temp file path
    failed = pyqtSignal(str)          # error message

    SEGMENTS = 8          # parallel segments per source (like download managers)
    CHUNK = 512 * 1024    # 512KB read buffer
    TIMEOUT = 12          # connection timeout seconds
    MAX_PARALLEL = 4      # mirror racing fallback: race up to 4 sources
    SPEED_CHECK_TIME = 5  # seconds to wait before judging speed
    MIN_SPEED = 200 * 1024  # 200KB/s minimum acceptable speed

    def __init__(self, urls, dest_path, parent=None):
        super().__init__(parent)
        self._urls = urls
        self._dest = dest_path
        self._abort = False
        self._slow_abort = False  # set when current source is too slow
        self._lock = threading.Lock()
        self._received = 0  # total bytes received across all segments
        self._total = 0

    def abort(self):
        self._abort = True

    def run(self):
        import concurrent.futures
        last_err = "未知错误"

        # Try each URL: attempt segmented download first, fallback to simple
        for url in self._urls:
            if self._abort:
                return
            host = url.split("/")[2] if "/" in url else url
            self.status.emit(f"正在探测: {host}")
            # Probe: check if server supports Range requests
            total, range_ok = self._probe(url)
            if self._abort:
                return
            if range_ok and total > 0:
                # Segmented parallel download (maximizes bandwidth)
                self.status.emit(f"多线程下载中: {host} ({self.SEGMENTS}线程)")
                ok, err = self._segmented_download(url, total)
                if ok:
                    self.finished_ok.emit(self._dest)
                    return
                last_err = err
            else:
                # Fallback: simple single-stream download from this URL
                self.status.emit(f"正在下载: {host}")
                ok, err = self._simple_download(url)
                if ok:
                    self.finished_ok.emit(self._dest)
                    return
                last_err = err
            if self._abort:
                return

        if not self._abort:
            self.failed.emit(last_err)

    def _probe(self, url):
        """HEAD request to get Content-Length and Accept-Ranges. Returns (total, range_ok)."""
        import urllib.request
        try:
            req = urllib.request.Request(url, method="HEAD", headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) YouBoard-Updater",
            })
            with urllib.request.urlopen(req, timeout=self.TIMEOUT) as resp:
                total = int(resp.headers.get("Content-Length", 0) or 0)
                accept_ranges = resp.headers.get("Accept-Ranges", "none").lower()
                range_ok = accept_ranges != "none"
                # Also check if server responds to a tiny range request
                if not range_ok and total > 0:
                    range_ok = self._test_range(url)
                return total, range_ok
        except Exception:
            return 0, False

    def _test_range(self, url):
        """Quick test: request first 1 byte with Range header."""
        import urllib.request
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) YouBoard-Updater",
                "Range": "bytes=0-0",
            })
            with urllib.request.urlopen(req, timeout=8) as resp:
                return resp.status == 206
        except Exception:
            return False

    def _segmented_download(self, url, total):
        """Download file in parallel segments using Range requests. Returns (ok, err).
        If speed is below MIN_SPEED after SPEED_CHECK_TIME, aborts to try next source."""
        import concurrent.futures
        import time as _time
        # Reset progress and slow flag for this source
        with self._lock:
            self._received = 0
            self._total = total
        self._slow_abort = False
        self.progress.emit(0, total)

        # Pre-allocate output file
        try:
            with open(self._dest, "wb") as f:
                f.seek(total - 1)
                f.write(b"\x00")
        except OSError as e:
            return False, str(e)

        # Calculate segment boundaries
        seg_size = total // self.SEGMENTS
        segments = []
        for i in range(self.SEGMENTS):
            start = i * seg_size
            end = (start + seg_size - 1) if i < self.SEGMENTS - 1 else (total - 1)
            segments.append((start, end))

        start_time = _time.monotonic()
        speed_checked = False
        errors = []

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.SEGMENTS) as pool:
            futures = [pool.submit(self._download_segment, url, start, end)
                       for start, end in segments]
            for fut in concurrent.futures.as_completed(futures):
                # Speed check: after warmup, if too slow, abort this source
                if not speed_checked:
                    elapsed = _time.monotonic() - start_time
                    if elapsed >= self.SPEED_CHECK_TIME:
                        speed_checked = True
                        with self._lock:
                            recv = self._received
                        speed = recv / elapsed if elapsed > 0 else 0
                        if speed < self.MIN_SPEED and recv < total * 0.5:
                            # Too slow and not even halfway done → try next source
                            self._slow_abort = True
                            try:
                                os.remove(self._dest)
                            except OSError:
                                pass
                            return False, "速度过慢，切换源"
                try:
                    ok, err = fut.result()
                    if not ok:
                        errors.append(err)
                except Exception as e:
                    errors.append(str(e))
                if self._abort or self._slow_abort:
                    return False, "已取消" if self._abort else "速度过慢，切换源"

        if errors and len(errors) == len(segments):
            try:
                os.remove(self._dest)
            except OSError:
                pass
            return False, errors[0]
        if errors:
            pass
        return True, ""

    def _download_segment(self, url, start, end):
        """Download a single byte-range segment and write to the correct offset."""
        import urllib.request
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) YouBoard-Updater",
                "Range": f"bytes={start}-{end}",
            })
            with urllib.request.urlopen(req, timeout=self.TIMEOUT) as resp:
                offset = start
                with open(self._dest, "r+b") as f:
                    f.seek(offset)
                    while True:
                        if self._abort or self._slow_abort:
                            return False, "已取消"
                        buf = resp.read(self.CHUNK)
                        if not buf:
                            break
                        f.write(buf)
                        with self._lock:
                            self._received += len(buf)
                            recv = self._received
                            tot = self._total
                        self.progress.emit(recv, tot)
                return True, ""
        except Exception as e:
            return False, str(e)

    def _simple_download(self, url):
        """Fallback: single-stream download (for servers without Range support)."""
        import urllib.request
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) YouBoard-Updater",
                "Accept": "*/*",
            })
            with urllib.request.urlopen(req, timeout=self.TIMEOUT) as resp:
                total = int(resp.headers.get("Content-Length", 0) or 0)
                received = 0
                with open(self._dest, "wb") as f:
                    while True:
                        if self._abort:
                            return False, "已取消"
                        buf = resp.read(self.CHUNK)
                        if not buf:
                            break
                        f.write(buf)
                        received += len(buf)
                        self.progress.emit(received, total)
                if received > 0 and (total == 0 or received == total):
                    return True, ""
                return False, "下载不完整"
        except Exception as e:
            return False, str(e)


# ===========================================================================
# Global Hotkey (Python thread with own Win32 message loop)
# ===========================================================================
HOTKEY_ID = 0xB0AD


class _MacHotkeyListener(threading.Thread):
    """macOS 全局热键：Quartz CGEventTap 监听。

    需要 PyObjC（python.org 版 Python 自带；打包时随 bundle 携带）。
    首次使用需在「系统设置 → 隐私与安全性 → 辅助功能」中授予权限，
    未授权时静默降级（全局热键不可用，不影响其他功能）。
    """

    _KEYCODE_MAP = {chr(ord("a") + i): i for i in range(26)}
    _KEYCODE_MAP.update({"0": 29, "1": 18, "2": 19, "3": 20, "4": 21,
                         "5": 23, "6": 22, "7": 26, "8": 28, "9": 25})
    _KEYCODE_MAP.update({"f%d" % i: v for i, v in enumerate(
        [122, 120, 99, 118, 96, 97, 98, 100, 101, 109, 103, 111], 1)})

    def __init__(self, hotkey_str, callback):
        super().__init__(daemon=True)
        self._mods = set()
        self._key = None
        for p in hotkey_str.lower().replace(" ", "").split("+"):
            if p in ("ctrl", "control"):
                self._mods.add("ctrl")
            elif p == "alt":
                self._mods.add("alt")
            elif p == "shift":
                self._mods.add("shift")
            elif p in ("win", "super"):
                self._mods.add("win")
            elif len(p) == 1 and (p.isalpha() or p.isdigit()):
                self._key = p.lower()
            elif p.startswith("f") and p[1:].isdigit():
                self._key = p.lower()
        self._keycode = self._KEYCODE_MAP.get(self._key)
        self._callback = callback
        self._tap = None
        self._runloop = None

    def run(self):
        if self._keycode is None:
            return
        try:
            from Quartz import (
                CGEventTapCreate, CGEventTapEnable, CGEventGetFlags,
                CGEventGetIntegerValueField, CGEventMaskBit,
                kCGEventKeyDown, kCGKeyboardEventKeycode,
                kCGHeadInsertEventTap, kCGHIDEventTap,
                kCGEventTapOptionListenOnly, CFMachPortCreateRunLoopSource,
                CFRunLoopGetCurrent, CFRunLoopAddSource, CFRunLoopRun,
                CFRunLoopStop, kCFRunLoopCommonModes,
                kCGEventFlagMaskCommand, kCGEventFlagMaskShift,
                kCGEventFlagMaskAlternate, kCGEventFlagMaskControl,
            )
        except Exception:
            return

        want = self._mods
        keycode = self._keycode

        def _tap_callback(proxy, cg_type, event, refcon):
            try:
                flags = CGEventGetFlags(event)
                kc = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)
                got = set()
                if flags & kCGEventFlagMaskCommand:
                    got.add("win")
                if flags & kCGEventFlagMaskControl:
                    got.add("ctrl")
                if flags & kCGEventFlagMaskAlternate:
                    got.add("alt")
                if flags & kCGEventFlagMaskShift:
                    got.add("shift")
                if kc == keycode and got == want:
                    self._callback()
            except Exception:
                pass
            return event

        tap = CGEventTapCreate(kCGHIDEventTap, kCGHeadInsertEventTap,
                               kCGEventTapOptionListenOnly,
                               CGEventMaskBit(kCGEventKeyDown),
                               _tap_callback, None)
        if not tap:
            return
        self._tap = tap
        CGEventTapEnable(tap, True)
        src = CFMachPortCreateRunLoopSource(None, tap, 0)
        loop = CFRunLoopGetCurrent()
        self._runloop = loop
        CFRunLoopAddSource(loop, src, kCFRunLoopCommonModes)
        CFRunLoopRun()
        try:
            CGEventTapEnable(tap, False)
        except Exception:
            pass

    def stop(self):
        if self._tap is not None:
            try:
                from Quartz import CGEventTapEnable
                CGEventTapEnable(self._tap, False)
            except Exception:
                pass
        if self._runloop is not None:
            try:
                from Quartz import CFRunLoopStop
                CFRunLoopStop(self._runloop)
            except Exception:
                pass


class _HotkeyWorker(threading.Thread):
    """Runs its own Win32 message loop, sets flag on WM_HOTKEY."""

    def __init__(self, mods, vk):
        super().__init__(daemon=True)
        self._mods = mods
        self._vk = vk
        self.pressed = False
        self._running = True

    def run(self):
        user32 = ctypes.windll.user32
        # Try to register; if fails, unregister stale entry and retry
        if not user32.RegisterHotKey(None, HOTKEY_ID, self._mods, self._vk):
            user32.UnregisterHotKey(None, HOTKEY_ID)
            import time
            time.sleep(0.1)
            user32.RegisterHotKey(None, HOTKEY_ID, self._mods, self._vk)
        msg = ctypes.wintypes.MSG()
        while self._running:
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret <= 0:
                break
            if msg.message == 0x0312 and msg.wParam == HOTKEY_ID:
                self.pressed = True
        user32.UnregisterHotKey(None, HOTKEY_ID)

    def stop(self):
        self._running = False
        try:
            if self.ident:
                ctypes.windll.user32.PostThreadMessageW(self.ident, 0x0012, 0, 0)
        except Exception:
            pass


# ===========================================================================
# Edge Resize Handles (thin strips along window edges)
# ===========================================================================
class _EdgeHandle(QWidget):
    """Transparent strip along a window edge for resize with cursor feedback."""
    THICKNESS = 6

    def __init__(self, parent_window, edge):
        """edge: 'left', 'right', 'top', 'bottom'"""
        super().__init__(parent_window)
        self._win = parent_window
        self._edge = edge
        self._dragging = False
        self._start_pos = None
        self._start_geo = None
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet("background: transparent;")
        if edge in ('left', 'right'):
            self.setCursor(QCursor(Qt.CursorShape.SizeHorCursor))
            self.setFixedWidth(self.THICKNESS)
        else:
            self.setCursor(QCursor(Qt.CursorShape.SizeVerCursor))
            self.setFixedHeight(self.THICKNESS)
        self.raise_()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pressed_at = event.globalPosition().toPoint()
            if self._win.isMaximized():
                # 对齐原生 Win11 手感：最大化时拖边缘先还原再缩放
                # 延迟到窗口状态切换完成后再启动原生缩放，避免 Qt fail-fast 崩溃
                self._win.showNormal()
                QTimer.singleShot(0, lambda: self._start_resize(pressed_at))
            else:
                self._start_resize(pressed_at)
            event.accept()

    def _start_resize(self, global_pos):
        wh = self._win.windowHandle()
        edge_map = {'left': Qt.Edge.LeftEdge, 'right': Qt.Edge.RightEdge,
                    'top': Qt.Edge.TopEdge, 'bottom': Qt.Edge.BottomEdge}
        if wh is not None and wh.startSystemResize(edge_map[self._edge]):
            return
        self._dragging = True
        self._start_pos = global_pos
        self._start_geo = self._win.geometry()
        self.grabMouse()

    def mouseMoveEvent(self, event):
        if not self._dragging or not self._start_pos:
            return
        delta = event.globalPosition().toPoint() - self._start_pos
        geo = self._start_geo
        x, y, w, h = geo.x(), geo.y(), geo.width(), geo.height()
        min_w, min_h = self._win.minimumWidth(), self._win.minimumHeight()
        if self._edge == 'left':
            new_w = w - delta.x()
            if new_w >= min_w:
                self._win.setGeometry(geo.x() + delta.x(), y, new_w, h)
        elif self._edge == 'right':
            self._win.resize(max(min_w, w + delta.x()), h)
        elif self._edge == 'top':
            new_h = h - delta.y()
            if new_h >= min_h:
                self._win.setGeometry(x, geo.y() + delta.y(), w, new_h)
        elif self._edge == 'bottom':
            self._win.resize(w, max(min_h, h + delta.y()))
        event.accept()

    def mouseReleaseEvent(self, event):
        if self._dragging:
            self.releaseMouse()
        self._dragging = False
        self._start_pos = None


# ===========================================================================
# Resize Grip (bottom-right corner visual indicator)
# ===========================================================================
class _ResizeGrip(QWidget):
    """Small grip icon in the bottom-right corner indicating resizable edges."""
    SIZE = 18

    def __init__(self, parent_window):
        super().__init__(parent_window)
        self._win = parent_window
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setCursor(QCursor(Qt.CursorShape.SizeFDiagCursor))
        self._dragging = False
        self._start_pos = None
        self._start_geo = None

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Draw 3 diagonal dots (classic resize grip pattern)
        color = QColor(160, 165, 180, 140)
        s = self.SIZE
        for i in range(3):
            offset = 4 + i * 5
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            p.drawEllipse(s - offset - 1, s - offset - 1, 3, 3)
        p.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pressed_at = event.globalPosition().toPoint()
            if self._win.isMaximized():
                self._win.showNormal()
                QTimer.singleShot(0, lambda: self._start_resize(pressed_at))
            else:
                self._start_resize(pressed_at)
            event.accept()

    def _start_resize(self, global_pos):
        wh = self._win.windowHandle()
        if wh is not None and wh.startSystemResize(
                Qt.Edge.BottomEdge | Qt.Edge.RightEdge):
            return
        self._dragging = True
        self._start_pos = global_pos
        self._start_geo = self._win.geometry()
        self.grabMouse()

    def mouseMoveEvent(self, event):
        if self._dragging and self._start_pos:
            delta = event.globalPosition().toPoint() - self._start_pos
            new_w = max(self._win.minimumWidth(), self._start_geo.width() + delta.x())
            new_h = max(self._win.minimumHeight(), self._start_geo.height() + delta.y())
            self._win.resize(new_w, new_h)
            event.accept()

    def mouseReleaseEvent(self, event):
        if self._dragging:
            self.releaseMouse()
        self._dragging = False
        self._start_pos = None


# ===========================================================================
# Corner Resize Handle (transparent, diagonal cursor, native resize)
# ===========================================================================
class _CornerHandle(QWidget):
    """透明角缩放热区：对角光标 + 原生 startSystemResize 双向缩放。"""
    SIZE = 14

    def __init__(self, parent_window, corner):
        """corner: 'tl' / 'tr' / 'bl'"""
        super().__init__(parent_window)
        self._win = parent_window
        self._corner = corner
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet("background: transparent;")
        # 与小组件约定一致：tl/br=SizeFDiag，tr/bl=SizeBDiag
        if corner in ("tl", "br"):
            self.setCursor(QCursor(Qt.CursorShape.SizeFDiagCursor))
        else:
            self.setCursor(QCursor(Qt.CursorShape.SizeBDiagCursor))
        self.raise_()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pressed_at = event.globalPosition().toPoint()
            if self._win.isMaximized():
                self._win.showNormal()
                QTimer.singleShot(0, lambda: self._start_resize(pressed_at))
            else:
                self._start_resize(pressed_at)
            event.accept()

    def _start_resize(self, global_pos):
        wh = self._win.windowHandle()
        edges = {
            "tl": Qt.Edge.TopEdge | Qt.Edge.LeftEdge,
            "tr": Qt.Edge.TopEdge | Qt.Edge.RightEdge,
            "bl": Qt.Edge.BottomEdge | Qt.Edge.LeftEdge,
            "br": Qt.Edge.BottomEdge | Qt.Edge.RightEdge,
        }
        if wh is not None and wh.startSystemResize(edges[self._corner]):
            return


# ===========================================================================
# Custom Title Bar (frameless window)
# ===========================================================================
class _TitleBar(QWidget):
    """Custom title bar with animated gradient and window control buttons."""
    HEIGHT = 32

    def __init__(self, parent_window):
        super().__init__()
        self._win = parent_window
        self._drag_pos = None
        self.setFixedHeight(self.HEIGHT)
        self._build()
        # Shimmer animation timer
        self._shimmer_offset = 0.0
        self._shimmer_timer = QTimer(self)
        self._shimmer_timer.timeout.connect(self._tick_shimmer)
        self._shimmer_timer.start(50)

    def _build(self):
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 0, 0)
        lay.setSpacing(3)
        self._light = _is_light_theme()
        hover_bg = ("background: rgba(0,0,0,22);" if self._light
                    else "background: rgba(255,255,255,25);")

        def _btn_icon(ico, png):
            if self._light:
                pp = _res_icon(png + ".png")
                if os.path.exists(pp):
                    return _tint_icon(pp, QColor(C['TEXT_SEC']))
            return QIcon(ico)

        self._icon_min = _btn_icon(ICO_MIN, "zuixiao")
        self._icon_max = _btn_icon(ICO_MAX, "zuida")
        self._icon_rest = _btn_icon(ICO_RESTORE, "zuidahuifu")
        self._icon_close = _btn_icon(ICO_CLOSE, "guanbi")
        # Icon
        if LOGO_ICO and os.path.exists(LOGO_ICO):
            ico_lbl = QLabel()
            ico_lbl.setFixedSize(18, 18)
            ico_lbl.setPixmap(QIcon(LOGO_ICO).pixmap(QSize(18, 18)))
            ico_lbl.setStyleSheet("background: transparent;")
            lay.addWidget(ico_lbl)
        # Title
        self._title_lbl = QLabel(APP_NAME)
        self._title_lbl.setStyleSheet(f"color: {C['TEXT_SEC']}; font-size: 12px; background: transparent;")
        lay.addWidget(self._title_lbl)
        lay.addStretch()
        # Window buttons (icon-based)
        # 直角 + 铺满标题栏高度：关闭按钮贴满窗口右上角，消除圆角露出的缝隙。
        btn_style_base = (
            "border: none; border-radius: 0; padding: 0; margin: 0; "
            "min-width: 32px; max-width: 32px; min-height: 32px; max-height: 32px; "
            "background: transparent;")
        ico_size = QSize(14, 14)

        self._min_btn = QPushButton()
        self._min_btn.setIcon(self._icon_min)
        self._min_btn.setIconSize(ico_size)
        self._min_btn.setStyleSheet(btn_style_base)
        self._min_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._min_btn.clicked.connect(self._win.showMinimized)
        self._min_btn.enterEvent = lambda e: (self._min_btn.setStyleSheet(
            btn_style_base + hover_bg), self.update())[1]
        self._min_btn.leaveEvent = lambda e: (self._min_btn.setStyleSheet(
            btn_style_base + "background: transparent;"), self.update())[1]
        lay.addWidget(self._min_btn)

        self._max_btn = QPushButton()
        self._max_btn.setIcon(self._icon_max)
        self._max_btn.setIconSize(ico_size)
        self._max_btn.setStyleSheet(btn_style_base)
        self._max_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._max_btn.clicked.connect(self._toggle_max)
        self._max_btn.enterEvent = lambda e: (self._max_btn.setStyleSheet(
            btn_style_base + hover_bg), self.update())[1]
        self._max_btn.leaveEvent = lambda e: (self._max_btn.setStyleSheet(
            btn_style_base + "background: transparent;"), self.update())[1]
        lay.addWidget(self._max_btn)

        self._close_btn = QPushButton()
        self._close_btn.setIcon(self._icon_close)
        self._close_btn.setIconSize(ico_size)
        self._close_btn.setStyleSheet(btn_style_base)
        self._close_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._close_btn.clicked.connect(self._win.close)
        self._close_btn.enterEvent = lambda e: (self._close_btn.setStyleSheet(
            btn_style_base + "background: #e04343;"), self.update())[1]
        self._close_btn.leaveEvent = lambda e: (self._close_btn.setStyleSheet(
            btn_style_base + "background: transparent;"), self.update())[1]
        lay.addWidget(self._close_btn)

    def _toggle_max(self):
        if self._win.isMaximized():
            self._win.showNormal()
            self._max_btn.setIcon(self._icon_max)
        else:
            self._win.showMaximized()
            self._max_btn.setIcon(self._icon_rest)
        self._max_btn.repaint()
        self.update()

    def update_max_btn(self):
        self._max_btn.setIcon(self._icon_rest if self._win.isMaximized() else self._icon_max)
        self._max_btn.repaint()
        self.update()

    # --- Drag to move ---
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            # Button area: just return, don't accept (let button receive the event)
            if (self._min_btn.geometry().contains(pos) or
                self._max_btn.geometry().contains(pos) or
                self._close_btn.geometry().contains(pos)):
                event.ignore()
                return
            wh = self._win.windowHandle()
            if wh is not None and wh.startSystemMove():
                event.accept()
                return
            self._drag_pos = event.globalPosition().toPoint() - self._win.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_pos and event.buttons() & Qt.MouseButton.LeftButton:
            if self._win.isMaximized():
                # Un-maximize on drag
                self._win.showNormal()
                self._max_btn.setIcon(self._icon_max)
                self._drag_pos = QPoint(int(self._win.width() / 2), int(self.HEIGHT / 2))
            self._win.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None

    def mouseDoubleClickEvent(self, event):
        self._toggle_max()

    # --- Animated shimmer ---
    def pause_shimmer(self):
        self._shimmer_timer.stop()

    def resume_shimmer(self):
        if not self._shimmer_timer.isActive():
            self._shimmer_timer.start(50)

    def _tick_shimmer(self):
        self._shimmer_offset += 0.02
        if self._shimmer_offset > 1.0:
            self._shimmer_offset -= 1.0
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        light = _is_light_theme()
        # Vertical gradient background (theme aware)
        vgrad = QLinearGradient(0, 0, 0, h)
        if light:
            vgrad.setColorAt(0.0, QColor(250, 251, 254, 255))
            vgrad.setColorAt(1.0, QColor(236, 239, 245, 255))
        else:
            vgrad.setColorAt(0.0, QColor(28, 30, 38, 255))
            vgrad.setColorAt(1.0, QColor(16, 17, 22, 255))
        p.fillRect(self.rect(), vgrad)
        # Moving shimmer with subtle blue-purple tint
        x = int(self._shimmer_offset * w * 2 - w * 0.4)
        sgrad = QLinearGradient(x, 0, x + w // 3, 0)
        if light:
            sgrad.setColorAt(0.0, QColor(80, 110, 255, 0))
            sgrad.setColorAt(0.5, QColor(90, 120, 255, 26))
            sgrad.setColorAt(1.0, QColor(140, 110, 255, 0))
        else:
            sgrad.setColorAt(0.0, QColor(120, 140, 255, 0))
            sgrad.setColorAt(0.5, QColor(120, 140, 255, 14))
            sgrad.setColorAt(1.0, QColor(180, 120, 255, 0))
        p.fillRect(self.rect(), sgrad)
        # Bottom accent line with shifting hue
        hue = (self._shimmer_offset * 360) % 360
        import colorsys
        r, g, b = colorsys.hsv_to_rgb(hue / 360.0, 0.5, 0.7)
        accent = QColor(int(r * 255), int(g * 255), int(b * 255), 60)
        p.setPen(QPen(accent, 1))
        p.drawLine(0, h - 1, w, h - 1)
        p.end()


# ===========================================================================
# DesktopClipboardWidget — 桌面小组件：实时显示当前剪贴板内容 + 最近历史
# ===========================================================================
class DesktopClipboardWidget(QWidget):
    """小型置顶桌面窗口：显示最新一条剪贴板内容 + 最近 20 条历史记录。

    特性：无边框、总在最前、不在任务栏显示；可拖拽移动（位置记忆）；
    拖动右下角可缩放大小（尺寸记忆）；点击历史条目复制回剪贴板；
    右上角 ✕ 关闭并在设置中停用本组件。
    """

    HISTORY_ROWS = 20
    # 最小尺寸 = 完整内容尺寸（标题 + 当前行 + 历史标题 + 至少一条历史），
    # 避免启动后或拖动时缩成 90x60 的小药丸，保证“退出前多大，重启后还多大”。
    MIN_W, MIN_H = 90, 60
    MAX_W, MAX_H = 1200, 1080
    _GRIP = 18        # 右下角缩放热区边长
    _EDGE = 12        # 底边上下缩放缓区高度
    IDLE_OPACITY = 0.45   # 常驻透明度
    HOVER_OPACITY = 1.0   # 鼠标悬停时完全清晰

    def __init__(self, app):
        super().__init__(None)
        self.app = app
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool
                            | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.setWindowOpacity(self.IDLE_OPACITY)
        self._drag_pos = None
        self._resize_mode = None
        self._resize_origin = None
        self._resize_geo = None
        self._press_pos = None
        self._press_zone = None
        self._press_active = False
        self._entries = []
        # 布局派生的 minimumSize 会把窗口锁在 ~87px 高，覆盖掉，
        # 下限由 MIN_W/MIN_H 与分级收敛逻辑共同管理
        self.setMinimumSize(self.MIN_W, self.MIN_H)
        self._icons = {}
        for etype in ("text", "image", "file", "url"):
            p = _res_icon(TAB_ICON_FILES[etype])
            if os.path.exists(p):
                self._icons[etype] = QIcon(p)
        self._build()
        self._apply_theme()
        self._cache_geo_consts()
        self._restore_geometry()
        # 几何自动保存：位置/尺寸一变就落盘（5 秒轮询），
        # 强退/崩溃也不会丢摆放，重启后与退出前一致
        self._last_saved_geo = (self.x(), self.y(),
                                self.width(), self.height())
        self._geo_timer = QTimer(self)
        self._geo_timer.timeout.connect(self._autosave_geometry)
        self._geo_timer.start(5000)

    # ---- UI ----
    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._card = QFrame()
        self._card.setObjectName("dwCard")
        lay = QVBoxLayout(self._card)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(5)

        head = QHBoxLayout()
        self._title_lbl = QLabel(tr("widget_title"))
        head.addWidget(self._title_lbl)
        head.addStretch()
        close_btn = QPushButton("\u2715")
        close_btn.setObjectName("dwClose")
        close_btn.setFixedSize(20, 20)
        close_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        close_btn.clicked.connect(self._close_to_config)
        head.addWidget(close_btn)
        self._close_btn = close_btn
        self._head_lay = head
        lay.addLayout(head)

        # 当前剪贴板内容（图标 + 文本）
        cur_row = QHBoxLayout()
        cur_row.setSpacing(6)
        self._cur_icon = QLabel()
        self._cur_icon.setFixedSize(20, 20)
        cur_row.addWidget(self._cur_icon)
        self._cur_lbl = QLabel(tr("widget_empty"))
        self._cur_lbl.setObjectName("dwCur")
        self._cur_lbl.setWordWrap(False)
        # 内容适应组件尺寸，而非组件跟随内容变大
        self._cur_lbl.setSizePolicy(QSizePolicy.Policy.Ignored,
                                    QSizePolicy.Policy.Maximum)
        self._cur_lbl.setMinimumHeight(0)
        self._cur_full = ""
        cur_row.addWidget(self._cur_lbl, 1)
        self._cur_lay = cur_row
        lay.addLayout(cur_row)

        self._hist_title = QLabel(tr("widget_history"))
        self._hist_title.setObjectName("dwHistTitle")
        self._hist_title.setSizePolicy(QSizePolicy.Policy.Preferred,
                                       QSizePolicy.Policy.Maximum)
        lay.addWidget(self._hist_title)

        self._hist_list = QListWidget()
        self._hist_list.setObjectName("dwList")
        self._hist_list.setIconSize(QSize(16, 16))
        self._hist_list.setSizePolicy(QSizePolicy.Policy.Expanding,
                                      QSizePolicy.Policy.Ignored)
        self._hist_list.setMinimumHeight(0)
        self._hist_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._hist_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._hist_list.itemClicked.connect(self._on_item_clicked)
        lay.addWidget(self._hist_list, 1)

        outer.addWidget(self._card)

        # 子控件默认不接收无按键鼠标移动，事件无法冒泡到父窗口，
        # 边缘缩放光标就不会触发；统一开启 mouseTracking。
        self._card.setMouseTracking(True)
        for _w in (self._title_lbl, self._cur_icon, self._cur_lbl, self._hist_title):
            _w.setMouseTracking(True)

    def showEvent(self, event):
        super().showEvent(event)
        # 直接设为常驻透明度：透明度变化即触发 DWM 合成，且无亮度跳变。
        self.setWindowOpacity(self.IDLE_OPACITY)
        QTimer.singleShot(0, self._sync_hist_visibility)
        # 兜底：延迟用原生 SetWindowPos 尺寸 nudge 强制 DWM 合成
        QTimer.singleShot(800, self._kick_render)

    def _fade_to_idle(self):
        if self.isVisible():
            self.setWindowOpacity(self.IDLE_OPACITY)

    def _kick_render(self):
        if not IS_WIN or not self.isVisible():
            return
        try:
            import ctypes
            g = self.geometry()
            self._kick_target = (g.x(), g.y(), g.width(), g.height())
            # SetWindowPos 使用物理像素；Qt 几何为逻辑像素，需按 DPR 换算，
            # 否则 DPI 感知进程下会把窗口“缩小+往左上跳”并被自动保存进配置。
            dpr = self.devicePixelRatioF() or 1.0
            ctypes.windll.user32.SetWindowPos(int(self.winId()), 0,
                                              int(g.x() * dpr) + 1, int(g.y() * dpr),
                                              int(g.width() * dpr) + 1, int(g.height() * dpr) + 1,
                                              0x0004)
            QTimer.singleShot(60, self._kick_restore)
        except Exception:
            pass

    def _kick_restore(self):
        if not IS_WIN or not self.isVisible():
            return
        try:
            import ctypes
            x, y, w, h = getattr(self, "_kick_target", (0, 0, 0, 0))
            if w and h:
                dpr = self.devicePixelRatioF() or 1.0
                ctypes.windll.user32.SetWindowPos(int(self.winId()), 0,
                                                  int(x * dpr), int(y * dpr),
                                                  int(w * dpr), int(h * dpr), 0x0004)
        except Exception:
            pass

    def _apply_theme(self):
        theme = load_config().get("theme", "dark")
        if theme == "light":
            card_bg, txt, sec, border = "rgba(255,255,255,250)", "#1f2329", "#6b7280", "rgba(0,0,0,40)"
            hover = "rgba(0,0,0,14)"
        else:
            card_bg, txt, sec, border = "rgba(24,26,33,250)", "#e8eaed", "#9aa0a6", "rgba(255,255,255,34)"
            hover = "rgba(255,255,255,22)"
        self.setStyleSheet(f"""
            #dwCard {{ background: {card_bg}; border: 1px solid {border}; border-radius: 10px; }}
            QLabel {{ background: transparent; color: {txt};
                      font-family: "Microsoft YaHei UI","Segoe UI",sans-serif; }}
            #dwCur {{ font-size: 12px; }}
            #dwHistTitle {{ color: {sec}; font-size: 10px; }}
            #dwClose {{ background: transparent; border: none; color: {sec}; font-size: 11px; }}
            #dwClose:hover {{ color: {txt}; }}
            #dwList {{ background: transparent; border: none; color: {txt}; font-size: 11px;
                       outline: 0; }}
            #dwList::item {{ padding: 3px 4px; border-radius: 5px; }}
            #dwList::item:hover {{ background: {hover}; }}
            #dwList::item:selected {{ background: {hover}; color: {txt}; }}
            #dwList QScrollBar:vertical {{ background: transparent; width: 8px; }}
            #dwList QScrollBar::handle:vertical {{ background: {border}; border-radius: 4px; min-height: 26px; }}
            #dwList QScrollBar::add-line:vertical, #dwList QScrollBar::sub-line:vertical {{ height: 0; }}
        """)

    # ---- data ----
    def refresh(self, override=None):
        """重载最新剪贴板内容 + 最近历史。

        override：应用内复制（Enter / 点击组件条目）时直接指定当前行
        显示的条目——这类复制不会新增记录，组件需跟随显示。
        """
        try:
            all_entries = self.app.store.get_all()
            all_entries.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
        except Exception:
            all_entries = []
        self._entries = all_entries[:1 + self.HISTORY_ROWS]
        if override is not None:
            cur = override
            rest = [e for e in self._entries
                    if e.get("hash") != override.get("hash")][:self.HISTORY_ROWS]
        else:
            cur = self._entries[0] if self._entries else None
            rest = self._entries[1:]
        if cur is None:
            self._cur_icon.clear()
            self._cur_full = ""
            self._cur_lbl.setText(tr("widget_empty"))
            self._hist_list.clear()
            self._hist_entries = []
            return
        ico = self._icons.get(cur.get("type", "text"))
        self._cur_icon.setPixmap(ico.pixmap(QSize(20, 20)) if ico else QPixmap())
        self._cur_full = self._fmt(cur, 160)
        self._elide_cur()
        self._hist_entries = rest
        self._hist_list.clear()
        for e in rest:
            item = QListWidgetItem(self._fmt(e, 60))
            ico2 = self._icons.get(e.get("type", "text"))
            if ico2:
                item.setIcon(ico2)
            item.setToolTip(tr("widget_click_copy"))
            self._hist_list.addItem(item)
        QTimer.singleShot(0, self._sync_hist_visibility)

    def _elide_cur(self):
        """当前行文本按组件宽度省略，内容跟随组件尺寸显示。"""
        txt = getattr(self, "_cur_full", "") or ""
        if not txt:
            return
        w = self._cur_lbl.width()
        if w <= 0:
            w = max(40, self.width() - 70)
        fm = self._cur_lbl.fontMetrics()
        self._cur_lbl.setText(
            fm.elidedText(txt, Qt.TextElideMode.ElideRight, w))

    def _cache_geo_consts(self):
        """缓存正常形态下的布局度量，作为分级收敛的稳定判据。"""
        try:
            cl = self._card.layout()
            m = cl.contentsMargins()
            self._g_base = m.top() + m.bottom()
            self._g_sp = cl.spacing()
            self._g_head = self._head_lay.sizeHint().height()
            self._g_cur = self._cur_lay.sizeHint().height()
            self._g_title = self._hist_title.sizeHint().height()
        except Exception:
            pass

    def _sync_hist_visibility(self):
        """按高度分级收敛，保证任何尺寸都不截断、退出前后形态一致：
        历史不足一行→收起列表；历史标题放不下→隐藏标题；
        连当前行都放不下→微缩模式（窄边距+16px 图标）。"""
        try:
            if not hasattr(self, "_g_base"):
                self._cache_geo_consts()
            sp = self._g_sp
            hb, cb, tb = self._g_head, self._g_cur, self._g_title
            # 用窗口高度：resizeEvent 期间子卡片几何可能尚未更新
            H = self.height()
            micro = H < (self._g_base + sp + hb + cb)
            if micro != getattr(self, "_micro_mode", False):
                self._micro_mode = micro
                cl = self._card.layout()
                if micro:
                    cl.setContentsMargins(8, 5, 8, 5)
                    cl.setSpacing(3)
                    self._close_btn.setFixedSize(16, 16)
                    self._cur_icon.setFixedSize(16, 16)
                else:
                    cl.setContentsMargins(12, 10, 12, 10)
                    cl.setSpacing(5)
                    self._close_btn.setFixedSize(20, 20)
                    self._cur_icon.setFixedSize(20, 20)
            show_title = H >= (self._g_base + sp * 2 + hb + cb + tb - 2)
            self._hist_title.setVisible(show_title)
            if show_title:
                avail = H - (self._g_base + sp * 3 + hb + cb + tb)
            else:
                avail = H - (self._g_base + sp * 2 + hb + cb)
            self._hist_list.setMaximumHeight(0 if avail < 24 else 16777215)
            self._card.layout().activate()
        except Exception:
            pass

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide_cur()
        self._sync_hist_visibility()

    def _fmt(self, entry, limit):
        etype = entry.get("type", "text")
        if etype == "image":
            text = f"{entry.get('width', '?')}\u00d7{entry.get('height', '?')} "
            src = entry.get("source_name", "")
            text += src if src else tr("type_image")
        elif etype == "file":
            paths = entry.get("file_paths", []) or []
            if isinstance(paths, str):
                paths = [paths]
            name = os.path.basename(paths[0]) if paths else "?"
            n = entry.get("file_count", len(paths))
            text = (name + f" (+{n - 1})") if n > 1 else name
        else:
            text = str(entry.get("content", ""))
        text = text.replace("\n", " ").replace("\r", " ").strip()
        if len(text) > limit:
            text = text[:limit] + "\u2026"
        return text

    # ---- interactions ----
    def _on_item_clicked(self, item):
        row = self._hist_list.row(item)
        entries = getattr(self, "_hist_entries", self._entries[1:])
        if row < 0 or row >= len(entries):
            return
        try:
            self.app.copy_entry_to_clipboard(entries[row])
            self.refresh(entries[row])
            try:
                self.app._set_status(tr("st_copied"), "ok")
            except Exception:
                pass
        except Exception:
            pass

    def _close_to_config(self):
        cfg = load_config()
        cfg["desktop_widget"] = False
        save_config(cfg)
        self.hide()

    # ---- drag / resize / persist geometry ----
    def _hit_zone(self, pos):
        """corner=右下；corner_l=左下；corner_tr=右上；corner_tl=左上（均双向缩放）；
        hedge_r/hedge_l=右/左水平；vedge/tedge=底/顶垂直；None=拖动。"""
        x, y = pos.x(), pos.y()
        if x >= self.width() - self._GRIP and y >= self.height() - self._GRIP:
            return "corner"
        if x <= self._GRIP and y >= self.height() - self._GRIP:
            return "corner_l"
        if x >= self.width() - self._GRIP and y <= self._GRIP:
            return "corner_tr"
        if x <= self._GRIP and y <= self._GRIP:
            return "corner_tl"
        if x >= self.width() - self._EDGE:
            return "hedge_r"
        if x <= self._EDGE:
            return "hedge_l"
        if y >= self.height() - self._EDGE:
            return "vedge"
        if y <= self._EDGE:
            return "tedge"
        return None

    def enterEvent(self, event):
        self.setWindowOpacity(self.HOVER_OPACITY)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.setWindowOpacity(self.IDLE_OPACITY)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            # 按下不立即进入拖拽/缩放：普通点击的手抖（几像素）曾把
            # 组件意外拖动/缩小并写进配置，导致重启后跳位置、变小。
            self._press_zone = self._hit_zone(event.position().toPoint())
            self._press_pos = event.globalPosition().toPoint()
            self._press_active = False
            event.accept()

    def _activate_press(self):
        self._press_active = True
        if self._press_zone:
            self._resize_mode = self._press_zone
            self._resize_origin = self._press_pos
            self._resize_geo = self.geometry()
        else:
            self._drag_pos = self._press_pos - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self._press_pos is not None and not self._press_active:
            if ((event.globalPosition().toPoint() - self._press_pos)
                    .manhattanLength() < 4):
                return  # 视为纯点击：不拖拽、不缩放
            self._activate_press()
        if self._resize_mode and self._resize_origin is not None:
            delta = event.globalPosition().toPoint() - self._resize_origin
            geo = self._resize_geo
            gx, gy, gw, gh = geo.x(), geo.y(), geo.width(), geo.height()
            if self._resize_mode == "corner":
                w = max(self.MIN_W, min(self.MAX_W, gw + delta.x()))
                h = max(self.MIN_H, min(self.MAX_H, gh + delta.y()))
                self.resize(int(w), int(h))
            elif self._resize_mode == "corner_l":
                nw = max(self.MIN_W, min(self.MAX_W, gw - delta.x()))
                h = max(self.MIN_H, min(self.MAX_H, gh + delta.y()))
                self.setGeometry(gx + gw - nw, gy, int(nw), int(h))
            elif self._resize_mode == "corner_tr":
                w = max(self.MIN_W, min(self.MAX_W, gw + delta.x()))
                nh = max(self.MIN_H, min(self.MAX_H, gh - delta.y()))
                self.setGeometry(gx, gy + gh - nh, int(w), int(nh))
            elif self._resize_mode == "corner_tl":
                nw = max(self.MIN_W, min(self.MAX_W, gw - delta.x()))
                nh = max(self.MIN_H, min(self.MAX_H, gh - delta.y()))
                self.setGeometry(gx + gw - nw, gy + gh - nh, int(nw), int(nh))
            elif self._resize_mode == "hedge_r":
                w = max(self.MIN_W, min(self.MAX_W, gw + delta.x()))
                self.resize(int(w), gh)
            elif self._resize_mode == "hedge_l":
                nw = max(self.MIN_W, min(self.MAX_W, gw - delta.x()))
                self.setGeometry(gx + gw - nw, gy, int(nw), gh)
            elif self._resize_mode == "tedge":
                nh = max(self.MIN_H, min(self.MAX_H, gh - delta.y()))
                self.setGeometry(gx, gy + gh - nh, gw, int(nh))
            else:  # vedge：上下缩放只改高度
                h = max(self.MIN_H, min(self.MAX_H, gh + delta.y()))
                self.resize(gw, int(h))
            event.accept()
            return
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
            return
        zone = self._hit_zone(event.position().toPoint())
        if zone in ("corner", "corner_tl"):
            self.setCursor(QCursor(Qt.CursorShape.SizeFDiagCursor))
        elif zone in ("corner_l", "corner_tr"):
            self.setCursor(QCursor(Qt.CursorShape.SizeBDiagCursor))
        elif zone in ("hedge_l", "hedge_r"):
            self.setCursor(QCursor(Qt.CursorShape.SizeHorCursor))
        elif zone in ("vedge", "tedge"):
            self.setCursor(QCursor(Qt.CursorShape.SizeVerCursor))
        else:
            self.unsetCursor()

    def mouseReleaseEvent(self, event):
        changed = bool(self._press_active)
        self._resize_mode = None
        self._resize_origin = None
        self._resize_geo = None
        self._drag_pos = None
        self._press_pos = None
        self._press_zone = None
        self._press_active = False
        self.unsetCursor()
        if changed:
            self.save_geometry()
        event.accept()

    def _restore_geometry(self):
        """原样恢复保存的几何（同版本内存什么就恢复什么，数学上保证一致）。"""
        cfg = load_config()
        screen = QApplication.primaryScreen().availableGeometry()
        size = cfg.get("widget_size")
        if isinstance(size, (list, tuple)) and len(size) == 2:
            w = max(self.MIN_W, min(self.MAX_W, int(size[0])))
            h = max(self.MIN_H, min(self.MAX_H, int(size[1])))
        else:
            w, h = self.MIN_W, self.MIN_H
        self.resize(w, h)
        pos = cfg.get("widget_pos")
        if (isinstance(pos, (list, tuple)) and len(pos) == 2
                and screen.left() - 60 <= pos[0] <= screen.right()
                and screen.top() - 60 <= pos[1] <= screen.bottom()):
            self.move(int(pos[0]), int(pos[1]))
        else:
            self.move(screen.right() - w - 24, screen.top() + 90)

    def save_geometry(self):
        """保存当前位置与尺寸，退出前调用以保证重启后一模一样。"""
        try:
            cfg = load_config()
            cfg["widget_pos"] = [self.x(), self.y()]
            cfg["widget_size"] = [self.width(), self.height()]
            save_config(cfg)
            self._last_saved_geo = (self.x(), self.y(),
                                    self.width(), self.height())
        except Exception:
            pass

    def _autosave_geometry(self):
        if not self.isVisible():
            return
        g = (self.x(), self.y(), self.width(), self.height())
        if g != getattr(self, "_last_saved_geo", None):
            self.save_geometry()


class _PopupPanel(QFrame):
    """不透明圆角弹层面板：自绘 SURFACE2 填充 + 细边框 + 圆角。
    在 WA_TranslucentBackground 下 QSS 的 background 会被忽略（导致弹层透明/出现梯形），
    因此用 paintEvent 手动绘制，保证面板真正不透明、圆角、无梯形。"""
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(r, 6.0, 6.0)
        p.fillPath(path, QColor(C['SURFACE2']))
        p.setPen(QPen(QColor(C['BORDER']), 1.0))
        p.drawPath(path)


class _SortMenuButton(QPushButton):
    """排序下拉：不透明圆角弹层（自绘面板，非透明、无梯形），样式与主体一致。"""
    currentIndexChanged = pyqtSignal(int)

    def __init__(self):
        super().__init__()
        self._labels = []
        self._current = 0
        self._sortpop = None
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedWidth(150)
        self.setMinimumHeight(30)
        self.setStyleSheet(f"""
            QPushButton {{ background: {C['SURFACE2']}; color: {C['TEXT_SEC']};
                border: 1px solid {C['BORDER']}; border-radius: 6px; padding: 6px 10px;
                font-size: 12px; text-align: left; }}
            QPushButton:hover {{ border-color: {C['BORDER_LT']}; color: {C['TEXT']}; }}
        """)
        self.clicked.connect(self._popup)

    def addItems(self, labels):
        self._labels = list(labels)
        self._current = 0
        self.setText(self._labels[0] if self._labels else "")
        self.setToolTip(self._labels[0] if self._labels else "")

    def setCurrentIndex(self, idx):
        if 0 <= idx < len(self._labels):
            self._current = idx
            self.setText(self._labels[idx])

    def currentIndex(self):
        return self._current

    def _popup(self):
        if not self._labels:
            return
        # 不透明圆角弹层（自绘面板），宽度与触发按钮一致，非透明、无梯形。
        if self._sortpop is not None:
            try:
                self._sortpop.close()
            except Exception:
                pass
            self._sortpop = None
        pop = _PopupPanel(self, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        pop.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        pop.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C['TEXT']};
                border: none; border-radius: 4px; text-align: left; padding: 7px 10px;
                font-size: 12px; }}
            QPushButton:hover {{ background: {C['SURFACE3']}; color: {C['TEXT']}; }}
            QFrame#sortSep {{ background: {C['BORDER']}; border: none; }}
        """)
        pop.setFixedWidth(self.width())
        lay = QVBoxLayout(pop)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(0)
        self._sortpop = pop
        for i, lab in enumerate(self._labels):
            btn = QPushButton(lab, pop)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, idx=i: self._on_pick(idx))
            lay.addWidget(btn)
            if i != len(self._labels) - 1:
                sep = QFrame(pop)
                sep.setObjectName("sortSep")
                sep.setFixedHeight(1)
                lay.addWidget(sep)
        pop.adjustSize()
        pop.move(self.mapToGlobal(QPoint(0, self.height())))
        pop.show()

    def _on_pick(self, idx):
        if self._sortpop is not None:
            try:
                self._sortpop.close()
            except Exception:
                pass
            self._sortpop = None
        if 0 <= idx < len(self._labels):
            if idx != self._current:
                self._current = idx
                self.setText(self._labels[idx])
                self.currentIndexChanged.emit(idx)


class YouBoardApp(QMainWindow):

    def __init__(self, store, monitor=None):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.store = store
        self.monitor = monitor
        self._active_type = "text"
        self._tables = {}
        self._tab_layouts = []
        self._iid_to_hash = {"text": {}, "image": {}, "file": {}, "url": {}}
        self._search_edits = {}
        self._search_timers = {}
        self._count_labels = {}
        self._sort_orders = {"text": "default", "image": "default", "file": "default", "url": "default"}
        self._sort_combos = {}
        self._entry_index = {}
        self._pinned_hashes = set()
        self._preview_gen = 0
        self._cur_image_path = None
        self._cur_image_entry = None
        self._cur_text_entry = None
        self._cached_pil = None
        self._cached_path = None
        self._last_render_key = None
        self._status_timer = None
        self._dot_phase = 0
        self._last_self_copy = 0.0
        self._hist_ids = []
        self.restart_flag = False
        self._bg_movie = None
        self._bg_pixmap = None
        self._bg_resize_timer = None
        self._image_loader = None
        self._fade_anim = None
        self._resize_edge = 0
        self._resize_start_geo = None
        self._resize_start_pos = None

        self.setWindowTitle(tr("win_title"))
        self.resize(1180, 720)
        self.setMinimumSize(920, 540)
        # Restore saved window geometry
        cfg = load_config()
        saved_geo = cfg.get("win_geometry")
        geo_ok = bool(saved_geo and len(saved_geo) == 4)
        if geo_ok:
            # 保存值若是最大化尺寸（>=屏幕可用区），属污染数据，
            # 不能当作普通几何，否则最大化还原后仍是全屏
            _avail = QApplication.primaryScreen().availableGeometry()
            if saved_geo[2] >= _avail.width() and saved_geo[3] >= _avail.height():
                geo_ok = False
        if geo_ok:
            self.setGeometry(saved_geo[0], saved_geo[1], saved_geo[2], saved_geo[3])
        if cfg.get("win_maximized", False):
            self.showMaximized()
        if LOGO_ICO and os.path.exists(LOGO_ICO):
            self.setWindowIcon(QIcon(LOGO_ICO))

        self._init_tray()  # 提前创建托盘图标，保证启动后及时显示
        # 启动时按配置初始化临时会话（合并原隐私模式：暂停记录 + 退出即清空）
        self._session_active = False
        self._session_baseline = None
        _session_on = bool(load_config().get("temporary_session", False)
                           or load_config().get("privacy_mode", False))
        if _session_on:
            self._start_session()
        if getattr(self, "_tray_session_act", None) is not None:
            self._tray_session_act.setChecked(_session_on)
        self._build_ui()
        self._apply_background()
        QTimer.singleShot(150, self._initial_refresh)
        QTimer.singleShot(250, self._focus_search)
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_monitor)
        self._poll_timer.start(120)
        # 文件失效即时检测：源文件删除后当场置灰，无需手动刷新
        self._file_sig = None
        self._file_check_timer = QTimer(self)
        self._file_check_timer.timeout.connect(self._check_files_changed)
        self._file_check_timer.start(2000)
        self._animate_dot()
        # 桌面小组件（可选功能，默认开启；延迟创建不影响启动速度）
        self._desk_widget = None
        QTimer.singleShot(600, self._apply_desktop_widget)
        # 启动构建完成后回收一次内存垃圾，降低常驻占用
        QTimer.singleShot(3000, gc.collect)
        QTimer.singleShot(10000, gc.collect)
        # Win11 无边框窗口强制直角，消除左上角缝隙
        QTimer.singleShot(0, lambda: _force_square_corners(self))
        self._apply_max_state()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self):
        central = QWidget()
        central.setStyleSheet("background: transparent;")
        self.setCentralWidget(central)
        self._bg_label = QLabel(central)
        self._bg_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._bg_label.setGeometry(0, 0, 9999, 9999)
        self._bg_label.lower()

        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 6)
        root.setSpacing(0)
        # Custom title bar (replaces native Windows title bar)
        self._title_bar = _TitleBar(self)
        root.addWidget(self._title_bar)
        self._build_header(root)

        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setHandleWidth(6)
        self._splitter.setStyleSheet("QSplitter::handle { background: transparent; }")
        root.addWidget(self._splitter, 1)

        self._tabs = QTabWidget()
        self._tabs.currentChanged.connect(self._on_tab_changed)
        self._splitter.addWidget(self._tabs)

        right_split = QSplitter(Qt.Orientation.Vertical)
        right_split.setHandleWidth(6)
        right_split.setStyleSheet("QSplitter::handle { background: transparent; }")
        self._splitter.addWidget(right_split)
        self._build_preview_panel(right_split)
        self._build_history_panel(right_split)
        right_split.setSizes([400, 200])
        self._splitter.setSizes([700, 380])

        self._tabs.blockSignals(True)
        self._tabs.setIconSize(QSize(18, 18))
        for etype in ("text", "image", "file", "url"):
            tab_w = QWidget()
            self._tabs.addTab(tab_w, f"  {self._type_label(etype)}  0  ")
            self._tabs.setTabIcon(self._tabs.count() - 1,
                                  QIcon(_res_icon(TAB_ICON_FILES[etype])))
            self._build_tab(tab_w, etype)
        self._tabs.blockSignals(False)

        self._build_statusbar(root)
        # Resize grip icon (bottom-right corner)
        self._resize_grip = _ResizeGrip(self)
        self._resize_grip.raise_()
        # Edge resize handles (thin strips with cursor feedback)
        self._edge_left = _EdgeHandle(self, 'left')
        self._edge_right = _EdgeHandle(self, 'right')
        self._edge_top = _EdgeHandle(self, 'top')
        self._edge_bottom = _EdgeHandle(self, 'bottom')
        # Corner resize handles (top-left / top-right / bottom-left, diagonal cursors)
        self._corner_tl = _CornerHandle(self, 'tl')
        self._corner_tr = _CornerHandle(self, 'tr')
        self._corner_bl = _CornerHandle(self, 'bl')
        self._bind_shortcuts()

    def _build_header(self, root):
        header = QFrame()
        header.setStyleSheet(f"""
            QFrame {{
                background: {C['HEADER_ALPHA']};
                border: none;
                border-radius: 0;
            }}
            QLabel {{
                background: transparent;
            }}
        """)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(18, 12, 18, 10)
        hl.setSpacing(14)

        if LOGO_ICO and os.path.exists(LOGO_ICO):
            logo_lbl = QLabel()
            pm = QIcon(LOGO_ICO).pixmap(QSize(48, 48)).scaled(
                36, 36, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            logo_lbl.setPixmap(pm)
            logo_lbl.setStyleSheet("background: transparent;")
            hl.addWidget(logo_lbl)

        brand_box = QVBoxLayout()
        brand_box.setSpacing(2)
        brand_row = QHBoxLayout()
        name_lbl = QLabel(APP_NAME)
        name_lbl.setFont(QFont("Bahnschrift", 17, QFont.Weight.Bold))
        name_lbl.setStyleSheet(f"color: {C['TEXT']}; background: transparent;")
        brand_row.addWidget(name_lbl)
        sub_lbl = QLabel(tr("brand_sub"))
        sub_lbl.setStyleSheet(f"color: {C['TEXT_SEC']}; font-size: 12px; background: transparent;")
        brand_row.addWidget(sub_lbl)
        brand_row.addStretch()
        brand_box.addLayout(brand_row)
        sub_row = QHBoxLayout()
        self._dot_lbl = QLabel()
        self._dot_lbl.setFixedSize(8, 8)
        self._dot_lbl.setStyleSheet(f"background: {C['SUCCESS']}; border-radius: 4px;")
        sub_row.addWidget(self._dot_lbl)
        tag_lbl = QLabel("CLIPBOARD HISTORY")
        tag_lbl.setStyleSheet(
            f"color: {C['TEXT_MUTED']}; font-size: 9px; letter-spacing: 2px; "
            f"background: transparent;")
        sub_row.addWidget(tag_lbl)
        sub_row.addStretch()
        brand_box.addLayout(sub_row)
        hl.addLayout(brand_box)
        hl.addStretch()

        self._monitor_lbl = QLabel(tr("monitor_live") if self.monitor else tr("monitor_off"))
        self._monitor_lbl.setStyleSheet(f"color: {C['SUCCESS']}; font-size: 11px; background: transparent;")
        hl.addWidget(self._monitor_lbl)
        self._header_count = QLabel(tr("total_records", n=0))
        self._header_count.setStyleSheet(f"color: {C['TEXT_SEC']}; font-size: 12px; background: transparent;")
        hl.addWidget(self._header_count)

        settings_btn = QPushButton()
        settings_btn.setIcon(QIcon(ICO_SETTINGS))
        settings_btn.setIconSize(QSize(20, 20))
        settings_btn.setFixedSize(30, 26)
        settings_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        settings_btn.setStyleSheet(
            "QPushButton { border: none; background: transparent; border-radius: 6px; }"
            "QPushButton:hover { background: rgba(128,128,128,0.18); }"
            "QPushButton:pressed { background: rgba(128,128,128,0.30); }")
        settings_btn.clicked.connect(self._open_settings)
        hl.addWidget(settings_btn)
        self._manage_btn = QPushButton(tr("manage"))
        self._manage_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._manage_btn.clicked.connect(self._show_manage_menu)
        hl.addWidget(self._manage_btn)
        root.addWidget(header)

        self.lightbar = AmbientLightBar(theme=load_config().get("theme", "dark"))
        root.addWidget(self.lightbar)

    def _apply_background(self):
        """Load and display custom background (image/GIF)."""
        cfg = load_config()
        # --- Image background ---
        bg_path = cfg.get("bg_image", "")
        if not bg_path or not os.path.exists(bg_path):
            self._bg_label.hide()
            return
        self._bg_label.show()
        if bg_path.lower().endswith(".gif"):
            self._bg_is_gif = True
            self._bg_image_path = ""
            self._bg_movie = QMovie(bg_path)
            self._bg_movie.frameChanged.connect(self._on_bg_frame)
            self._bg_movie.start()
        else:
            self._bg_is_gif = False
            self._bg_image_path = bg_path
            self._bg_pixmap = QPixmap(bg_path)
            self._scale_bg()

    def _on_bg_frame(self):
        if self._bg_movie:
            self._bg_pixmap = self._bg_movie.currentPixmap()
            self._scale_bg()

    def _scale_bg(self):
        pm = self._bg_pixmap
        is_gif = getattr(self, "_bg_is_gif", False)
        path = getattr(self, "_bg_image_path", "")
        # 静态图：缓存比窗口小（窗口放大）或缓存丢失时，从磁盘重新加载
        if (not is_gif and path and os.path.exists(path)
                and (pm is None or pm.isNull()
                     or self.width() > pm.width() or self.height() > pm.height())):
            pm = QPixmap(path)
        if pm and not pm.isNull():
            scaled = pm.scaled(
                self.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation)
            self._bg_label.setPixmap(scaled)
            self._bg_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._bg_label.setGeometry(self.rect())
            if not is_gif:
                # 只保留缩放后的窗口尺寸副本，释放原图大图内存
                self._bg_pixmap = scaled

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, '_bg_label'):
            self._bg_label.setGeometry(self.rect())
            if self._bg_pixmap and not self._bg_pixmap.isNull():
                if self._bg_resize_timer:
                    self._bg_resize_timer.stop()
                self._bg_resize_timer = QTimer()
                self._bg_resize_timer.setSingleShot(True)
                self._bg_resize_timer.timeout.connect(self._scale_bg)
                self._bg_resize_timer.start(30)
        # Re-render preview image on resize (label's own resizeEvent also handles this)
        if hasattr(self, '_cached_qpixmap') and self._cached_qpixmap and not self._cached_qpixmap.isNull():
            self._last_render_key = None
            self._render_preview_image()
        # Reposition resize grip to bottom-right corner
        if hasattr(self, '_resize_grip'):
            if self.isMaximized():
                self._resize_grip.hide()
            else:
                self._resize_grip.show()
                self._resize_grip.move(self.width() - 20, self.height() - 20)
        # Reposition edge resize handles
        # 最大化时也保留边缘热区：拖拽时先还原再缩放（见 _EdgeHandle.mousePressEvent）
        if hasattr(self, '_edge_left'):
            w, h = self.width(), self.height()
            t = _EdgeHandle.THICKNESS
            self._edge_left.setGeometry(0, t, t, h - 2 * t)
            self._edge_right.setGeometry(w - t, t, t, h - 2 * t)
            self._edge_top.setGeometry(t, 0, w - 2 * t, t)
            self._edge_bottom.setGeometry(t, h - t, w - 2 * t, t)
            self._edge_left.show()
            self._edge_right.show()
            self._edge_top.show()
            self._edge_bottom.show()
            self._edge_left.raise_()
            self._edge_right.raise_()
            self._edge_top.raise_()
            self._edge_bottom.raise_()
        # Reposition corner resize handles (top-left / top-right / bottom-left)
        if hasattr(self, '_corner_tl'):
            w, h = self.width(), self.height()
            s = _CornerHandle.SIZE
            if self.isMaximized():
                self._corner_tl.hide()
                self._corner_tr.hide()
                self._corner_bl.hide()
            else:
                self._corner_tl.move(0, 0)
                self._corner_tr.move(w - s, 0)
                self._corner_bl.move(0, h - s)
                for _c in (self._corner_tl, self._corner_tr, self._corner_bl):
                    _c.show()
                    _c.raise_()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            if hasattr(self, '_title_bar'):
                self._title_bar.update_max_btn()
            self._apply_max_state()
            if self.isMinimized():
                self._pause_motion()
            elif self.isVisible():
                self._resume_motion()

    def showEvent(self, event):
        super().showEvent(event)
        self._resume_motion()

    def hideEvent(self, event):
        self._pause_motion()
        super().hideEvent(event)

    def _pause_motion(self):
        """Stop all continuous animations while hidden/minimized (saves CPU)."""
        try:
            self.lightbar.pause()
        except Exception:
            pass
        try:
            self._title_bar.pause_shimmer()
        except Exception:
            pass
        if getattr(self, "_bg_movie", None):
            self._bg_movie.setPaused(True)
        # 隐藏/最小化时释放进程工作集，降低后台常驻内存
        try:
            k32 = ctypes.windll.kernel32
            k32.SetProcessWorkingSetSize.argtypes = [
                ctypes.wintypes.HANDLE, ctypes.c_size_t, ctypes.c_size_t]
            _minus1 = ctypes.c_size_t(-1).value
            k32.SetProcessWorkingSetSize(k32.GetCurrentProcess(), _minus1, _minus1)
        except Exception:
            pass

    def _resume_motion(self):
        """Resume animations when the window becomes visible again."""
        try:
            self.lightbar.resume()
        except Exception:
            pass
        try:
            self._title_bar.resume_shimmer()
        except Exception:
            pass
        if getattr(self, "_bg_movie", None):
            self._bg_movie.setPaused(False)

    # ------------------------------------------------------------------
    # Frameless window edge resize (pure Qt mouse events)
    # ------------------------------------------------------------------
    _RESIZE_BORDER = 8

    def _edge_at(self, pos):
        """Return edge code for a local position, or 0 if not on resize border."""
        if self.isMaximized():
            return 0
        r = self.rect()
        b = self._RESIZE_BORDER
        x, y = pos.x(), pos.y()
        left = x < b
        right = x > r.width() - b
        top = y < b
        bottom = y > r.height() - b
        if top and left:
            return 1
        if top and right:
            return 2
        if bottom and left:
            return 3
        if bottom and right:
            return 4
        if top:
            return 5
        if bottom:
            return 6
        if left:
            return 7
        if right:
            return 8
        return 0

    _EDGE_CURSORS = {
        1: Qt.CursorShape.SizeFDiagCursor,
        4: Qt.CursorShape.SizeFDiagCursor,
        2: Qt.CursorShape.SizeBDiagCursor,
        3: Qt.CursorShape.SizeBDiagCursor,
        5: Qt.CursorShape.SizeVerCursor,
        6: Qt.CursorShape.SizeVerCursor,
        7: Qt.CursorShape.SizeHorCursor,
        8: Qt.CursorShape.SizeHorCursor,
    }

    def mouseMoveEvent(self, event):
        if hasattr(self, '_resize_edge') and self._resize_edge and event.buttons() & Qt.MouseButton.LeftButton:
            self._do_resize(event.globalPosition().toPoint())
            return
        edge = self._edge_at(event.position().toPoint())
        self.setCursor(self._EDGE_CURSORS.get(edge, Qt.CursorShape.ArrowCursor))
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            edge = self._edge_at(event.position().toPoint())
            if edge:
                self._resize_edge = edge
                self._resize_start_geo = self.geometry()
                self._resize_start_pos = event.globalPosition().toPoint()
                event.accept()
                return
        self._resize_edge = 0
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self._resize_edge = 0
        super().mouseReleaseEvent(event)

    def _do_resize(self, global_pos):
        if not hasattr(self, '_resize_start_geo') or not self._resize_start_geo:
            return
        dx = global_pos.x() - self._resize_start_pos.x()
        dy = global_pos.y() - self._resize_start_pos.y()
        geo = self._resize_start_geo
        x, y, w, h = geo.x(), geo.y(), geo.width(), geo.height()
        min_w, min_h = self.minimumWidth(), self.minimumHeight()
        edge = self._resize_edge
        if edge in (1, 3, 7):  # left side
            new_w = w - dx
            if new_w >= min_w:
                x = geo.x() + dx
                w = new_w
        if edge in (2, 4, 8):  # right side
            w = max(min_w, w + dx)
        if edge in (1, 2, 5):  # top side
            new_h = h - dy
            if new_h >= min_h:
                y = geo.y() + dy
                h = new_h
        if edge in (3, 4, 6):  # bottom side
            h = max(min_h, h + dy)
        self.setGeometry(x, y, w, h)

    # ------------------------------------------------------------------
    # Tab construction
    # ------------------------------------------------------------------
    @staticmethod
    def _type_label(etype):
        return {"text": tr("type_text"), "image": tr("type_image"),
                "file": tr("type_file"), "url": tr("type_url")}[etype]

    def _build_tab(self, parent, etype):
        lay = QVBoxLayout(parent)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)
        self._tab_layouts.append(lay)

        search_row = QHBoxLayout()
        search_edit = QLineEdit()
        search_edit.setObjectName("searchEdit")
        search_edit.setPlaceholderText(tr("col_preview") + "...")
        _sousuo = _res_icon("sousuo.ico")
        if os.path.exists(_sousuo):
            search_edit.addAction(QIcon(_sousuo),
                                  QLineEdit.ActionPosition.LeadingPosition)
        search_edit.setClearButtonEnabled(True)
        search_edit.textChanged.connect(lambda _, t=etype: self._debounce_search(t))
        search_edit.returnPressed.connect(self._copy_selected)
        self._search_edits[etype] = search_edit
        search_row.addWidget(search_edit, 1)

        count_lbl = QLabel("")
        count_lbl.setStyleSheet(f"color: {C['TEXT_MUTED']}; font-size: 11px;")
        self._count_labels[etype] = count_lbl
        search_row.addWidget(count_lbl)

        sort_ids = (["default", "oldest"] if etype in ("text", "url") else
                    ["default", "oldest", "name_az", "name_za",
                     "fmt_az", "fmt_za", "size_desc", "size_asc"])
        combo = _SortMenuButton()
        combo.addItems([tr("sort_" + sid) for sid in sort_ids])
        combo.setFixedWidth(150)
        combo.currentIndexChanged.connect(
            lambda idx, t=etype, ids=sort_ids: self._on_sort_changed(t, idx, ids))
        self._sort_combos[etype] = combo
        search_row.addWidget(combo)
        lay.addLayout(search_row)

        act_row = QHBoxLayout()
        copy_btn = QPushButton(tr("btn_copy"))
        copy_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        copy_btn.clicked.connect(self._copy_selected)
        act_row.addWidget(copy_btn)
        for label, slot in [(tr("btn_pin"), self._pin_selected),
                            (tr("btn_unpin"), self._unpin_selected),
                            (tr("btn_delete"), self._delete_selected)]:
            btn = QPushButton(label)
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn.clicked.connect(slot)
            act_row.addWidget(btn)
        act_row.addStretch()
        if etype in ("image", "file", "url"):
            open_btn = QPushButton(tr("btn_open"))
            open_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            open_btn.clicked.connect(self._open_selected)
            act_row.addWidget(open_btn)
        if etype == "file":
            purge_btn = QPushButton(tr("btn_purge_missing"))
            purge_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            purge_btn.clicked.connect(self._purge_missing_files)
            act_row.addWidget(purge_btn)
        export_btn = QPushButton(tr("btn_export"))
        export_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        export_btn.clicked.connect(self._export_selected)
        act_row.addWidget(export_btn)
        lay.addLayout(act_row)

        table = QTableWidget()
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        table.customContextMenuRequested.connect(
            lambda pos, t=etype: self._on_right_click(t, pos))
        table.doubleClicked.connect(lambda _: self._open_selected())
        table.itemSelectionChanged.connect(
            lambda t=etype: self._on_selection_changed(t))

        if etype == "text":
            table.setColumnCount(4)
            table.setHorizontalHeaderLabels(["#", tr("col_time"), "", tr("col_preview")])
            table.setColumnWidth(0, 44)
            table.setColumnWidth(1, 150)
            table.setColumnWidth(2, 30)
            table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        elif etype == "url":
            table.setColumnCount(4)
            table.setHorizontalHeaderLabels(["#", tr("col_time"), "", tr("col_url")])
            table.setColumnWidth(0, 44)
            table.setColumnWidth(1, 150)
            table.setColumnWidth(2, 30)
            table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        elif etype == "image":
            table.setColumnCount(7)
            table.setHorizontalHeaderLabels(
                ["#", tr("col_time"), "", tr("col_filename"), tr("col_format"),
                 tr("col_dims"), tr("col_size")])
            table.setColumnWidth(0, 44)
            table.setColumnWidth(1, 140)
            table.setColumnWidth(2, 30)
            table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
            table.setColumnWidth(4, 58)
            table.setColumnWidth(5, 92)
            table.setColumnWidth(6, 74)
        else:
            table.setColumnCount(7)
            table.setHorizontalHeaderLabels(
                ["#", tr("col_time"), "", tr("col_count"), tr("col_format"),
                 tr("col_size"), tr("col_files")])
            table.setColumnWidth(0, 44)
            table.setColumnWidth(1, 132)
            table.setColumnWidth(2, 30)
            table.setColumnWidth(3, 46)
            table.setColumnWidth(4, 62)
            table.setColumnWidth(5, 74)
            table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)

        lay.addWidget(table, 1)
        self._tables[etype] = table
        if etype == "text":
            # 文本预览列：换行处渲染内联 huiche 图标
            if not hasattr(self, "_text_preview_delegate"):
                self._text_preview_delegate = _InlineImageDelegate(table)
            table.setItemDelegateForColumn(3, self._text_preview_delegate)

    def _build_preview_panel(self, parent_split):
        pf = QFrame()
        pf.setStyleSheet("background: transparent; border: none; border-radius: 8px;")
        pl = QVBoxLayout(pf)
        pl.setContentsMargins(8, 6, 8, 6)
        pl.setSpacing(4)
        title = QLabel(tr("panel_preview"))
        title.setStyleSheet(f"color: {C['ACCENT']}; font-weight: bold; font-size: 12px;")
        pl.addWidget(title)
        self._preview_scroll = QScrollArea()
        self._preview_scroll.setWidgetResizable(True)
        self._preview_inner = QWidget()
        self._preview_inner.setStyleSheet(f"background: {C['PANEL_ALPHA2']};")
        self._preview_layout = QVBoxLayout(self._preview_inner)
        self._preview_layout.setContentsMargins(4, 4, 4, 4)
        self._preview_layout.setSpacing(4)
        self._preview_scroll.setWidget(self._preview_inner)
        pl.addWidget(self._preview_scroll, 1)
        self._show_preview_placeholder()
        parent_split.addWidget(pf)

    def _build_history_panel(self, parent_split):
        hf = QFrame()
        hf.setStyleSheet("background: transparent; border: none; border-radius: 8px;")
        hl = QVBoxLayout(hf)
        hl.setContentsMargins(8, 6, 8, 6)
        hl.setSpacing(4)
        # 与上方预览面板之间的隔断
        sep_top = QFrame()
        sep_top.setFrameShape(QFrame.Shape.HLine)
        sep_top.setFixedHeight(1)
        sep_top.setStyleSheet(f"background: {C['BORDER']}; border: none; margin-bottom: 2px;")
        hl.addWidget(sep_top)
        title = QLabel(tr("panel_snapshots"))
        title.setStyleSheet(f"color: {C['ACCENT']}; font-weight: bold; font-size: 12px;")
        hl.addWidget(title)
        self._hist_list = QListWidget()
        self._hist_list.setStyleSheet(f"QListWidget {{ background: {C['PANEL_ALPHA2']}; }}")
        self._hist_list.setAlternatingRowColors(False)
        self._hist_list.setSpacing(2)
        self._hist_list.doubleClicked.connect(lambda: self._restore_history())
        hl.addWidget(self._hist_list, 1)
        # 列表与操作按钮之间的隔断
        sep_btn = QFrame()
        sep_btn.setFrameShape(QFrame.Shape.HLine)
        sep_btn.setFixedHeight(1)
        sep_btn.setStyleSheet(f"background: {C['BORDER']}; border: none; margin-top: 2px; margin-bottom: 2px;")
        hl.addWidget(sep_btn)
        btn_row = QHBoxLayout()
        rb = QPushButton(tr("btn_restore"))
        rb.setStyleSheet(f"background: {C['SURFACE2']}; color: {C['ACCENT']}; "
                         f"border: 1px solid {C['BORDER']}; border-radius: 6px; "
                         f"padding: 6px 12px; font-weight: bold; font-size: 12px;")
        rb.clicked.connect(self._restore_history)
        btn_row.addWidget(rb)
        btn_row.addStretch()
        cb = QPushButton(tr("btn_clear_history"))
        cb.setProperty("cssClass", "danger")
        cb.clicked.connect(self._clear_history)
        btn_row.addWidget(cb)
        hl.addLayout(btn_row)
        parent_split.addWidget(hf)

    def _build_statusbar(self, root):
        bar = QFrame()
        bar.setFixedHeight(34)
        bar.setStyleSheet("background: transparent; border: none;")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(12, 0, 12, 0)
        self._hint_lbl = QLabel()
        self._hint_lbl.setStyleSheet(f"color: {C['TEXT_MUTED']}; font-size: 11px; background: transparent;")
        bl.addWidget(self._hint_lbl)
        bl.addStretch()
        self._sel_lbl = QLabel()
        self._sel_lbl.setStyleSheet(f"color: {C['ACCENT']}; font-size: 12px; font-weight: bold; background: transparent;")
        bl.addWidget(self._sel_lbl)
        self._status_lbl = QLabel()
        self._status_lbl.setStyleSheet(f"color: {C['TEXT_SEC']}; font-size: 12px; font-weight: bold; background: transparent;")
        bl.addWidget(self._status_lbl)
        root.addWidget(bar)

    def _bind_shortcuts(self):
        """绑定主窗口快捷键（可自定义动作在设置 → 动作快捷键中修改）。"""
        for sc in getattr(self, "_shortcuts", []):
            try:
                sc.setParent(None)
            except Exception:
                pass
        self._shortcuts = []
        cfg = load_config()

        def _add(hk, slot):
            seq = _hotkey_to_sequence(hk)
            if seq is None:
                return
            sc = QShortcut(seq, self, activated=slot)
            self._shortcuts.append(sc)

        # 固定快捷键（不提供修改）
        _add("f5", self._refresh_all)
        _add("ctrl+a", self._select_all)
        _add("ctrl+o", self._open_selected)
        _add("ctrl+e", self._export_selected)
        _add("escape", self._focus_search)
        # 可自定义的动作快捷键
        _add(cfg.get("hk_copy", _ACTION_HOTKEY_DEFAULTS["hk_copy"]),
             self._copy_selected)
        _add(cfg.get("hk_delete", _ACTION_HOTKEY_DEFAULTS["hk_delete"]),
             self._delete_selected)
        _add(cfg.get("hk_pin", _ACTION_HOTKEY_DEFAULTS["hk_pin"]),
             self._toggle_pin_selected)
        _add(cfg.get("hk_next_tab", _ACTION_HOTKEY_DEFAULTS["hk_next_tab"]),
             self._next_tab)
        _add(cfg.get("hk_prev_tab", _ACTION_HOTKEY_DEFAULTS["hk_prev_tab"]),
             self._prev_tab)

        # Tab 组合无法靠 QShortcut 触发（焦点导航优先），改用应用级过滤器
        app = QApplication.instance()
        if app is not None:
            old = getattr(self, "_tab_filter", None)
            if old is not None:
                try:
                    app.removeEventFilter(old)
                except Exception:
                    pass
            self._tab_filter = _TabHotkeyFilter(self)
            app.installEventFilter(self._tab_filter)

    def _next_tab(self):
        n = self._tabs.count()
        if n:
            self._tabs.setCurrentIndex((self._tabs.currentIndex() + 1) % n)

    def _prev_tab(self):
        n = self._tabs.count()
        if n:
            self._tabs.setCurrentIndex((self._tabs.currentIndex() - 1) % n)

    # ------------------------------------------------------------------
    # Refresh / Search / Sort
    # ------------------------------------------------------------------
    def _rebuild_index(self):
        idx, pinned = {}, set()
        for etype in ("text", "image", "file", "url"):
            cat = self.store.categories[etype]
            for e in cat["pinned"]:
                idx[e["hash"]] = e
                pinned.add(e["hash"])
            for e in cat["entries"]:
                idx[e["hash"]] = e
        self._entry_index = idx
        self._pinned_hashes = pinned

    def _initial_refresh(self):
        for etype in ("text", "image", "file", "url"):
            self._refresh_tab(etype)
        self._refresh_history_list()
        self._update_hint()
        self._update_desk_widget()
        try:
            self.lightbar.surge(210.0, 0.8)
            for i, x in enumerate((0.2, 0.5, 0.8)):
                QTimer.singleShot(180 * i, lambda x=x: self.lightbar.pulse(
                    200.0 + x * 60.0, x, strength=0.8))
        except Exception:
            pass

    def _refresh_all(self):
        for etype in ("text", "image", "file", "url"):
            self._refresh_tab(etype)
        self._refresh_history_list()
        self._update_preview()
        self._set_status(tr("st_refreshed"), "ok")

    def _debounce_search(self, etype):
        if etype in self._search_timers:
            self._search_timers[etype].stop()
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: self._refresh_tab(etype))
        timer.start(160)
        self._search_timers[etype] = timer

    def _style_sort_combo(self, combo):
        """让排序下拉（含展开的弹层）完全跟随主题，避免出现系统默认的浅色弹层。"""
        q = QColor(C['SURFACE2'])
        combo.setStyleSheet(f"""
            QComboBox {{ background: {C['SURFACE2']}; color: {C['TEXT_SEC']};
                border: 1px solid {C['BORDER']}; border-radius: 6px; padding: 4px 10px; font-size: 11px; }}
            QComboBox:hover {{ border-color: {C['BORDER_LT']}; }}
            QComboBox::drop-down {{ border: none; width: 20px; }}
            QComboBox QAbstractItemView {{ background: transparent; color: {C['TEXT']};
                selection-background-color: {C['ACCENT_DIM']}; selection-color: {C['TEXT']};
                border: none; outline: none; }}
            QComboBox QAbstractItemView::item {{ min-height: 24px; padding: 6px 12px;
                border-bottom: 1px solid {C['BORDER']}; color: {C['TEXT']}; }}
            QComboBox QAbstractItemView::item:hover {{ background: {C['SURFACE3']}; color: {C['TEXT']}; }}
            QComboBox QAbstractItemView::item:selected {{ background: {C['ACCENT_DIM']}; color: {C['TEXT']}; }}
        """)
        roles = [
            (QPalette.ColorRole.Window, q),
            (QPalette.ColorRole.Base, q),
            (QPalette.ColorRole.Text, QColor(C['TEXT'])),
            (QPalette.ColorRole.WindowText, QColor(C['TEXT'])),
            (QPalette.ColorRole.Highlight, QColor(C['ACCENT_DIM'])),
            (QPalette.ColorRole.HighlightedText, QColor(C['TEXT'])),
        ]
        pal = combo.palette()
        for role, col in roles:
            pal.setColor(role, col)
        combo.setPalette(pal)
        view = combo.view()
        if view is not None:
            vp = view.palette()
            for role, col in roles:
                vp.setColor(role, col)
            view.setPalette(vp)
            view.setUniformItemSizes(True)
            view.setStyleSheet(f"""
                QListView {{ background: transparent; border: none; outline: none; }}
                QListView::item {{ min-height: 24px; padding: 6px 12px;
                    border-bottom: 1px solid {C['BORDER']}; color: {C['TEXT']}; }}
                QListView::item:hover {{ background: {C['SURFACE3']}; color: {C['TEXT']}; }}
                QListView::item:selected {{ background: {C['ACCENT_DIM']}; color: {C['TEXT']}; }}
            """)
            # 由弹层容器绘制圆角主题面板，列表透明，避免圆角外露出黑色方角条带
            container = view.window()
            if container is not None and container is not combo:
                container.setAutoFillBackground(False)
                try:
                    container.setContentsMargins(0, 0, 0, 0)
                    container.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
                except Exception:
                    pass
                cpal = container.palette()
                cpal.setColor(QPalette.ColorRole.Window, QColor(0, 0, 0, 0))
                cpal.setColor(QPalette.ColorRole.Base, QColor(0, 0, 0, 0))
                container.setPalette(cpal)
                container.setStyleSheet(
                    f"QComboBoxPrivateContainer {{ background: {C['PANEL_ALPHA']}; "
                    f"border: 1px solid {C['BORDER']}; border-radius: 8px; outline: none; }}")

    def _on_sort_changed(self, etype, idx, ids):
        if 0 <= idx < len(ids):
            self._sort_orders[etype] = ids[idx]
        self._refresh_tab(etype)

    def _apply_sort(self, etype, entries):
        order = self._sort_orders.get(etype, "default")
        pinned = [e for e in entries if e["hash"] in self._pinned_hashes]
        unpinned = [e for e in entries if e["hash"] not in self._pinned_hashes]

        def key(e):
            if order in ("name_az", "name_za"):
                if e.get("type") == "image":
                    src = e.get("source_name", "")
                    name = os.path.splitext(src if src else os.path.basename(e.get("filename", "")))[0]
                else:
                    paths = e.get("file_paths", [])
                    name = os.path.splitext(os.path.basename(paths[0]) if paths else "")[0]
                return _filename_sort_key(name)
            if order in ("fmt_az", "fmt_za"):
                if e.get("type") == "image":
                    return e.get("original_format", "").lower()
                paths = e.get("file_paths", [])
                return os.path.splitext(paths[0])[1].lower() if paths else ""
            if order in ("size_desc", "size_asc"):
                if e.get("type") == "image":
                    return e.get("file_size", 0)
                sizes = e.get("file_sizes", [])
                return sum(s for s in sizes if s > 0) if sizes else 0
            return e.get("timestamp", "")

        reverse = order in ("default", "name_za", "fmt_za", "size_desc")
        unpinned.sort(key=key, reverse=reverse)
        return pinned + unpinned

    def _refresh_tab(self, etype):
        table = self._tables.get(etype)
        if not table:
            return
        self._rebuild_index()
        kw = self._search_edits.get(etype, QLineEdit()).text().strip().lower()
        entries = self.store.search(kw, etype) if kw else self.store.get_by_type(etype)
        entries = self._apply_sort(etype, entries)
        total_all = len(entries)
        shown = entries[:DISPLAY_LIMIT]
        table.setRowCount(len(shown))
        iid_map = {}
        pin_color = QColor(C['PIN_BG'])
        for i, entry in enumerate(shown):
            ts = entry.get("timestamp", "")
            try:
                time_str = datetime.fromisoformat(ts).strftime(TIME_FORMAT)
            except ValueError:
                time_str = ts[:19] if len(ts) >= 19 else ts
            is_pin = entry["hash"] in self._pinned_hashes
            status = "\U0001f4cc" if is_pin else ""
            if etype == "text":
                content = entry.get("content", "")
                preview = content[:120].replace("\n", " ⏎ ").replace("\t", "  ")
                preview_html = _text_preview_html(content, 120)
                if len(content) > 120:
                    preview += "…"
                vals = [str(i + 1), time_str, status, preview]
            elif etype == "url":
                vals = [str(i + 1), time_str, status, entry.get("content", "")]
            elif etype == "image":
                src = entry.get("source_name", "")
                fn = src if src else os.path.basename(entry.get("filename", ""))
                vals = [str(i + 1), time_str, status, fn,
                        fmt_image_type(entry.get("original_format", "?")),
                        f"{entry.get('width', '?')}x{entry.get('height', '?')}",
                        fmt_size(entry.get("file_size", 0))]
            else:
                paths = self.store._norm_paths(entry)
                sizes = entry.get("file_sizes", [])
                total_sz = sum(s for s in sizes if s > 0) if sizes else 0
                fp = "  |  ".join(os.path.basename(p) for p in paths[:6])
                if len(paths) > 6:
                    fp += f"  …(+{len(paths) - 6})"
                if self.store.file_entry_missing(entry):
                    fp += f"  {tr('file_missing')}"
                vals = [str(i + 1), time_str, status,
                        str(entry.get("file_count", len(paths))),
                        _extract_extensions(paths),
                        fmt_size(total_sz) if total_sz > 0 else "?", fp]
            missing = (etype == "file") and self.store.file_entry_missing(entry)
            for col, val in enumerate(vals):
                item = QTableWidgetItem(val)
                if etype == "text" and col == 3:
                    item.setData(_InlineImageDelegate.HTML_ROLE, preview_html)
                if col in (0, 2):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if is_pin:
                    item.setBackground(pin_color)
                if missing:
                    item.setForeground(QColor(C['TEXT_MUTED']))
                    if col == len(vals) - 1:
                        item.setIcon(self._missing_icon())
                table.setItem(i, col, item)
            iid_map[i] = entry["hash"]
        self._iid_to_hash[etype] = iid_map
        pin_n = self.store.pinned_count(etype)
        cl = self._count_labels.get(etype)
        if cl:
            if kw:
                cl.setText(tr("count_match", n=total_all))
            elif total_all > DISPLAY_LIMIT:
                cl.setText(tr("count_shown", shown=DISPLAY_LIMIT, total=total_all, pinned=pin_n))
            else:
                cl.setText(tr("count_total", total=total_all, pinned=pin_n))
        self._update_tab_badge(etype)
        self._update_header_stats()

    def _purge_missing_files(self):
        n = self.store.purge_missing_files()
        self._set_status(tr("purge_done", n=n), "ok")
        self._refresh_tab("file")
        self._refresh_history_list()
        self._update_desk_widget()

    def _missing_icon(self):
        if not hasattr(self, "_jinggao_icon"):
            self._jinggao_icon = QIcon(_res_icon("jinggao.ico"))
        return self._jinggao_icon

    def _check_files_changed(self):
        """定时检测文件失效状态，源文件被删除时当场刷新（无需手动 F5）。"""
        try:
            entries = self.store.get_by_type("file")
            sig = tuple(self.store.file_entry_missing(e) for e in entries)
        except Exception:
            return
        if getattr(self, "_file_sig", None) != sig:
            self._file_sig = sig
            self._refresh_tab("file")
            self._update_desk_widget()

    def _update_tab_badge(self, etype):
        n = self.store.count(etype)
        idx = ("text", "image", "file", "url").index(etype)
        self._tabs.setTabText(idx, f"  {self._type_label(etype)}  {n}  ")

    def _update_header_stats(self):
        self._header_count.setText(tr("total_records", n=self.store.count()))

    # ------------------------------------------------------------------
    # Snapshot history
    # ------------------------------------------------------------------
    def _refresh_history_list(self, animate=False):
        self._hist_list.clear()
        snaps = list(reversed(self.store.get_snapshots()))
        self._hist_ids = []
        for snap in snaps[:HIST_DISPLAY]:
            ts = snap.get("time", "")
            try:
                ts_str = datetime.fromisoformat(ts).strftime("%m-%d %H:%M:%S")
            except ValueError:
                ts_str = ts[:16]
            self._hist_list.addItem(f"  {ts_str}   {snap.get('desc', '?')}")
            self._hist_ids.append(snap["id"])
        if animate and self._hist_list.count() > 0:
            self._animate_new_snapshot()

    def _animate_new_snapshot(self):
        """Briefly highlight the newest snapshot item with a fade-out effect."""
        item = self._hist_list.item(0)
        if not item:
            return
        self._snap_anim_step = 0
        self._snap_anim_item = item
        self._snap_anim_timer = QTimer(self)
        self._snap_anim_timer.timeout.connect(self._snap_anim_tick)
        self._snap_anim_timer.start(50)
        self._snap_anim_tick()

    def _snap_anim_tick(self):
        self._snap_anim_step += 1
        t = self._snap_anim_step / 12.0  # 12 steps * 50ms = 600ms
        if t >= 1.0:
            self._snap_anim_timer.stop()
            if self._snap_anim_item:
                self._snap_anim_item.setBackground(QColor(0, 0, 0, 0))
            return
        # Fade from accent color to transparent
        alpha = int(90 * (1.0 - t))
        accent = QColor(C['ACCENT'])
        accent.setAlpha(alpha)
        if self._snap_anim_item:
            self._snap_anim_item.setBackground(accent)

    def _restore_history(self):
        row = self._hist_list.currentRow()
        if row < 0 or row >= len(self._hist_ids):
            self._set_status(tr("snap_select_first"), "warn")
            return
        sid = self._hist_ids[row]
        snap = next((s for s in self.store.get_snapshots() if s["id"] == sid), None)
        if not snap:
            return
        ts = snap.get("time", "")[:19]
        ret = QMessageBox.question(self, tr("dlg_confirm_restore"),
            tr("msg_restore_confirm", ts=ts, desc=snap.get("desc", "?")),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ret != QMessageBox.StandardButton.Yes:
            return
        self.store.save_snapshot(tr("snap_before_restore"))
        self.store.restore_snapshot(sid)
        self._refresh_all()
        self._set_status(tr("st_restored"), "ok")

    def _clear_history(self):
        snaps = self.store.get_snapshots()
        if not snaps:
            self._set_status(tr("snap_empty"))
            return
        ret = QMessageBox.question(self, tr("dlg_confirm_clear"),
            tr("msg_clear_history", n=len(snaps)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ret != QMessageBox.StandardButton.Yes:
            return
        self.store.clear_snapshots()
        self._refresh_history_list()
        self._set_status(tr("st_history_cleared"), "ok")

    # ------------------------------------------------------------------
    # Tab switching / shortcuts / status bar
    # ------------------------------------------------------------------
    def _on_tab_changed(self, idx):
        types = ("text", "image", "file", "url")
        if 0 <= idx < len(types):
            self._active_type = types[idx]
            self._refresh_tab(self._active_type)
            self._update_preview()
            self._update_hint()
            self._focus_search()

    def _focus_search(self):
        edit = self._search_edits.get(self._active_type)
        if edit:
            edit.setFocus()
            edit.selectAll()

    def _select_all(self):
        table = self._tables.get(self._active_type)
        if table:
            table.selectAll()

    def _on_selection_changed(self, etype):
        if etype != self._active_type:
            return
        table = self._tables.get(etype)
        if not table:
            return
        n = len(table.selectionModel().selectedRows())
        self._sel_lbl.setText(tr("selected_n", n=n) if n > 1 else "")
        QTimer.singleShot(130, self._update_preview)

    def _set_status(self, msg, kind="info"):
        if self._status_timer:
            self._status_timer.stop()
        color = {"ok": C['SUCCESS'], "err": C['DANGER'], "warn": C['AMBER']}.get(kind, C['TEXT_SEC'])
        self._status_lbl.setStyleSheet(f"color: {color}; font-size: 12px; font-weight: bold; background: transparent;")
        self._status_lbl.setText(msg)
        self._status_timer = QTimer()
        self._status_timer.setSingleShot(True)
        self._status_timer.timeout.connect(lambda: self._status_lbl.setText(""))
        self._status_timer.start(4500)
        lb = getattr(self, "lightbar", None)
        if lb:
            hue = {"ok": 140.0, "err": 4.0, "warn": 38.0}.get(kind, 215.0)
            lb.surge(hue, 0.85 if kind in ("ok", "err") else 0.5)

    def _update_hint(self):
        hints = {"text": tr("hint_text"), "image": tr("hint_image"),
                 "file": tr("hint_file"), "url": tr("hint_url")}
        self._hint_lbl.setText(hints.get(self._active_type, ""))

    # ------------------------------------------------------------------
    # Preview panel
    # ------------------------------------------------------------------
    def _clear_preview(self):
        def _clear_layout(layout):
            while layout.count():
                item = layout.takeAt(0)
                w = item.widget()
                if w:
                    w.deleteLater()
                child_lay = item.layout()
                if child_lay:
                    _clear_layout(child_lay)
        _clear_layout(self._preview_layout)

    def _show_preview_placeholder(self):
        self._preview_gen += 1
        self._cur_image_path = None
        self._cur_image_entry = None
        self._clear_preview()
        lbl = QLabel(tr("preview_placeholder"))
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet(f"color: {C['TEXT_MUTED']}; font-size: 13px; padding: 30px;")
        lbl.setWordWrap(True)
        self._preview_layout.addWidget(lbl)
        self._preview_layout.addStretch()

    def _update_preview(self):
        table = self._tables.get(self._active_type)
        if not table:
            return
        rows = table.selectionModel().selectedRows()
        entry = None
        if rows:
            row = rows[0].row()
            h = self._iid_to_hash[self._active_type].get(row)
            entry = self._entry_index.get(h) if h else None
        if not entry:
            self._show_preview_placeholder()
            return
        etype = entry.get("type", "text")
        if etype == "text":
            self._preview_text(entry)
        elif etype == "image":
            self._preview_image(entry)
        elif etype == "url":
            self._preview_url(entry)
        else:
            self._preview_files(entry)

    def _preview_text(self, entry):
        self._preview_gen += 1
        self._cur_image_path = None
        self._cur_text_entry = entry
        self._clear_preview()
        content = entry.get("content", "")
        url_pat = re.compile(r'https?://\S+|www\.\S+')
        urls = url_pat.findall(content)
        stripped = url_pat.sub('', content).strip()
        is_pure_url = bool(urls) and not stripped
        if urls:
            url_box = QFrame()
            url_box.setStyleSheet(f"background: {C['SURFACE2']}; border-radius: 6px;")
            ul = QVBoxLayout(url_box)
            ul.setContentsMargins(6, 4, 6, 4)
            for u in urls:
                lbl = QLabel(u)
                lbl.setStyleSheet(f"color: {C['ACCENT']}; font-family: Consolas; font-size: 11px;")
                lbl.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
                lbl.mouseDoubleClickEvent = lambda e, url=u: self._open_url(url)
                ul.addWidget(lbl)
            self._preview_layout.addWidget(url_box)
        if not is_pure_url:
            txt = QTextEdit()
            txt.setReadOnly(True)
            shown = content[:20000]
            txt.setPlainText(shown + (tr("preview_truncated") if len(content) > 20000 else ""))
            self._preview_layout.addWidget(txt, 1)
        info_row = QHBoxLayout()
        n_lines = content.count("\n") + 1
        chip1 = QLabel(tr("chip_chars", n=f"{len(content):,}"))
        chip1.setStyleSheet(f"background: {C['ACCENT_DIM']}; color: {C['ACCENT']}; border-radius: 4px; padding: 2px 8px; font-size: 11px;")
        info_row.addWidget(chip1)
        chip2 = QLabel(tr("chip_lines", n=f"{n_lines:,}"))
        chip2.setStyleSheet(f"background: {C['SURFACE3']}; color: {C['TEXT_SEC']}; border-radius: 4px; padding: 2px 8px; font-size: 11px;")
        info_row.addWidget(chip2)
        info_row.addStretch()
        self._preview_layout.addLayout(info_row)

    def _preview_url(self, entry):
        self._preview_gen += 1
        self._cur_image_path = None
        self._cur_text_entry = entry
        self._clear_preview()
        url = entry.get("content", "")
        icon_lbl = QLabel("\U0001f310")
        icon_lbl.setFont(QFont("Segoe UI Emoji", 28))
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_layout.addWidget(icon_lbl)
        url_lbl = QLabel(url)
        url_lbl.setWordWrap(True)
        url_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        url_lbl.setStyleSheet(f"color: {C['ACCENT']}; font-family: Consolas; font-size: 12px;")
        url_lbl.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        url_lbl.mouseDoubleClickEvent = lambda e: self._open_url(url)
        self._preview_layout.addWidget(url_lbl)
        hint = QLabel(tr("preview_dblclick_url"))
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet(f"color: {C['TEXT_MUTED']}; font-size: 11px; padding-top: 12px;")
        self._preview_layout.addWidget(hint)
        self._preview_layout.addStretch()

    def _image_full_path(self, entry):
        base = os.path.dirname(self.store.path)
        return os.path.join(base, entry.get("filename", ""))

    def _preview_image(self, entry):
        self._preview_gen += 1
        gen = self._preview_gen
        self._cur_image_entry = entry
        self._clear_preview()
        img_path = self._image_full_path(entry)
        thumb_path = os.path.join(os.path.dirname(img_path), "thumb_" + os.path.basename(img_path))
        self._preview_img_lbl = QLabel()
        self._preview_img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_img_lbl.setScaledContents(False)
        self._preview_img_lbl.setMinimumSize(100, 80)
        self._preview_img_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._preview_img_lbl.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._preview_img_lbl.mouseDoubleClickEvent = lambda e: self._open_selected()
        # Real-time rescale when label itself resizes (splitter drag / window resize)
        _orig_lbl_resize = self._preview_img_lbl.resizeEvent
        def _lbl_resized(ev, _orig=_orig_lbl_resize):
            _orig(ev)
            self._last_render_key = None
            self._render_preview_image()
        self._preview_img_lbl.resizeEvent = _lbl_resized
        self._preview_layout.addWidget(self._preview_img_lbl, 1)
        if not os.path.exists(img_path):
            self._cur_image_path = None
            self._preview_img_lbl.setText(tr("preview_unavailable"))
            self._preview_img_lbl.setStyleSheet(f"color: {C['TEXT_MUTED']};")
        else:
            self._cur_image_path = img_path
            self._cached_pil = None
            self._cached_path = None
            self._last_render_key = None
            if os.path.exists(thumb_path):
                pm = QPixmap(thumb_path)
                if not pm.isNull():
                    self._preview_img_lbl.setPixmap(pm.scaled(
                        360, 300, Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation))
            if HAS_PIL:
                self._image_loader = ImageLoader(img_path, gen)
                self._image_loader.finished.connect(self._on_image_loaded)
                self._image_loader.start()
        info_row = QHBoxLayout()
        for text, bg, fg in [
            (f" {entry.get('width', '?')} × {entry.get('height', '?')} ", C['ACCENT_DIM'], C['ACCENT']),
            (f" {fmt_image_type(entry.get('original_format', '?'))} ", C['SURFACE3'], C['TEAL']),
            (f" {fmt_size(entry.get('file_size', 0))} ", C['SURFACE3'], C['TEXT_SEC']),
        ]:
            chip = QLabel(text)
            chip.setStyleSheet(f"background: {bg}; color: {fg}; border-radius: 4px; padding: 2px 8px; font-size: 11px;")
            info_row.addWidget(chip)
        info_row.addStretch()
        hint = QLabel(tr("preview_dblclick_viewer"))
        hint.setStyleSheet(f"color: {C['TEXT_MUTED']}; font-size: 10px;")
        info_row.addWidget(hint)
        self._preview_layout.addLayout(info_row)

    def _on_image_loaded(self, gen, path, img):
        if gen != self._preview_gen or path != self._cur_image_path:
            return
        self._cached_pil = img
        self._cached_path = path
        self._last_render_key = None
        # Convert to full-res QPixmap once; subsequent resizes use fast QPixmap.scaled()
        try:
            rgba = img.convert("RGBA")
            data = rgba.tobytes("raw", "RGBA")
            qimg = QImage(data, rgba.width, rgba.height,
                          rgba.width * 4, QImage.Format.Format_RGBA8888).copy()
            self._cached_qpixmap = QPixmap.fromImage(qimg)
        except Exception:
            self._cached_qpixmap = None
        self._render_preview_image()
        # Delayed re-render in case label size wasn't final yet
        QTimer.singleShot(100, self._render_preview_image)

    def _render_preview_image(self):
        if not hasattr(self, '_preview_img_lbl'):
            return
        pm = getattr(self, '_cached_qpixmap', None)
        if pm is None or pm.isNull():
            return
        lbl_w = self._preview_img_lbl.width()
        lbl_h = self._preview_img_lbl.height()
        if lbl_w < 10 or lbl_h < 10:
            lbl_w = max(40, self._preview_inner.width() - 24)
            lbl_h = max(40, self._preview_inner.height() - 70)
        key = (self._cur_image_path, lbl_w, lbl_h)
        if key == self._last_render_key:
            return
        self._last_render_key = key
        scaled = pm.scaled(lbl_w, lbl_h,
                           Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
        self._preview_img_lbl.setPixmap(scaled)

    def _preview_files(self, entry):
        self._preview_gen += 1
        self._cur_image_path = None
        self._clear_preview()
        paths = entry.get("file_paths", [])
        info_row = QHBoxLayout()
        chip1 = QLabel(tr("chip_files", n=entry.get("file_count", len(paths))))
        chip1.setStyleSheet(f"background: {C['ACCENT_DIM']}; color: {C['ACCENT']}; border-radius: 4px; padding: 2px 8px; font-size: 11px;")
        info_row.addWidget(chip1)
        chip2 = QLabel(f" {_extract_extensions(paths)} ")
        chip2.setStyleSheet(f"background: {C['SURFACE3']}; color: {C['TEAL']}; border-radius: 4px; padding: 2px 8px; font-size: 11px;")
        info_row.addWidget(chip2)
        info_row.addStretch()
        self._preview_layout.addLayout(info_row)
        lw = QListWidget()
        for fp in paths:
            lw.addItem(" " + fp)
        lw.doubleClicked.connect(lambda idx, w=lw: self._open_path_from_list(w, idx))
        self._preview_layout.addWidget(lw, 1)
        hint = QLabel(tr("preview_dblclick_open"))
        hint.setStyleSheet(f"color: {C['TEXT_MUTED']}; font-size: 10px;")
        hint.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._preview_layout.addWidget(hint)

    def _open_path_from_list(self, lw, idx):
        path = lw.item(idx.row()).text().strip()
        if os.path.exists(path):
            _open_path(path)
        else:
            QMessageBox.information(self, tr("dlg_info"), tr("msg_file_not_found", path=path))

    def _open_url(self, url):
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        webbrowser.open(url)

    # ------------------------------------------------------------------
    # Selection helpers
    # ------------------------------------------------------------------
    def _get_selected_hashes(self):
        table = self._tables.get(self._active_type)
        if not table:
            return []
        iid_map = self._iid_to_hash[self._active_type]
        rows = table.selectionModel().selectedRows()
        return [iid_map[r.row()] for r in rows if r.row() in iid_map]

    def _get_selected_entry(self):
        hashes = self._get_selected_hashes()
        if hashes:
            return self._entry_index.get(hashes[0])
        table = self._tables.get(self._active_type)
        if table and table.rowCount() > 0:
            h = self._iid_to_hash[self._active_type].get(0)
            return self._entry_index.get(h) if h else None
        return None

    # ------------------------------------------------------------------
    # Actions: copy / open / pin / delete / export
    # ------------------------------------------------------------------
    def copy_entry_to_clipboard(self, entry):
        """Copy a history entry back to the clipboard (used by desktop widget).

        Marked as self-copy so the monitor won't re-capture it. Raises on failure.
        """
        etype = entry.get("type", "text")
        self._last_self_copy = time.time()
        self.store.mark_self_copy()
        if etype in ("text", "url"):
            set_clipboard_text(entry.get("content", ""))
        elif etype == "image":
            img_path = self._image_full_path(entry)
            if os.path.exists(img_path) and HAS_PIL:
                from PIL import Image as PILImage
                img = PILImage.open(img_path)
                img.load()
                set_clipboard_image(img)
        else:
            paths = [p for p in entry.get("file_paths", []) if os.path.exists(p)]
            if paths:
                set_clipboard_files(paths)

    def _copy_selected(self):
        entry = self._get_selected_entry()
        if not entry:
            self._set_status(tr("st_nothing_to_copy"), "warn")
            return
        etype = entry.get("type", "text")
        self._last_self_copy = time.time()
        self.store.mark_self_copy()
        try:
            if etype in ("text", "url"):
                set_clipboard_text(entry["content"])
                self._set_status(tr("st_copied_chars", n=f"{len(entry['content']):,}"), "ok")
            elif etype == "image":
                img_path = self._image_full_path(entry)
                if os.path.exists(img_path) and HAS_PIL:
                    from PIL import Image as PILImage
                    img = PILImage.open(img_path)
                    img.load()
                    set_clipboard_image(img)
                    self._set_status(tr("st_image_copied"), "ok")
                else:
                    self._set_status(tr("st_image_missing"), "err")
                    return
            else:
                paths = [p for p in entry.get("file_paths", []) if os.path.exists(p)]
                if paths:
                    set_clipboard_files(paths)
                    self._set_status(tr("st_files_copied", n=len(paths)), "ok")
                else:
                    self._set_status(tr("st_paths_missing"), "err")
                    return
            self._update_desk_widget(entry)
            self._flash_selected()
        except Exception as ex:
            QMessageBox.critical(self, tr("dlg_error"), tr("msg_copy_failed", err=ex))

    def _flash_selected(self):
        table = self._tables.get(self._active_type)
        if not table:
            return
        rows = [r.row() for r in table.selectionModel().selectedRows()]
        flash_color = QColor(C['ACCENT_DIM'])
        for row in rows:
            for col in range(table.columnCount()):
                item = table.item(row, col)
                if item:
                    item.setBackground(flash_color)
        QTimer.singleShot(500, lambda: self._refresh_tab(self._active_type))

    def _open_selected(self):
        entry = self._get_selected_entry()
        if not entry:
            return
        etype = entry.get("type", "text")
        try:
            if etype == "image":
                img_path = self._image_full_path(entry)
                if os.path.exists(img_path):
                    _open_path(img_path)
                    self._set_status(tr("st_opened_viewer"), "ok")
                else:
                    self._set_status(tr("st_image_missing"), "err")
            elif etype == "file":
                paths = [p for p in entry.get("file_paths", []) if os.path.exists(p)]
                if not paths:
                    self._set_status(tr("st_path_missing"), "err")
                    return
                if len(paths) == 1:
                    _open_path(paths[0])
                    self._set_status(tr("st_opened_file"), "ok")
                else:
                    self._reveal_in_explorer(paths[0])
                    self._set_status(tr("st_revealed", n=len(paths)), "ok")
            elif etype == "url":
                self._open_url(entry.get("content", ""))
                self._set_status(tr("st_opened_url"), "ok")
            else:
                self._copy_selected()
        except Exception as ex:
            QMessageBox.critical(self, tr("dlg_error"), tr("msg_open_failed", err=ex))

    @staticmethod
    def _reveal_in_explorer(path):
        if IS_WIN:
            subprocess.Popen(f'explorer /select,"{os.path.abspath(path)}"')
        elif IS_MAC:
            subprocess.Popen(["open", "-R", os.path.abspath(path)])
        else:
            subprocess.Popen(["xdg-open", os.path.abspath(path)])

    def _pin_selected(self):
        hashes = self._get_selected_hashes()
        if not hashes:
            return
        to_pin = [h for h in hashes if h not in self._pinned_hashes]
        if not to_pin:
            self._set_status(tr("st_already_pinned"))
            return
        self.store.save_snapshot(tr("snap_pin", n=len(to_pin), t=self._type_label(self._active_type)))
        n = self.store.pin_many(to_pin)
        self._after_mutate(tr("st_pinned", n=n))
        self.lightbar.surge(42.0, 0.9)

    def _unpin_selected(self):
        hashes = self._get_selected_hashes()
        if not hashes:
            return
        to_unpin = [h for h in hashes if h in self._pinned_hashes]
        if not to_unpin:
            self._set_status(tr("st_not_pinned"))
            return
        self.store.save_snapshot(tr("snap_unpin", n=len(to_unpin), t=self._type_label(self._active_type)))
        n = self.store.unpin_many(to_unpin)
        self._after_mutate(tr("st_unpinned", n=n))

    def _toggle_pin_selected(self):
        hashes = self._get_selected_hashes()
        if not hashes:
            return
        pinned = unpinned = 0
        self.store.save_snapshot(tr("snap_toggle_pin", t=self._type_label(self._active_type)))
        for h in hashes:
            if self.store.toggle_pin(h):
                pinned += 1
            else:
                unpinned += 1
        self._after_mutate(tr("st_pin_toggled", a=pinned, b=unpinned))
        self.lightbar.surge(42.0, 0.9)

    def _delete_selected(self):
        hashes = self._get_selected_hashes()
        if not hashes:
            return
        ret = QMessageBox.question(self, tr("dlg_confirm_delete"),
            tr("msg_delete_confirm", n=len(hashes)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ret != QMessageBox.StandardButton.Yes:
            return
        self.store.save_snapshot(tr("snap_delete", n=len(hashes), t=self._type_label(self._active_type)))
        self.store.delete_many(hashes)
        self._after_mutate(tr("st_deleted", n=len(hashes)))
        self.lightbar.surge(4.0, 0.95)

    def _after_mutate(self, status_msg):
        self._refresh_tab(self._active_type)
        self._update_preview()
        self._refresh_history_list(animate=True)
        self._set_status(status_msg, "ok")

    # ---- Clear operations ----
    def _clear_type(self):
        etype, label = self._active_type, self._type_label(self._active_type)
        count = self.store.count(etype)
        if count == 0:
            self._set_status(tr("st_no_type_records", t=label))
            return
        ret = QMessageBox.question(self, tr("dlg_confirm_clear"),
            tr("msg_clear_type", t=label, n=count),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ret != QMessageBox.StandardButton.Yes:
            return
        self.store.save_snapshot(tr("snap_clear_type", t=label, n=count))
        self.store.clear_type(etype)
        self._after_mutate(tr("st_cleared_type", t=label))

    def _clear_type_unpinned(self):
        etype, label = self._active_type, self._type_label(self._active_type)
        unpinned = self.store.unpinned_count(etype)
        if unpinned == 0:
            self._set_status(tr("st_no_unpinned_type", t=label))
            return
        ret = QMessageBox.question(self, tr("dlg_confirm_remove"),
            tr("msg_clear_type_unpinned", t=label, n=unpinned),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ret != QMessageBox.StandardButton.Yes:
            return
        self.store.save_snapshot(tr("snap_clear_type_unpinned", t=label, n=unpinned))
        self.store.clear_type_unpinned(etype)
        self._after_mutate(tr("st_cleared_unpinned_type", t=label))

    def _clear_unpinned(self):
        unpinned = self.store.count() - self.store.pinned_count()
        if unpinned == 0:
            self._set_status(tr("st_no_unpinned"))
            return
        ret = QMessageBox.question(self, tr("dlg_confirm_remove"),
            tr("msg_clear_unpinned", n=unpinned),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ret != QMessageBox.StandardButton.Yes:
            return
        self.store.save_snapshot(tr("snap_clear_unpinned", n=unpinned))
        self.store.clear_unpinned()
        self._refresh_all()
        self._set_status(tr("st_cleared_unpinned"), "ok")

    def _clear_all(self):
        total = self.store.count()
        if total == 0:
            self._set_status(tr("st_nothing_to_clear"))
            return
        ret = QMessageBox.question(self, tr("dlg_confirm_clear"),
            tr("msg_clear_all", n=total),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ret != QMessageBox.StandardButton.Yes:
            return
        self.store.save_snapshot(tr("snap_clear_all", n=total))
        self.store.clear()
        self._refresh_all()
        self._set_status(tr("st_cleared_all"), "ok")

    # ---- Export / copy paths ----
    def _export_selected(self):
        entry = self._get_selected_entry()
        if not entry:
            self._set_status(tr("st_nothing_to_export"), "warn")
            return
        etype = entry.get("type", "text")
        if etype == "text":
            path, _ = QFileDialog.getSaveFileName(
                self, tr("btn_export"), "", f"{tr('ft_text')} (*.txt);;{tr('ft_all')} (*.*)")
            if path:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(entry["content"])
                self._set_status(tr("st_exported", name=os.path.basename(path)), "ok")
        elif etype == "image":
            img_path = self._image_full_path(entry)
            if not os.path.exists(img_path):
                self._set_status(tr("st_image_missing"), "err")
                return
            out, _ = QFileDialog.getSaveFileName(
                self, tr("btn_export"), "", "PNG (*.png);;JPEG (*.jpg);;All (*.*)")
            if out:
                shutil.copy2(img_path, out)
                self._set_status(tr("st_exported", name=os.path.basename(out)), "ok")
        else:
            self._set_status(tr("st_export_files_hint"), "warn")

    def _copy_image_path(self, entry):
        img_path = self._image_full_path(entry)
        self._last_self_copy = time.time()
        self.store.mark_self_copy()
        set_clipboard_text(img_path)
        self._set_status(tr("st_copied_image_path"), "ok")

    def _copy_file_paths(self, entry):
        self._last_self_copy = time.time()
        self.store.mark_self_copy()
        set_clipboard_text("\n".join(entry.get("file_paths", [])))
        self._set_status(tr("st_copied_paths"), "ok")

    # ------------------------------------------------------------------
    # Right-click context menu
    # ------------------------------------------------------------------
    def _on_right_click(self, etype, pos):
        table = self._tables.get(etype)
        if not table:
            return
        idx = table.indexAt(pos)
        if idx.isValid():
            if not table.selectionModel().isSelected(idx):
                table.selectRow(idx.row())
        entry = self._get_selected_entry()
        if not entry:
            return
        n = len(table.selectionModel().selectedRows())
        menu = QMenu(self)
        if etype == "text":
            menu.addAction(tr("m_copy_content"), self._copy_selected)
            menu.addAction(tr("m_export_txt"), self._export_selected)
        elif etype == "url":
            menu.addAction(tr("m_copy_content"), self._copy_selected)
            menu.addAction(tr("m_open_url"), self._open_selected)
        elif etype == "image":
            menu.addAction(tr("m_copy_image"), self._copy_selected)
            menu.addAction(tr("m_open_viewer"), self._open_selected)
            menu.addAction(tr("m_open_folder"),
                           lambda: self._reveal_in_explorer(self._image_full_path(entry)))
            menu.addAction(tr("m_copy_path"), lambda: self._copy_image_path(entry))
            menu.addAction(tr("m_export_image"), self._export_selected)
        else:
            menu.addAction(tr("m_copy_files"), self._copy_selected)
            menu.addAction(tr("m_open_locate"), self._open_selected)
            first = next((p for p in entry.get("file_paths", []) if os.path.exists(p)), None)
            if first:
                menu.addAction(tr("m_open_folder"), lambda p=first: self._reveal_in_explorer(p))
            menu.addAction(tr("m_copy_paths"), lambda: self._copy_file_paths(entry))
        menu.addSeparator()
        menu.addAction(tr("m_toggle_pin"), self._toggle_pin_selected)
        menu.addAction(tr("m_delete_n", n=n) if n > 1 else tr("m_delete"), self._delete_selected)
        menu.exec(table.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------------
    # Manage menu (header)
    # ------------------------------------------------------------------
    def _show_manage_menu(self):
        menu = QMenu(self)
        menu.addAction(tr("m_refresh"), self._refresh_all)
        menu.addSeparator()
        menu.addAction(tr("m_clear_type", t=self._type_label(self._active_type)), self._clear_type)
        menu.addAction(tr("m_clear_type_unpinned", t=self._type_label(self._active_type)),
                       self._clear_type_unpinned)
        menu.addAction(tr("m_clear_unpinned"), self._clear_unpinned)
        menu.addSeparator()
        menu.addAction(tr("m_clear_all"), self._clear_all)
        btn = self._manage_btn
        menu.exec(btn.mapToGlobal(QPoint(0, btn.height())))

    # ------------------------------------------------------------------
    # Monitor integration
    # ------------------------------------------------------------------
    def _poll_monitor(self):
        changed = False
        if self.monitor and self.monitor.is_alive():
            # 正常路径：只消费监控线程的变化信号（轻量事件检查，不碰剪贴板）
            if self.monitor.consume_change():
                changed = True
        else:
            # Fallback: direct clipboard text comparison (only when monitor thread died)
            try:
                import pyperclip
                txt = pyperclip.paste()
                if txt and txt.strip():
                    if not hasattr(self, '_last_poll_text'):
                        self._last_poll_text = txt
                    elif txt != self._last_poll_text:
                        self._last_poll_text = txt
                        if not self.store.is_self_copy():
                            # Directly add to store
                            from youboard_core import URL_PATTERN
                            urls = URL_PATTERN.findall(txt)
                            stripped = URL_PATTERN.sub('', txt).strip()
                            if urls and not stripped:
                                for u in urls:
                                    self.store.add_url(u)
                            else:
                                self.store.add_text(txt)
                                if urls:
                                    for u in urls:
                                        self.store.add_url(u)
                            changed = True
            except Exception:
                pass
        if changed:
            self._on_clip_changed()

    def _on_clip_changed(self):
        self._refresh_tab(self._active_type)
        for etype in ("text", "image", "file", "url"):
            if etype != self._active_type:
                self._update_tab_badge(etype)
        self._update_header_stats()
        self._update_desk_widget()
        try:
            self.lightbar.pulse(190.0, 0.0, strength=1.0)
        except Exception:
            pass
        if time.time() - self._last_self_copy > 1.2:
            self._set_status(tr("st_captured"), "ok")

    # ------------------------------------------------------------------
    # Header breathing dot
    # ------------------------------------------------------------------
    def _animate_dot(self):
        if self.monitor:
            frames = [C['SUCCESS'], "#37b87b", "#2b9a67", "#37b878"]
            self._dot_phase = (self._dot_phase + 1) % len(frames)
            color = frames[self._dot_phase]
            self._monitor_lbl.setText(tr("monitor_live"))
            self._monitor_lbl.setStyleSheet(f"color: {C['SUCCESS']}; font-size: 11px; background: transparent;")
        else:
            color = C['TEXT_MUTED']
            self._monitor_lbl.setText(tr("monitor_off"))
        self._dot_lbl.setStyleSheet(f"background: {color}; border-radius: 4px;")
        QTimer.singleShot(700, self._animate_dot)

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------
    def _open_settings(self):
        dlg = SettingsDialog(self)
        dlg.exec()
        # 设置窗口关闭后静默刷新：保证期间复制的新内容立即显示（不弹状态提示）
        try:
            for etype in ("text", "image", "file", "url"):
                self._refresh_tab(etype)
            self._refresh_history_list()
            self._update_desk_widget()
        except Exception:
            pass

    def _open_phone_transfer(self):
        dlg = PhoneTransferDialog(self)
        dlg.exec()

    def apply_settings(self, lang, autostart, theme="dark", bg_changed=False):
        if autostart != get_autostart():
            if set_autostart(autostart):
                self._set_status(tr("st_autostart_on") if autostart else tr("st_autostart_off"), "ok")
            else:
                self._set_status(tr("st_autostart_failed"), "err")
        cfg = load_config()
        need_restart = bg_changed
        if cfg.get("language", "zh") != lang:
            cfg["language"] = lang
            need_restart = True
        if cfg.get("theme", "dark") != theme:
            cfg["theme"] = theme
            need_restart = True
        # Re-register hotkey if it changed (takes effect immediately)
        self._unregister_hotkey()
        self._register_hotkey()
        # 重新绑定动作快捷键（设置中修改后立即生效）
        self._bind_shortcuts()
        # Optional desktop widget — apply live
        self._apply_desktop_widget()
        if need_restart:
            # Save window state so it persists across restart
            geo = self.normalGeometry() if self.isMaximized() else self.geometry()
            cfg["win_geometry"] = [geo.x(), geo.y(), geo.width(), geo.height()]
            cfg["win_maximized"] = self.isMaximized()
            save_config(cfg)
            self.restart_flag = True
            self._restarting = True
            self.close()

    def _apply_desktop_widget(self):
        """Create/show or hide the optional desktop clipboard widget."""
        enabled = bool(load_config().get("desktop_widget", True))
        if enabled:
            if self._desk_widget is None:
                self._desk_widget = DesktopClipboardWidget(self)
            self._desk_widget.refresh()
            self._desk_widget.show()
            self._desk_widget.raise_()
        elif self._desk_widget is not None:
            self._desk_widget.hide()

    def _update_desk_widget(self, entry=None):
        w = getattr(self, "_desk_widget", None)
        if w is not None and w.isVisible():
            w.refresh(entry)

    # ------------------------------------------------------------------
    # Cleanup / tray / fade-in
    # ------------------------------------------------------------------
    def _apply_max_state(self):
        """最大化时内容贴合屏幕左缘（左边距归零 + 直角面板），消除圆角缺口。"""
        maxed = self.isMaximized()
        if getattr(self, "_flush", None) == maxed:
            return
        self._flush = maxed
        for lay in getattr(self, "_tab_layouts", []):
            lay.setContentsMargins(0 if maxed else 8, 8, 8, 8)
        try:
            self.setStyleSheet(build_qss(load_config().get("theme", "dark"), flush=maxed))
        except Exception:
            pass

    def closeEvent(self, event):
        """X = quit the app."""
        self._save_window_geometry()
        self._end_session()  # 临时会话：退出即清空本次记录
        self._unregister_hotkey()
        if hasattr(self, '_desk_widget') and self._desk_widget:
            self._desk_widget.save_geometry()
            self._desk_widget.close()
        if self.monitor:
            self.monitor.stop()
        srv = getattr(self, "_phone_server", None)
        if srv is not None:
            srv.stop()
        if hasattr(self, '_tray') and self._tray:
            self._tray.hide()
        event.accept()

    def _real_quit(self):
        self._save_window_geometry()
        self._end_session()  # 临时会话：退出即清空本次记录
        if hasattr(self, '_unregister_hotkey'):
            self._unregister_hotkey()
        if hasattr(self, '_desk_widget') and self._desk_widget:
            self._desk_widget.save_geometry()
        if self.monitor:
            self.monitor.stop()
        srv = getattr(self, "_phone_server", None)
        if srv is not None:
            srv.stop()
        if hasattr(self, '_tray') and self._tray:
            self._tray.hide()
        QApplication.quit()

    def _save_window_geometry(self):
        """退出/重启前保存主窗口位置、大小与最大化状态，保证下次启动原样恢复。"""
        try:
            cfg = load_config()
            geo = self.normalGeometry() if self.isMaximized() else self.geometry()
            cfg["win_geometry"] = [geo.x(), geo.y(), geo.width(), geo.height()]
            cfg["win_maximized"] = self.isMaximized()
            save_config(cfg)
        except Exception:
            pass

    def _init_tray(self):
        """创建并显示系统托盘图标（幂等）。启动早期调用，保证图标及时出现。"""
        if getattr(self, "_tray", None) is not None:
            return
        self._tray = QSystemTrayIcon(_build_tray_icon(), self)
        tray_menu = QMenu()
        show_act = QAction(tr("tray_show"), self)
        show_act.triggered.connect(self._tray_show)
        self._tray_session_act = QAction(tr("tray_session"), self)
        self._tray_session_act.setCheckable(True)
        self._tray_session_act.setChecked(
            bool(load_config().get("temporary_session", False)
                 or load_config().get("privacy_mode", False)))
        self._tray_session_act.triggered.connect(self._toggle_session)
        phone_act = QAction(tr("tray_phone"), self)
        phone_act.triggered.connect(self._open_phone_transfer)
        self._phone_stop_act = QAction(tr("tray_phone_stop", port=0), self)
        self._phone_stop_act.setVisible(False)
        self._phone_stop_act.triggered.connect(self._stop_phone_transfer)
        quit_act = QAction(tr("tray_quit"), self)
        quit_act.triggered.connect(self._tray_quit)
        tray_menu.addAction(show_act)
        tray_menu.addAction(self._tray_session_act)
        tray_menu.addAction(phone_act)
        tray_menu.addAction(self._phone_stop_act)
        tray_menu.addSeparator()
        tray_menu.addAction(quit_act)
        self._tray.setContextMenu(tray_menu)
        self._tray.setToolTip("YouBoard v" + APP_VERSION)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()
        # 个别系统托盘区首显可能是空白占位，稍后重设一次图标
        QTimer.singleShot(1500, self._refresh_tray_icon)
        # Win11：自提升托盘图标到可见区并更新系统识别的快照图标
        QTimer.singleShot(2500, self._promote_tray_icon_registry)
        QTimer.singleShot(3200, self._refresh_tray_icon)
        self._refresh_phone_tray()

    def _refresh_phone_tray(self):
        """同步托盘「停止手机传输」入口与当前服务状态。"""
        if not hasattr(self, "_phone_stop_act"):
            return
        srv = getattr(self, "_phone_server", None)
        running = bool(srv is not None and srv.running)
        self._phone_stop_act.setVisible(running)
        if running:
            self._phone_stop_act.setText(tr("tray_phone_stop", port=srv.port))

    def _stop_phone_transfer(self):
        """停止手机传输服务并刷新托盘状态。"""
        srv = getattr(self, "_phone_server", None)
        if srv is not None:
            srv.stop()
        self._refresh_phone_tray()
        if getattr(self, "_tray", None) is not None:
            try:
                self._tray.showMessage("YouBoard", tr("phone_stopped"),
                                       QSystemTrayIcon.MessageIcon.Information,
                                       2500)
            except Exception:
                pass

    def _refresh_tray_icon(self):
        if getattr(self, "_tray", None) is not None:
            self._tray.setIcon(_build_tray_icon())

    # ---- 临时会话（合并原隐私模式：暂停记录 + 退出即清空本次记录）----

    def _collect_session_baseline(self):
        """记录当前历史 / 快照 / 图片 / 文件缓存的基线，用于区分"本次新增"。"""
        base = {"hashes": set(), "snaps": set(), "images": set(), "cache": set()}
        try:
            for e in self.store.get_all():
                base["hashes"].add(e["hash"])
        except Exception:
            pass
        try:
            base["snaps"] = {s.get("id") for s in self.store.get_snapshots()}
        except Exception:
            pass
        try:
            if os.path.isdir(IMAGES_DIR):
                base["images"] = set(os.listdir(IMAGES_DIR))
        except Exception:
            pass
        try:
            if os.path.isdir(FILE_CACHE_DIR):
                for root, _dirs, files in os.walk(FILE_CACHE_DIR):
                    for f in files:
                        base["cache"].add(os.path.relpath(
                            os.path.join(root, f), FILE_CACHE_DIR))
        except Exception:
            pass
        return base

    def _start_session(self):
        self._session_active = True
        self._session_baseline = self._collect_session_baseline()

    def _end_session(self):
        """结束临时会话：清空本次运行产生的记录（退出应用 / 关闭开关时调用）。"""
        if not getattr(self, "_session_active", False):
            return
        try:
            self._clear_session_records()
        except Exception:
            pass
        self._session_active = False
        self._session_baseline = None

    def _clear_session_records(self):
        base = self._session_baseline
        if not base:
            return
        # 历史条目
        to_delete = []
        try:
            for e in self.store.get_all():
                if e["hash"] not in base["hashes"]:
                    to_delete.append(e["hash"])
            if to_delete:
                self.store.delete_many(to_delete)
        except Exception:
            pass
        # 快照
        try:
            keep = {s.get("id") for s in self.store.get_snapshots()
                    if s.get("id") in base["snaps"]}
            self.store.prune_snapshots(keep)
        except Exception:
            pass
        # 图片缓存
        try:
            if os.path.isdir(IMAGES_DIR):
                for name in os.listdir(IMAGES_DIR):
                    if name not in base["images"]:
                        try:
                            os.remove(os.path.join(IMAGES_DIR, name))
                        except OSError:
                            pass
        except Exception:
            pass
        # 压缩包物化文件缓存
        try:
            if os.path.isdir(FILE_CACHE_DIR):
                for root, dirs, files in os.walk(FILE_CACHE_DIR, topdown=False):
                    for f in files:
                        rel = os.path.relpath(os.path.join(root, f), FILE_CACHE_DIR)
                        if rel not in base["cache"]:
                            try:
                                os.remove(os.path.join(root, f))
                            except OSError:
                                pass
                    for d in dirs:
                        try:
                            os.rmdir(os.path.join(root, d))
                        except OSError:
                            pass
        except Exception:
            pass

    def set_temporary_session(self, on, save=False):
        """开关临时会话：开启=正常记录并标记会话；关闭=清空本次记录。"""
        on = bool(on)
        if on:
            self._start_session()
        else:
            self._end_session()
        if save:
            try:
                cfg = load_config()
                cfg["temporary_session"] = on
                save_config(cfg)
            except Exception:
                pass
        if getattr(self, "_tray_session_act", None) is not None:
            self._tray_session_act.setChecked(on)
        if getattr(self, "_tray", None) is not None:
            try:
                self._tray.showMessage(
                    "YouBoard", tr("session_started") if on else tr("session_cleared"),
                    QSystemTrayIcon.MessageIcon.Information, 2500)
            except Exception:
                pass

    def _toggle_session(self, on):
        self.set_temporary_session(on, save=True)

    def _promote_tray_icon_registry(self):
        """Win11 默认把新托盘图标放进溢出区。扫描注册表找到当前 EXE 的
        通知区条目，写 isPromoted=1 使其显示在可见托盘区；同时把
        IconSnapshot 更新为当前图标的 16x16 PNG，保证系统识别的图标正确。"""
        try:
            import winreg
            exe = os.path.normcase(os.path.abspath(
                sys.executable if getattr(sys, "frozen", False) else __file__))
            snap = b""
            try:
                from PyQt6.QtCore import QBuffer, QIODevice
                pm = _build_tray_icon().pixmap(16, 16)
                buf = QBuffer()
                buf.open(QIODevice.OpenModeFlag.WriteOnly)
                pm.save(buf, "PNG")
                buf.close()
                snap = bytes(buf.data())
            except Exception:
                snap = b""
            base = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                  r"Control Panel\NotifyIconSettings",
                                  0, winreg.KEY_READ)
            i = 0
            while True:
                try:
                    name = winreg.EnumKey(base, i)
                except OSError:
                    break
                i += 1
                try:
                    sub = winreg.OpenKey(base, name, 0,
                                         winreg.KEY_READ | winreg.KEY_WRITE)
                except OSError:
                    continue
                try:
                    val, _ = winreg.QueryValueEx(sub, "ExecutablePath")
                except OSError:
                    val = None
                if val and os.path.normcase(os.path.abspath(str(val))) == exe:
                    winreg.SetValueEx(sub, "isPromoted", 0, winreg.REG_DWORD, 1)
                    if snap:
                        winreg.SetValueEx(sub, "IconSnapshot", 0,
                                          winreg.REG_BINARY, snap)
                winreg.CloseKey(sub)
            winreg.CloseKey(base)
        except Exception:
            pass

    def run(self):
        """Show window with tray icon and fade-in animation."""
        self._ui_ready = True
        self._init_tray()
        # Register global hotkey
        self._register_hotkey()
        # Fade-in animation
        self._fade_in()
        self.show()
        # 开机自启动时任务栏偶发显示默认占位图标：onefile 程序启动阶段
        # 解压/初始化较慢，任务栏可能在图标就绪前就抓取了占位图标；
        # 这里在窗口显示后（立即 + 延迟）重新注册 AppUserModelID 并重设图标，
        # 让 shell 有机会重新解析为正确的 YouBoard 图标。
        QTimer.singleShot(0, self._refresh_taskbar_icon)
        QTimer.singleShot(1500, self._refresh_taskbar_icon)
        # Add native resize borders to frameless window (WS_THICKFRAME)
        try:
            import ctypes
            hwnd = int(self.winId())
            GWL_STYLE = -16
            WS_THICKFRAME = 0x00040000
            WS_MINIMIZEBOX = 0x00020000
            WS_MAXIMIZEBOX = 0x00010000  # ← 加上这行
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_STYLE)
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_STYLE,
                                                style | WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX)
        except Exception:
            pass

    def _refresh_taskbar_icon(self):
        """重新注册进程 AppUserModelID 并重设窗口/应用图标，强制任务栏刷新图标。"""
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                APP_USER_MODEL_ID)
        except Exception:
            pass
        try:
            self.setWindowIcon(QIcon(LOGO_ICO))
            app = QApplication.instance()
            if app is not None:
                app.setWindowIcon(QIcon(LOGO_ICO))
        except Exception:
            pass

    # ---- Global Hotkey ----
    HOTKEY_ID = 0xB0AD
    WM_HOTKEY = 0x0312

    def _register_hotkey(self):
        """Register global hotkey using keyboard library (reliable) or Win32 fallback."""
        cfg = load_config()
        hk = cfg.get("hotkey", "alt+q")
        if IS_MAC:
            self._hk_worker = _MacHotkeyListener(hk, self._on_hotkey_threadsafe)
            self._hk_worker.start()
            self._hotkey_registered = True
            return
        if HAS_KEYBOARD:
            try:
                # keyboard library uses same format: 'win+f8', 'ctrl+alt+q', etc.
                self._hk_hotkey_name = _keyboard_lib.add_hotkey(hk, self._on_hotkey_threadsafe, suppress=True)
                self._hotkey_registered = True
                return
            except Exception:
                pass
        # Fallback: Win32 thread approach
        mods, vk = self._parse_hotkey(hk)
        self._hk_worker = _HotkeyWorker(mods, vk)
        self._hk_worker.start()
        self._hotkey_registered = True
        # Poll the worker's flag
        self._hk_timer = QTimer(self)
        self._hk_timer.timeout.connect(self._poll_hk_flag)
        self._hk_timer.start(50)

    def _on_hotkey_threadsafe(self):
        """Called from keyboard library's thread; marshal to Qt main thread via QTimer."""
        QTimer.singleShot(0, self._on_hotkey)

    def _poll_hk_flag(self):
        if hasattr(self, '_hk_worker') and self._hk_worker.pressed:
            self._hk_worker.pressed = False
            self._on_hotkey()

    def _unregister_hotkey(self):
        if hasattr(self, '_hk_timer'):
            self._hk_timer.stop()
        if hasattr(self, '_hk_worker') and self._hk_worker:
            self._hk_worker.stop()
            self._hk_worker = None
        # Clean up keyboard library hook
        if HAS_KEYBOARD and hasattr(self, '_hk_hotkey_name'):
            try:
                _keyboard_lib.remove_hotkey(self._hk_hotkey_name)
            except Exception:
                pass
            del self._hk_hotkey_name

    @staticmethod
    def _parse_hotkey(hk_str):
        """Parse hotkey string like 'alt+q' into (modifiers, vk_code)."""
        MOD_ALT = 0x0001
        MOD_CTRL = 0x0002
        MOD_SHIFT = 0x0004
        MOD_WIN = 0x0008
        parts = hk_str.lower().replace(" ", "").split("+")
        mods = 0
        vk = 0
        for p in parts:
            if p == "alt":
                mods |= MOD_ALT
            elif p in ("ctrl", "control"):
                mods |= MOD_CTRL
            elif p == "shift":
                mods |= MOD_SHIFT
            elif p in ("win", "super"):
                mods |= MOD_WIN
            elif len(p) == 1 and p.isalpha():
                vk = ord(p.upper())
            elif len(p) == 1 and p.isdigit():
                vk = ord(p)  # '0'=0x30, '1'=0x31, etc.
            elif p.startswith("f") and p[1:].isdigit():
                vk = 0x70 + int(p[1:]) - 1  # F1=0x70
        if vk == 0:
            vk = ord("Q")  # default
            mods = MOD_ALT
        return mods, vk

    def _on_hotkey(self):
        """Toggle window visibility on hotkey press (with debounce)."""
        import time
        now = time.monotonic()
        if hasattr(self, '_hk_last_time') and (now - self._hk_last_time) < 0.4:
            return  # debounce: ignore rapid double-fires
        self._hk_last_time = now
        if self.isVisible() and not self.isMinimized():
            self.hide()
        else:
            self.showNormal()
            self.activateWindow()
            self.raise_()

    def _tray_show(self):
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def _tray_quit(self):
        self._real_quit()
        QApplication.quit()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._tray_show()

    def _fade_in(self):
        # Skip fade-in on frameless windows (QGraphicsOpacityEffect can leave window invisible)
        pass


# ===========================================================================
# Hotkey Capture Widget
# ===========================================================================
_ACTION_HOTKEY_DEFAULTS = {
    "hk_copy": "enter",
    "hk_delete": "delete",
    "hk_pin": "space",
    "hk_next_tab": "tab",
    "hk_prev_tab": "shift+tab",
}

_HK_KEY_ALIAS = {
    "enter": Qt.Key.Key_Return,
    "return": Qt.Key.Key_Return,
    "del": Qt.Key.Key_Delete,
    "delete": Qt.Key.Key_Delete,
    "space": Qt.Key.Key_Space,
    "tab": Qt.Key.Key_Tab,
    "esc": Qt.Key.Key_Escape,
    "escape": Qt.Key.Key_Escape,
    "backspace": Qt.Key.Key_Backspace,
    "home": Qt.Key.Key_Home,
    "end": Qt.Key.Key_End,
    "up": Qt.Key.Key_Up,
    "down": Qt.Key.Key_Down,
    "left": Qt.Key.Key_Left,
    "right": Qt.Key.Key_Right,
}


def _canon_hotkey(hk):
    """统一别名，保证显示与存储一致：del→delete、return→enter。"""
    out = []
    for p in str(hk).lower().replace(" ", "").split("+"):
        if p == "del":
            p = "delete"
        elif p == "return":
            p = "enter"
        out.append(p)
    return "+".join(out)


def _hotkey_to_sequence(hk):
    """把 'ctrl+shift+tab' 这类字符串转成 QKeySequence；解析失败返回 None。"""
    mods = 0
    key = None
    for p in str(hk).lower().replace(" ", "").split("+"):
        if p == "ctrl":
            mods |= int(Qt.KeyboardModifier.ControlModifier.value)
        elif p == "alt":
            mods |= int(Qt.KeyboardModifier.AltModifier.value)
        elif p == "shift":
            mods |= int(Qt.KeyboardModifier.ShiftModifier.value)
        elif p == "win":
            mods |= int(Qt.KeyboardModifier.MetaModifier.value)
        elif p in _HK_KEY_ALIAS:
            key = int(_HK_KEY_ALIAS[p].value)
        elif len(p) == 1 and p.isalpha():
            key = int(getattr(Qt.Key, "Key_" + p.upper()).value)
        elif len(p) == 1 and p.isdigit():
            key = int(getattr(Qt.Key, "Key_" + p).value)
        elif p.startswith("f") and p[1:].isdigit():
            key = int(getattr(Qt.Key, "Key_F" + p[1:]).value)
    if key is None:
        return None
    return QKeySequence(mods | key)


def _hotkey_display(hk):
    return _canon_hotkey(hk).upper().replace("+", " + ")


def _tab_event_hotkey(event):
    """把按键事件转成 'tab' / 'shift+tab' / 'ctrl+tab' 这类规范字符串。"""
    parts = []
    m = event.modifiers()
    if m & Qt.KeyboardModifier.ControlModifier:
        parts.append("ctrl")
    if m & Qt.KeyboardModifier.AltModifier:
        parts.append("alt")
    if m & Qt.KeyboardModifier.ShiftModifier:
        parts.append("shift")
    if m & Qt.KeyboardModifier.MetaModifier:
        parts.append("win")
    parts.append("tab")
    return "+".join(parts)


class _TabHotkeyFilter(QObject):
    """让 Tab / Shift+Tab 等 Tab 组合能作为分类切换快捷键。

    QShortcut 对裸 Tab 不生效（焦点导航会先消费按键），因此用应用级事件
    过滤器在焦点导航之前接管。仅当主窗口处于活动状态、且组合与配置匹配时
    才接管，避免影响设置弹窗等其它窗口里的 Tab 焦点切换。
    """

    def __init__(self, owner):
        super().__init__(owner)
        self._owner = owner

    def eventFilter(self, obj, event):
        if event.type() != QEvent.Type.KeyPress:
            return False
        if event.key() not in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            return False
        if not self._owner.isActiveWindow():
            return False
        cfg = load_config()
        pressed = _tab_event_hotkey(event)
        nxt = _canon_hotkey(cfg.get(
            "hk_next_tab", _ACTION_HOTKEY_DEFAULTS["hk_next_tab"]))
        prv = _canon_hotkey(cfg.get(
            "hk_prev_tab", _ACTION_HOTKEY_DEFAULTS["hk_prev_tab"]))
        if pressed == nxt:
            self._owner._next_tab()
            return True
        if pressed == prv:
            self._owner._prev_tab()
            return True
        return False


class HotkeyCapture(QPushButton):
    """Click to record, then press a key combo to set the global hotkey."""

    def __init__(self, hotkey_str="alt+q", parent=None):
        super().__init__(parent)
        self._hotkey = hotkey_str
        self._recording = False
        self.setFixedWidth(120)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._update_display()

    def _update_display(self):
        if self._recording:
            self.setText("请按下快捷键...")
            self.setStyleSheet(f"background: {C['ACCENT_DIM']}; color: {C['ACCENT']}; "
                               f"border: 2px solid {C['ACCENT']}; border-radius: 6px; padding: 6px;")
        else:
            self.setText(self._hotkey.upper().replace("+", " + "))
            self.setStyleSheet(f"background: {C['SURFACE2']}; color: {C['TEXT']}; "
                               f"border: 1px solid {C['BORDER']}; border-radius: 6px; padding: 6px;")

    def mousePressEvent(self, event):
        self._recording = True
        self._update_display()
        self.setFocus()

    def event(self, e):
        # Tab/Shift+Tab 默认被焦点导航消费，这里先拦下来交给录制逻辑，
        # 让 Tab 也能作为快捷键被录入。
        if (e.type() == QEvent.Type.KeyPress
                and int(e.key()) in (int(Qt.Key.Key_Tab.value),
                                     int(Qt.Key.Key_Backtab.value))):
            self.keyPressEvent(e)
            return True
        return super().event(e)

    def keyPressEvent(self, event):
        if not self._recording:
            super().keyPressEvent(event)
            return
        key = event.key()
        if key in (Qt.Key.Key_Alt, Qt.Key.Key_Control, Qt.Key.Key_Shift,
                   Qt.Key.Key_Meta, Qt.Key.Key_AltGr):
            return
        mods = event.modifiers()
        parts = []
        if mods & Qt.KeyboardModifier.ControlModifier:
            parts.append("ctrl")
        if mods & Qt.KeyboardModifier.AltModifier:
            parts.append("alt")
        if mods & Qt.KeyboardModifier.ShiftModifier:
            parts.append("shift")
        if mods & Qt.KeyboardModifier.MetaModifier:
            parts.append("win")
        # Get key name
        if Qt.Key.Key_F1 <= key <= Qt.Key.Key_F12:
            parts.append(f"f{key - Qt.Key.Key_F1 + 1}")
        elif key == Qt.Key.Key_Escape:
            self._recording = False
            self._update_display()
            return
        elif Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
            parts.append(str(key - Qt.Key.Key_0))
        elif Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
            parts.append(chr(key - Qt.Key.Key_A + ord('a')))
        else:
            # Fallback: use QKeySequence to get readable name
            name = QKeySequence(key).toString().lower()
            if name and len(name) <= 12:
                parts.append(name)
            else:
                txt = event.text().lower()
                if txt and txt.isprintable():
                    parts.append(txt)
                else:
                    self._recording = False
                    self._update_display()
                    return
        self._hotkey = "+".join(parts)
        self._recording = False
        self._conflict = self._check_conflict(self._hotkey)
        self._update_display()

    @staticmethod
    def _check_conflict(hotkey_str):
        """Detect if the combo is already in use (system-level + RegisterHotKey test)."""
        # Known Windows system reserved shortcuts that RegisterHotKey can't detect
        _SYSTEM_RESERVED = {
            "win+l", "win+e", "win+d", "win+i", "win+r", "win+x", "win+a",
            "win+n", "win+s", "win+tab", "win+b", "win+g", "win+h", "win+k",
            "win+m", "win+p", "win+t", "win+u", "win+v", "win+w", "win+.",
            "win+space", "win+shift+s", "win+shift+m", "win+ctrl+d",
            "win+ctrl+f4", "win+ctrl+left", "win+ctrl+right",
            "ctrl+alt+del", "ctrl+shift+esc", "alt+f4", "alt+tab", "alt+esc",
        }
        normalized = hotkey_str.lower().replace(" ", "")
        # Sort parts for consistent comparison
        parts = sorted(normalized.split("+"))
        normalized_sorted = "+".join(parts)
        for reserved in _SYSTEM_RESERVED:
            r_parts = sorted(reserved.split("+"))
            if "+".join(r_parts) == normalized_sorted:
                return True
        # Also try RegisterHotKey to detect app-level conflicts
        try:
            user32 = ctypes.windll.user32
            parts = hotkey_str.lower().split("+")
            mods = 0
            vk = 0
            for p in parts:
                if p == "alt": mods |= 0x0001
                elif p in ("ctrl", "control"): mods |= 0x0002
                elif p == "shift": mods |= 0x0004
                elif p in ("win", "super"): mods |= 0x0008
                elif len(p) == 1 and p.isalpha(): vk = ord(p.upper())
                elif len(p) == 1 and p.isdigit(): vk = ord(p)
                elif p.startswith("f") and p[1:].isdigit(): vk = 0x70 + int(p[1:]) - 1
            if vk == 0:
                return False
            TEST_ID = 0xB0AE
            user32.UnregisterHotKey(None, TEST_ID)
            ok = user32.RegisterHotKey(None, TEST_ID, mods, vk)
            if ok:
                user32.UnregisterHotKey(None, TEST_ID)
                return False
            return True
        except Exception:
            return False

    def _update_display(self):
        if self._recording:
            self.setText("请按下快捷键...")
            self.setStyleSheet(f"background: {C['ACCENT_DIM']}; color: {C['ACCENT']}; "
                               f"border: 2px solid {C['ACCENT']}; border-radius: 6px; padding: 6px;")
        elif getattr(self, '_conflict', False):
            self.setText(self._hotkey.upper().replace("+", " + ") + "  ⚠已占用")
            self.setStyleSheet(f"background: {C['SURFACE2']}; color: {C['DANGER']}; "
                               f"border: 2px solid {C['DANGER']}; border-radius: 6px; padding: 6px;")
        else:
            self.setText(self._hotkey.upper().replace("+", " + "))
            self.setStyleSheet(f"background: {C['SURFACE2']}; color: {C['TEXT']}; "
                               f"border: 1px solid {C['BORDER']}; border-radius: 6px; padding: 6px;")

    def get_hotkey(self):
        return self._hotkey

    def focusOutEvent(self, event):
        if self._recording:
            self._recording = False
            self._update_display()
        super().focusOutEvent(event)


# ===========================================================================
# Settings Dialog
# ===========================================================================
class _HotkeyDialog(QDialog):
    """独立的快捷键设置界面：全局显示/隐藏 + 各类动作快捷键。"""

    def __init__(self, parent, values):
        super().__init__(parent)
        self.setWindowTitle(tr("set_hotkeys_title"))
        self.setModal(True)
        self.setMinimumWidth(440)
        self._values = dict(values)
        self._rows = {}
        self.setStyleSheet(f"""
            QDialog {{ background-color: {C['BG']}; }}
            QLabel {{ background: transparent; color: {C['TEXT']}; }}
            QPushButton {{ background: {C['SURFACE2']}; color: {C['TEXT_SEC']};
                border: 1px solid {C['BORDER']}; border-radius: 6px;
                padding: 6px 14px; font-size: 12px; }}
            QPushButton:hover {{ background: {C['SURFACE3']}; color: {C['TEXT']}; }}
            QPushButton[cssClass="accent"] {{ background: {C['ACCENT']};
                color: #fff; border: none; font-weight: bold; }}
            QPushButton[cssClass="accent"]:hover {{ background: {C['ACCENT_HV']}; }}
        """)

        lay = QVBoxLayout(self)
        self._add_row(lay, "hotkey", tr("set_hotkey_title"))
        lay.addWidget(self._make_sep())
        for key in ("hk_copy", "hk_delete", "hk_pin",
                    "hk_next_tab", "hk_prev_tab"):
            self._add_row(lay, key, tr(key))
        lay.addStretch()

        btns = QHBoxLayout()
        btns.addStretch()
        cancel = QPushButton(tr("btn_cancel"))
        cancel.clicked.connect(self.reject)
        btns.addWidget(cancel)
        ok = QPushButton(tr("btn_ok"))
        ok.setProperty("cssClass", "accent")
        ok.clicked.connect(self.accept)
        btns.addWidget(ok)
        lay.addLayout(btns)

    def _make_sep(self):
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFixedHeight(1)
        line.setStyleSheet(f"background: {C['BORDER']};")
        return line

    def _add_row(self, lay, key, title):
        row = QHBoxLayout()
        lbl = QLabel(title)
        lbl.setStyleSheet(f"color: {C['TEXT']}; font-weight: bold;")
        row.addWidget(lbl, 1)
        cur = QLabel(_hotkey_display(self._values[key]))
        cur.setStyleSheet(f"color: {C['TEXT_SEC']}; font-size: 12px;")
        row.addWidget(cur)
        chg = QPushButton(tr("hk_change"))
        chg.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        chg.clicked.connect(lambda _, k=key, c=cur: self._change(k, c))
        row.addWidget(chg)
        lay.addLayout(row)
        self._rows[key] = cur

    def _change(self, key, cur_lbl):
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("hk_dialog_title"))
        dlg.setModal(True)
        dlg.setMinimumWidth(340)
        l = QVBoxLayout(dlg)
        hint = QLabel(tr("hk_dialog_hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {C['TEXT_SEC']}; font-size: 12px;")
        l.addWidget(hint)
        cap = HotkeyCapture(self._values.get(key, "alt+q"))
        cap.setFixedWidth(220)
        l.addWidget(cap, 0, Qt.AlignmentFlag.AlignHCenter)
        b = QHBoxLayout()
        b.addStretch()
        c2 = QPushButton(tr("btn_cancel"))
        c2.clicked.connect(dlg.reject)
        b.addWidget(c2)
        ok = QPushButton(tr("btn_ok"))
        ok.setProperty("cssClass", "accent")
        ok.clicked.connect(dlg.accept)
        b.addWidget(ok)
        l.addLayout(b)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            hk = _canon_hotkey(cap.get_hotkey() or self._values.get(key, "alt+q"))
            self._values[key] = hk
            cur_lbl.setText(_hotkey_display(hk))

    def values(self):
        return self._values


def _pil_to_qimage(pil_img):
    """PIL → QImage。QImage 可在后台线程创建；copy 保证像素数据独立。"""
    img = pil_img.convert("RGB")
    data = img.tobytes()
    qimg = QImage(data, img.width, img.height,
                  img.width * 3, QImage.Format.Format_RGB888)
    return qimg.copy()


class PhoneQRWorker(QThread):
    """后台收集局域网 IP 并生成二维码，避免阻塞界面。"""

    sig_ips = pyqtSignal(list)
    sig_qr = pyqtSignal(str, object)
    sig_error = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._jobs = queue.Queue()
        self._alive = True

    def request(self, url):
        self._jobs.put(url)

    def stop(self):
        self._alive = False
        try:
            self._jobs.get_nowait()
        except queue.Empty:
            pass

    def run(self):
        try:
            ips = get_lan_ips()
        except Exception:
            ips = ["127.0.0.1"]
        self.sig_ips.emit(ips)
        while self._alive:
            try:
                url = self._jobs.get(timeout=0.4)
            except queue.Empty:
                continue
            if not self._alive:
                break
            try:
                pil = make_qr_pil(url)
            except Exception:
                pil = None
            if pil is None:
                self.sig_error.emit(tr("phone_no_qr"))
            else:
                self.sig_qr.emit(url, _pil_to_qimage(pil))


class SyncWorker(QThread):
    """云同步后台线程：加密打包 + 上传 / 下载解密 + 合并，不阻塞界面。"""

    sig_done = pyqtSignal(bool, str)

    def __init__(self, action, client, passphrase, store, parent=None):
        super().__init__(parent)
        self.action = action          # "upload" | "download"
        self.client = client
        self.passphrase = passphrase
        self.store = store
        self.result_gid = None

    def run(self):
        try:
            if self.action == "upload":
                cats, snaps = self.store.export_history()
                payload = {
                    "version": 1,
                    "ts": datetime.now().isoformat(),
                    "categories": cats,
                    "snapshots": snaps,
                }
                blob = encrypt_bundle(payload, self.passphrase)
                gid = self.client.upload(blob)
                if isinstance(self.client, GistSyncClient):
                    self.result_gid = gid or None
                self.sig_done.emit(True, tr("sync_uploaded"))
            else:
                blob = self.client.download()
                payload = decrypt_bundle(blob, self.passphrase)
                self.store.merge_history(payload.get("categories") or {},
                                         payload.get("snapshots") or [])
                self.sig_done.emit(True, tr("sync_downloaded"))
        except SyncError as e:
            self.sig_done.emit(False, str(e))
        except Exception as e:
            self.sig_done.emit(False, str(e))


class PhoneTransferDialog(QDialog):
    """手机传输窗口：二维码 + 链接 + 状态；关闭即停止服务。

    服务启动、IP 检测、二维码生成全部在后台线程完成，打开窗口不卡顿。
    """

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self._inbox = queue.Queue()
        self._ips = []
        self._current_ip = None
        self._qr_url = None
        self._fw_done = False
        self._server_was_running = False
        self.setWindowTitle(tr("phone_title"))
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)
        self.setFixedSize(420, 740)
        if LOGO_ICO and os.path.exists(LOGO_ICO):
            self.setWindowIcon(QIcon(LOGO_ICO))
        self.setStyleSheet(f"""
            QDialog {{ background-color: {C['BG']}; }}
            QLabel {{ background: transparent; color: {C['TEXT']}; }}
            QLabel#muted {{ color: {C['TEXT_MUTED']}; font-size: 11px; }}
            QLabel#url {{ color: {C['ACCENT']}; font-size: 11px; font-family: Consolas; }}
            QComboBox {{ background: {C['SURFACE2']}; color: {C['TEXT']};
                border: 1px solid {C['BORDER']}; border-radius: 6px; padding: 4px 8px; font-size: 12px; }}
            QPushButton {{ background: {C['SURFACE2']}; color: {C['TEXT_SEC']};
                border: 1px solid {C['BORDER']}; border-radius: 6px; padding: 6px 14px; font-size: 12px; }}
            QPushButton:hover {{ background: {C['SURFACE3']}; color: {C['TEXT']}; }}
            QPushButton[cssClass="accent"] {{ background: {C['ACCENT']}; color: #fff; border: none; font-weight: bold; }}
            QPushButton[cssClass="accent"]:hover {{ background: {C['ACCENT_HV']}; }}
        """)

        # 服务：复用主窗口持有的实例；首次打开时创建（绑定在后台线程，不卡界面）
        self._server = getattr(app, "_phone_server", None)
        if self._server is None:
            cfg = load_config()
            try:
                base_port = int(cfg.get("phone_port", 8765) or 8765)
            except (ValueError, TypeError):
                base_port = 8765
            self._server = PhoneTransferServer(
                app.store, on_receive_text=self._queue_text,
                port=pick_free_port(base_port))
            app._phone_server = self._server
        self._server_was_running = self._server.running
        self._server.start()

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(10)

        title = QLabel(tr("phone_title"))
        title.setStyleSheet(f"color: {C['TEXT']}; font-size: 16px; font-weight: bold;")
        root.addWidget(title)

        self._qr_lbl = QLabel(tr("phone_generating"))
        self._qr_lbl.setFixedSize(300, 300)
        self._qr_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qr_lbl.setStyleSheet(
            "background: #ffffff; border-radius: 12px; border: 1px solid #2a3a5f; "
            "color: #6b7f9f; font-size: 13px;")
        root.addWidget(self._qr_lbl, 0, Qt.AlignmentFlag.AlignHCenter)

        hint = QLabel(tr("phone_scan_hint"))
        hint.setStyleSheet(f"color: {C['TEXT_SEC']}; font-size: 13px; font-weight: bold;")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(hint)

        # 多网卡时显示 IP 选择器
        ip_row = QHBoxLayout()
        ip_tag = QLabel(tr("phone_ip_label"))
        ip_tag.setObjectName("muted")
        ip_row.addWidget(ip_tag)
        self._ip_combo = QComboBox()
        self._ip_combo.setVisible(False)
        self._ip_combo.currentIndexChanged.connect(self._on_ip_changed)
        ip_row.addWidget(self._ip_combo, 1)
        root.addLayout(ip_row)

        url_hint = QLabel(tr("phone_url_hint"))
        url_hint.setObjectName("muted")
        root.addWidget(url_hint)

        self._url_lbl = QLabel("")
        self._url_lbl.setObjectName("url")
        self._url_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._url_lbl.setWordWrap(True)
        root.addWidget(self._url_lbl)

        self._status_lbl = QLabel()
        self._status_lbl.setObjectName("muted")
        root.addWidget(self._status_lbl)

        note = QLabel("\n".join([
            tr("phone_same_lan"),
            tr("phone_hint_same_wifi"),
            tr("phone_hint_vpn"),
            tr("phone_firewall"),
            tr("phone_still_running"),
        ]))
        note.setObjectName("muted")
        note.setWordWrap(True)
        root.addWidget(note)

        btns = QHBoxLayout()
        copy_btn = QPushButton(tr("phone_copy_url"))
        copy_btn.clicked.connect(self._copy_url)
        btns.addWidget(copy_btn)
        refresh_btn = QPushButton(tr("phone_refresh"))
        refresh_btn.clicked.connect(self._refresh)
        btns.addWidget(refresh_btn)
        close_btn = QPushButton(tr("phone_close"))
        close_btn.setProperty("cssClass", "accent")
        close_btn.clicked.connect(self.reject)
        btns.addWidget(close_btn)
        root.addLayout(btns)

        # 接收手机发来的文字（跨线程安全：HTTP 线程入队，主线程定时取）
        self._drain_timer = QTimer(self)
        self._drain_timer.timeout.connect(self._drain_inbox)
        self._drain_timer.start(300)
        self._client_timer = QTimer(self)
        self._client_timer.timeout.connect(self._update_status)
        self._client_timer.start(2000)

        # 后台二维码线程：IP 检测 + QR 生成
        self._qr_worker = PhoneQRWorker(self)
        self._qr_worker.sig_ips.connect(self._on_ips)
        self._qr_worker.sig_qr.connect(self._on_qr)
        self._qr_worker.sig_error.connect(self._on_qr_error)
        self._qr_worker.start()
        self._update_status()
        self.app._refresh_phone_tray()

    # ---- 二维码 / IP ----

    def _current_url(self):
        ip = self._current_ip or get_lan_ip()
        return f"http://{ip}:{self._server.port}/?t={self._server.token}"

    def _schedule_qr(self):
        self._qr_lbl.setPixmap(QPixmap())
        self._qr_lbl.setText(tr("phone_generating"))
        self._qr_url = None
        self._qr_worker.request(self._current_url())

    def _on_ips(self, ips):
        self._ips = ips or ["127.0.0.1"]
        if len(self._ips) > 1:
            self._ip_combo.blockSignals(True)
            self._ip_combo.clear()
            for ip in self._ips:
                self._ip_combo.addItem(ip)
            self._ip_combo.setCurrentIndex(0)
            self._ip_combo.blockSignals(False)
            self._ip_combo.setVisible(True)
        else:
            self._ip_combo.setVisible(False)
        self._current_ip = self._ips[0]
        self._schedule_qr()

    def _on_ip_changed(self, idx):
        if 0 <= idx < len(self._ips):
            self._current_ip = self._ips[idx]
            self._schedule_qr()

    def _on_qr(self, url, img):
        if url != self._current_url():
            return  # 过期结果（token 已更换），丢弃
        pm = QPixmap.fromImage(img)
        self._qr_lbl.setPixmap(pm.scaled(
            280, 280, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))
        self._url_lbl.setText(url)
        self._qr_url = url

    def _on_qr_error(self, msg):
        self._qr_lbl.setText(msg)

    def _update_status(self):
        srv = self._server
        if srv.running:
            if not self._fw_done:
                self._fw_done = True
                self._try_firewall_rule()
            status = tr("phone_status_running", port=srv.port)
            clients = srv.client_count()
            if clients:
                status += " · " + tr("phone_status_clients", n=clients)
            self._status_lbl.setText(status)
        elif srv.last_error:
            self._status_lbl.setText(tr("phone_start_failed",
                                        err=srv.last_error))
            self._qr_lbl.setText(tr("phone_start_failed",
                                    err=srv.last_error))
        else:
            self._status_lbl.setText(tr("phone_starting"))

    def _try_firewall_rule(self):
        """尽力自动放行 Windows 防火墙（仅打包版；后台线程执行，不阻塞界面）。"""
        if not IS_WIN or not getattr(sys, "frozen", False):
            return

        def _work():
            try:
                exe = os.path.abspath(sys.executable)
                rule = "YouBoard Phone Transfer"
                out = subprocess.run(
                    ["netsh", "advfirewall", "firewall", "show", "rule",
                     "name=" + rule],
                    capture_output=True, text=True, timeout=6)
                if rule in (out.stdout or ""):
                    return
                subprocess.run(
                    ["netsh", "advfirewall", "firewall", "add", "rule",
                     "name=" + rule, "dir=in", "action=allow",
                     "program=" + exe, "enable=yes",
                     "profile=any"],
                    capture_output=True, text=True, timeout=6)
            except Exception:
                pass

        threading.Thread(target=_work, daemon=True).start()

    def _refresh(self):
        """刷新二维码：只更换 token（旧链接立即失效）+ 重新生成二维码，零卡顿。"""
        srv = self._server
        if not srv.running:
            srv.start()  # 服务未运行时（如曾被托盘停止）重新启动
        srv.rotate_token()
        self._qr_url = None
        self._url_lbl.setText("")
        self._qr_lbl.setText(tr("phone_generating"))
        self._qr_lbl.setPixmap(QPixmap())
        self._schedule_qr()
        self._update_status()
        self.app._refresh_phone_tray()

    def _copy_url(self):
        try:
            set_clipboard_text(self._qr_url or self._current_url())
            self.app._set_status(tr("phone_copied"), "ok")
        except Exception:
            pass

    # ---- 手机 → 电脑 ----

    def _queue_text(self, text):
        self._inbox.put(text)

    def _drain_inbox(self):
        while True:
            try:
                text = self._inbox.get_nowait()
            except queue.Empty:
                break
            self._handle_phone_text(text)

    def _handle_phone_text(self, text):
        try:
            self.app.store.mark_self_copy()
            self.app.store.add_text(text)
            try:
                set_clipboard_text(text)
            except Exception:
                pass
            self.app._refresh_all()
            self.app._update_desk_widget()
            self.app._set_status(tr("phone_received"), "ok")
            tray = getattr(self.app, "_tray", None)
            if tray is not None:
                try:
                    tray.showMessage("YouBoard", tr("phone_received"),
                                     QSystemTrayIcon.MessageIcon.Information, 3000)
                except Exception:
                    pass
        except Exception:
            pass

    def closeEvent(self, event):
        self._qr_worker.stop()
        self._qr_worker.wait(1500)
        # 窗口关闭后服务保持运行，方便其他设备继续扫码连接；托盘可随时停止
        if self._server.running:
            try:
                self.app._refresh_phone_tray()
                if not self._server_was_running:
                    tray = getattr(self.app, "_tray", None)
                    if tray is not None:
                        tray.showMessage("YouBoard", tr("phone_still_running"),
                                         QSystemTrayIcon.MessageIcon.Information,
                                         3500)
            except Exception:
                pass
        event.accept()


class SettingsDialog(QDialog):

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.setWindowTitle(tr("settings_title"))
        self.resize(480, 620)
        self.setMinimumSize(420, 500)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)
        if LOGO_ICO and os.path.exists(LOGO_ICO):
            self.setWindowIcon(QIcon(LOGO_ICO))

        cfg = load_config()
        self._lang_sel = LANG if LANG in STRINGS else "zh"
        self._theme_sel = cfg.get("theme", "dark")
        self._bg_path = cfg.get("bg_image", "")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.setStyleSheet(f"""
            QDialog {{ background-color: {C['BG']}; }}
            QScrollArea {{ background: transparent; border: none; }}
            QScrollArea > QWidget > QWidget {{ background: {C['BG']}; }}
            QWidget {{ color: {C['TEXT']}; font-family: "Microsoft YaHei UI","Segoe UI",sans-serif; }}
            QLabel {{ background: transparent; color: {C['TEXT']}; }}
            QPushButton {{ background: {C['SURFACE2']}; color: {C['TEXT_SEC']}; border: 1px solid {C['BORDER']};
                border-radius: 6px; padding: 6px 14px; font-size: 12px; }}
            QPushButton:hover {{ background: {C['SURFACE3']}; color: {C['TEXT']}; }}
            QPushButton[cssClass="accent"] {{ background: {C['ACCENT']}; color: #fff; border: none; font-weight: bold; }}
            QPushButton[cssClass="accent"]:hover {{ background: {C['ACCENT_HV']}; }}
            QCheckBox {{ color: {C['TEXT']}; spacing: 8px; }}
            QCheckBox::indicator {{ width: 18px; height: 18px; border: 2px solid {C['BORDER_LT']};
                border-radius: 4px; background: {C['SURFACE2']}; }}
            QCheckBox::indicator:checked {{ background: {C['ACCENT']}; border-color: {C['ACCENT']}; }}
            QScrollBar:vertical {{ background: transparent; width: 8px; }}
            QScrollBar::handle:vertical {{ background: {C['BORDER_LT']}; border-radius: 4px; min-height: 30px; }}
        """)

        # Mini light bar
        self.light = AmbientLightBar(theme=self._theme_sel)
        root.addWidget(self.light)
        self.light.surge(215.0, 0.5)

        # Scrollable area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll = scroll
        scroll.verticalScrollBar().valueChanged.connect(
            self._refresh_button_cursors)
        inner = QWidget()
        self._lay = QVBoxLayout(inner)
        self._lay.setContentsMargins(16, 12, 16, 12)
        self._lay.setSpacing(12)
        scroll.setWidget(inner)
        root.addWidget(scroll, 1)

        # Language card
        self._card(tr("set_language"))
        lang_row = QHBoxLayout()
        self._lang_btns = {}
        for code in ("zh", "en"):
            btn = QPushButton(tr("set_lang_" + code))
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn.clicked.connect(lambda _, c=code: self._pick_lang(c))
            self._lang_btns[code] = btn
            lang_row.addWidget(btn)
        self._lay.addLayout(lang_row)
        self._paint_lang()

        # General card
        self._card(tr("set_general"))
        auto_row = QHBoxLayout()
        t1 = QLabel(tr("set_autostart"))
        t1.setStyleSheet(f"color: {C['TEXT']}; font-weight: bold;")
        auto_row.addWidget(t1, 1)
        self._auto_cb = QCheckBox()
        self._auto_cb.setChecked(get_autostart())
        auto_row.addWidget(self._auto_cb)
        self._lay.addLayout(auto_row)
        self._add_sep()

        # Hotkeys row (opens independent hotkey settings dialog)
        self._hotkey_values = self._init_hotkey_values(cfg)
        hk_row = QHBoxLayout()
        hk_title = QLabel(tr("set_hotkeys_entry"))
        hk_title.setStyleSheet(f"color: {C['TEXT']}; font-weight: bold;")
        hk_row.addWidget(hk_title, 1)
        hk_open = QPushButton(tr("hk_change"))
        hk_open.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        hk_open.clicked.connect(self._open_hotkeys)
        hk_row.addWidget(hk_open)
        self._lay.addLayout(hk_row)
        self._add_sep()

        # Desktop widget row (on by default)
        widget_row = QHBoxLayout()
        wg_title = QLabel(tr("set_widget_title"))
        wg_title.setStyleSheet(f"color: {C['TEXT']}; font-weight: bold;")
        widget_row.addWidget(wg_title, 1)
        self._widget_cb = QCheckBox()
        self._widget_cb.setChecked(bool(cfg.get("desktop_widget", True)))
        widget_row.addWidget(self._widget_cb)
        self._lay.addLayout(widget_row)
        self._add_sep()

        # Temporary session row (merged from privacy mode)
        session_row = QHBoxLayout()
        ss_title = QLabel(tr("set_session_title"))
        ss_title.setStyleSheet(f"color: {C['TEXT']}; font-weight: bold;")
        session_row.addWidget(ss_title, 1)
        self._session_cb = QCheckBox()
        self._session_cb.setChecked(bool(cfg.get("temporary_session", False)
                                         or cfg.get("privacy_mode", False)))
        session_row.addWidget(self._session_cb)
        self._lay.addLayout(session_row)
        ss_desc = QLabel(tr("set_session_desc"))
        ss_desc.setStyleSheet(f"color: {C['TEXT_MUTED']}; font-size: 10px;")
        ss_desc.setWordWrap(True)
        self._lay.addWidget(ss_desc)

        # Theme card
        self._card(tr("set_theme"))
        theme_row = QHBoxLayout()
        self._theme_btns = {}
        for tname, ico in (("dark", "anse.ico"), ("light", "liangse.ico")):
            btn = QPushButton(tr("set_theme_" + tname))
            _tp = _res_icon(ico)
            if os.path.exists(_tp):
                btn.setIcon(QIcon(_tp))
                btn.setIconSize(QSize(16, 16))
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn.clicked.connect(lambda _, t=tname: self._pick_theme(t))
            self._theme_btns[tname] = btn
            theme_row.addWidget(btn)
        self._lay.addLayout(theme_row)
        self._paint_theme()

        # Background card
        self._card(tr("set_bg"))
        bg_row = QHBoxLayout()
        self._bg_lbl = QLabel(self._bg_display_name())
        self._bg_lbl.setStyleSheet(f"color: {C['TEXT_SEC']}; font-size: 11px;")
        self._bg_lbl.setWordWrap(True)
        self._bg_lbl.setMaximumWidth(320)
        bg_row.addWidget(self._bg_lbl, 1)
        sel_btn = QPushButton(tr("set_bg_select"))
        sel_btn.clicked.connect(self._select_bg)
        bg_row.addWidget(sel_btn)
        clr_btn = QPushButton(tr("set_bg_clear"))
        clr_btn.clicked.connect(self._clear_bg)
        bg_row.addWidget(clr_btn)
        wall_btn = QPushButton(tr("set_bg_wallpaper"))
        wall_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        wall_btn.clicked.connect(self._use_wallpaper)
        bg_row.addWidget(wall_btn)
        self._lay.addLayout(bg_row)

        # 历史壁纸：横向缩略图，点击可再次使用
        hist_lbl = QLabel(tr("set_bg_history"))
        hist_lbl.setStyleSheet(f"color: {C['TEXT_MUTED']}; font-size: 11px; font-weight: bold; "
                               f"letter-spacing: 1px; padding-top: 6px;")
        self._lay.addWidget(hist_lbl)
        self._bg_history = QListWidget()
        self._bg_history.setViewMode(QListView.ViewMode.IconMode)
        self._bg_history.setFlow(QListView.Flow.LeftToRight)
        self._bg_history.setWrapping(False)
        self._bg_history.setFixedHeight(76)
        self._bg_history.setIconSize(QSize(56, 40))
        # 历史壁纸仅供点击选用，禁止拖拽/改序
        self._bg_history.setMovement(QListView.Movement.Static)
        self._bg_history.setDragEnabled(False)
        self._bg_history.setAcceptDrops(False)
        self._bg_history.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop)
        self._bg_history.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._bg_history.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._bg_history.customContextMenuRequested.connect(self._show_bg_history_menu)
        self._bg_history.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._bg_history.installEventFilter(self)
        self._bg_history.setStyleSheet(
            f"QListWidget {{ background: {C['SURFACE2']}; border: 1px solid {C['BORDER']}; "
            f"border-radius: 8px; outline: none; }} "
            f"QListWidget::item {{ padding: 4px; border-radius: 6px; }} "
            f"QListWidget::item:selected {{ background: {C['ACCENT_DIM']}; "
            f"border: 1px solid {C['ACCENT']}; }}")
        self._bg_history.itemClicked.connect(self._on_pick_history)
        self._lay.addWidget(self._bg_history)
        self._fill_bg_history()

        # Phone transfer card
        self._card(tr("set_phone"))
        ph_desc = QLabel(tr("set_phone_desc"))
        ph_desc.setStyleSheet(f"color: {C['TEXT_SEC']}; font-size: 11px;")
        ph_desc.setWordWrap(True)
        self._lay.addWidget(ph_desc)
        ph_open = QPushButton(tr("set_phone_open"))
        ph_open.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        ph_open.clicked.connect(lambda: PhoneTransferDialog(self.app).exec())
        self._lay.addWidget(ph_open)

        # Cloud sync card（简洁入口：详细配置在独立窗口中）
        self._card(tr("set_sync"))
        sd = QLabel(tr("set_sync_desc"))
        sd.setStyleSheet(f"color: {C['TEXT_SEC']}; font-size: 11px;")
        sd.setWordWrap(True)
        self._lay.addWidget(sd)
        sync_open = QPushButton(tr("set_sync_open"))
        sync_open.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        sync_open.clicked.connect(lambda: CloudSyncDialog(self.app).exec())
        self._lay.addWidget(sync_open)

        # About card
        self._card(tr("set_about"))
        ver = QLabel(f"{APP_NAME}  v{APP_VERSION}")
        ver.setStyleSheet(f"color: {C['TEXT']}; font-family: Bahnschrift; font-size: 14px; font-weight: bold;")
        self._lay.addWidget(ver)
        data_lbl = QLabel(f"{tr('set_data_location')}: {os.path.dirname(HISTORY_FILE)}")
        data_lbl.setStyleSheet(f"color: {C['TEXT_MUTED']}; font-size: 10px; font-family: Consolas;")
        data_lbl.setWordWrap(True)
        self._lay.addWidget(data_lbl)
        update_btn = QPushButton(tr("set_check_update"))
        update_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        update_btn.clicked.connect(self._check_update)
        self._lay.addWidget(update_btn)
        self._lay.addStretch()

        # Footer buttons
        footer = QHBoxLayout()
        footer.addStretch()
        cancel_btn = QPushButton(tr("btn_cancel"))
        cancel_btn.clicked.connect(self.reject)
        footer.addWidget(cancel_btn)
        save_btn = QPushButton(tr("btn_save"))
        save_btn.setProperty("cssClass", "accent")
        save_btn.clicked.connect(self._save)
        footer.addWidget(save_btn)
        root.addLayout(footer)

        # 设置窗口打开期间兜底刷新：即使主窗口轮询受阻，复制的新内容也会实时显示
        self._live_refresh_timer = QTimer(self)
        self._live_refresh_timer.timeout.connect(self._live_refresh)
        self._live_refresh_timer.start(800)
        # 兜底光标刷新：修复 Qt 滚动区按钮手型光标在部分进入方向下不生效的问题
        self._cursor_timer = QTimer(self)
        self._cursor_timer.timeout.connect(self._force_cursor_refresh)
        self._cursor_timer.start(120)

    def _refresh_button_cursors(self):
        """滚动区按钮手型光标刷新：修复 Qt 滚动区在鼠标静止时
        滚动内容不更新子控件光标的问题（只重新设置原本就是手型的按钮）。"""
        for btn in self.findChildren(QPushButton):
            if btn.cursor().shape() != Qt.CursorShape.ArrowCursor:
                btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

    def _force_cursor_refresh(self):
        """鼠标下的控件若是手型按钮，强制重设光标（任何进入方向都立即生效）。"""
        try:
            w = QApplication.widgetAt(QCursor.pos())
            if w is not None and w.cursor().shape() != Qt.CursorShape.ArrowCursor:
                w.setCursor(w.cursor())
        except Exception:
            pass

    def showEvent(self, event):
        super().showEvent(event)
        self._refresh_button_cursors()

    def _live_refresh(self):
        try:
            self.app._poll_monitor()
        except Exception:
            pass

    def _card(self, title):
        lbl = QLabel(title)
        lbl.setStyleSheet(f"color: {C['TEXT_MUTED']}; font-size: 11px; font-weight: bold; "
                          f"letter-spacing: 1px; padding-top: 8px;")
        self._lay.addWidget(lbl)

    def _add_sep(self):
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFixedHeight(1)
        line.setStyleSheet(f"background: {C['BORDER']}; color: {C['BORDER']};")
        self._lay.addWidget(line)

    def _pick_lang(self, code):
        self._lang_sel = code
        self._paint_lang()
        self.light.pulse(215.0, 0.3 if code == "zh" else 0.7, strength=0.8)

    def _paint_lang(self):
        for code, btn in self._lang_btns.items():
            if code == self._lang_sel:
                btn.setStyleSheet(f"background: {C['ACCENT']}; color: #0c1420; font-weight: bold; "
                                  f"border-radius: 6px; padding: 7px 16px;")
            else:
                btn.setStyleSheet(f"background: {C['SURFACE3']}; color: {C['TEXT_SEC']}; "
                                  f"border-radius: 6px; padding: 7px 16px;")

    def _pick_theme(self, tname):
        self._theme_sel = tname
        self._paint_theme()
        self.light.surge(215.0, 0.3 if tname == "dark" else 0.7)

    def _paint_theme(self):
        for tname, btn in self._theme_btns.items():
            if tname == self._theme_sel:
                btn.setStyleSheet(f"background: {C['ACCENT']}; color: #0c1420; font-weight: bold; "
                                  f"border-radius: 6px; padding: 7px 16px;")
            else:
                btn.setStyleSheet(f"background: {C['SURFACE3']}; color: {C['TEXT_SEC']}; "
                                  f"border-radius: 6px; padding: 7px 16px;")

    def _bg_display_name(self):
        if self._bg_path and os.path.exists(self._bg_path):
            return _short_display_name(os.path.basename(self._bg_path))
        return tr("set_bg_current")

    def _select_bg(self):
        path, _ = QFileDialog.getOpenFileName(
            self, tr("set_bg_select"), "",
            "Images (*.png *.jpg *.jpeg *.bmp *.gif);;All (*.*)")
        if path:
            self._bg_path = path
            self._bg_lbl.setText(_short_display_name(os.path.basename(path)))

    def _clear_bg(self):
        self._bg_path = ""
        self._bg_lbl.setText(tr("set_bg_current"))
        try:
            mv = getattr(self.app, "_bg_movie", None)
            if mv is not None:
                mv.stop()
        except Exception:
            pass

    def _capture_desktop_screenshot(self):
        """抓取当前整屏（含 Wallpaper Engine 正在播放的画面）作为"当前壁纸"。

        先隐藏本应用主窗口与设置窗口，再临时隐藏桌面图标与任务栏，尽量只截到壁纸；
        抓完全部恢复。全程异常安全，任何失败都会恢复窗口并返回空串。
        """
        out = os.path.join(IMAGES_DIR, "_cur_wallpaper.png")
        try:
            os.makedirs(IMAGES_DIR, exist_ok=True)
        except OSError:
            pass
        main = getattr(self, "app", None)
        main_vis = bool(main and main.isVisible())
        self_vis = self.isVisible()
        hidden = []
        try:
            if main_vis and main is not None:
                main.hide()
            if self_vis:
                self.hide()
            hidden = _hide_desktop_overlay()
            QApplication.processEvents()
            QApplication.processEvents()
            time.sleep(0.05)
            QApplication.processEvents()
            screen = QApplication.primaryScreen()
            if screen is None:
                return ""
            pix = screen.grabWindow(0)
            if pix and not pix.isNull():
                img = pix.toImage()
                if not _image_is_mostly_black(img):
                    pix.save(out)
                    return out
        except Exception:
            pass
        finally:
            _show_desktop_overlay(hidden)
            if self_vis:
                try:
                    self.show()
                except Exception:
                    pass
            if main_vis and main is not None:
                try:
                    main.show()
                except Exception:
                    pass
        return ""

    def _fill_bg_history(self):
        cfg = load_config()
        self._bg_history.clear()
        shown = 0
        for path in (cfg.get("bg_history", []) or [])[:12]:
            if not path or not os.path.exists(path):
                continue
            pm = QPixmap(path)
            if pm.isNull():
                continue
            thumb = pm.scaled(56, 40, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                              Qt.TransformationMode.SmoothTransformation)
            item = QListWidgetItem(QIcon(thumb),
                                   _short_display_name(os.path.basename(path)))
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(path)
            self._bg_history.addItem(item)
            shown += 1
        if shown == 0:
            item = QListWidgetItem(tr("set_bg_history") + " · " + tr("set_bg_current"))
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self._bg_history.addItem(item)

    def _on_pick_history(self, item):
        path = item.data(Qt.ItemDataRole.UserRole)
        if path and os.path.exists(path):
            self._bg_path = path
            self._bg_lbl.setText(_short_display_name(os.path.basename(path)))

    def _show_bg_history_menu(self, pos):
        """历史壁纸右键菜单：设为背景 / 删除该背景。"""
        item = self._bg_history.itemAt(pos)
        if item is None:
            return
        path = item.data(Qt.ItemDataRole.UserRole)
        if not path:
            return
        menu = QMenu(self)
        act_use = menu.addAction(tr("bg_h_use"))
        act_del = menu.addAction(tr("bg_h_del"))
        chosen = menu.exec(self._bg_history.mapToGlobal(pos))
        if chosen is act_use:
            self._bg_path = path
            self._bg_lbl.setText(_short_display_name(os.path.basename(path)))
        elif chosen is act_del:
            self._delete_history_bg(path)

    def _delete_history_bg(self, path):
        """从历史壁纸中删除指定项；若其为当前背景则恢复默认。"""
        cfg = load_config()
        hist = cfg.get("bg_history", []) or []
        if path in hist:
            cfg["bg_history"] = [p for p in hist if p != path]
            save_config(cfg)
        if self._bg_path == path:
            self._bg_path = ""
            self._bg_lbl.setText(tr("set_bg_current"))
        self._fill_bg_history()

    def eventFilter(self, obj, event):
        if (obj is self._bg_history and event.type() == QEvent.Type.KeyPress
                and event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace)):
            item = self._bg_history.currentItem()
            if item is not None:
                path = item.data(Qt.ItemDataRole.UserRole)
                if path:
                    self._delete_history_bg(path)
                return True
        return super().eventFilter(obj, event)

    def _init_hotkey_values(self, cfg):
        vals = {"hotkey": _canon_hotkey(cfg.get("hotkey", "alt+q"))}
        for key in ("hk_copy", "hk_delete", "hk_pin",
                    "hk_next_tab", "hk_prev_tab"):
            vals[key] = _canon_hotkey(
                cfg.get(key, _ACTION_HOTKEY_DEFAULTS[key]))
        return vals

    def _open_hotkeys(self):
        """打开独立的动作快捷键设置界面。"""
        dlg = _HotkeyDialog(self, self._hotkey_values)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._hotkey_values = dlg.values()

    def _save(self):
        try:
            cfg = load_config()
            bg_changed = cfg.get("bg_image", "") != self._bg_path
            cfg["bg_image"] = self._bg_path
            hist = cfg.get("bg_history", []) or []
            if self._bg_path and os.path.exists(self._bg_path):
                hist = [p for p in hist if p != self._bg_path]
                hist.insert(0, self._bg_path)
                cfg["bg_history"] = hist[:12]
            cfg["desktop_widget"] = self._widget_cb.isChecked()
            cfg["hotkey"] = self._hotkey_values.get("hotkey", "alt+q")
            cfg["temporary_session"] = self._session_cb.isChecked()
            for k, v in self._hotkey_values.items():
                if k != "hotkey":
                    cfg[k] = v
            save_config(cfg)
            self.app.set_temporary_session(self._session_cb.isChecked())
            self.accept()
            self.app.apply_settings(self._lang_sel, self._auto_cb.isChecked(),
                                    self._theme_sel, bg_changed)
        except Exception:
            import traceback as _tb
            _tb.print_exc()
            try:
                self.accept()
            except Exception:
                pass

    def _use_wallpaper(self):
        # 只读取系统注册表已保存的壁纸文件；不再对壁纸层做 GDI/PrintWindow 强抓取。
        # 那条 _capture_wallpaper() 在硬件加速/壁纸引擎下会把窗口强制重绘，
        # 导致标题栏被"搅坏"（关闭按钮红块左上出现梯形缺口），故此处直接规避。
        p = _get_wallpaper()
        if not p:
            QMessageBox.information(self, tr("set_bg"), tr("set_bg_wall_err"))
            return
        self._bg_path = p
        self._bg_lbl.setText(_short_display_name(os.path.basename(p)))

    def _check_update(self):
        """Check GitHub Releases and auto-update by downloading + replacing EXE."""
        import urllib.request
        import json as _json

        def _short_release_notes(body):
            """从 GitHub Release 正文提取最多 2 句更新说明。"""
            if not body:
                return ""
            import re as _re
            lines = []
            for ln in body.splitlines():
                s = ln.strip()
                if not s:
                    continue
                if s.startswith(("#", ">", "!")) or _re.match(r"^[-*+]\s", s):
                    continue
                lines.append(s)
                if len(lines) >= 2:
                    break
            note = " ".join(lines)
            note = _re.sub(r"[*_`#>{}\[\]]", "", note)
            note = _re.sub(r"\s+", " ", note).strip()
            parts = _re.split(r"(?<=[。！？.!?])\s*", note)
            if len(parts) > 2:
                note = "".join(parts[:2]).strip()
            if len(note) > 80:
                note = note[:77].rstrip() + "…"
            return note

        try:
            url = "https://api.github.com/repos/cloudxys/YouBoard/releases/latest"
            req = urllib.request.Request(url, headers={"User-Agent": "YouBoard"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = _json.loads(resp.read().decode())
            tag = data.get("tag_name", "").lstrip("v")
            name = data.get("name", tag)
            # Only update if remote version is actually newer
            def _ver_tuple(v):
                try:
                    return tuple(int(x) for x in v.split(".")[:3])
                except (ValueError, AttributeError):
                    return (0,)
            if not tag or _ver_tuple(tag) <= _ver_tuple(APP_VERSION):
                QMessageBox.information(self, tr("upd_title"), tr("upd_latest", v=APP_VERSION))
                return
            # Find the portable EXE asset
            assets = data.get("assets", [])
            dl_url = None
            for a in assets:
                if a["name"] == "YouBoard.exe":
                    dl_url = a["browser_download_url"]
                    break
            if not dl_url:
                html_url = data.get("html_url", "https://github.com/cloudxys/YouBoard/releases")
                import webbrowser
                webbrowser.open(html_url)
                return
            if IS_MAC:
                # macOS 版暂不支持应用内替换可执行文件：直接打开 Releases 页面
                import webbrowser
                webbrowser.open(data.get(
                    "html_url", "https://github.com/cloudxys/YouBoard/releases"))
                return
            notes = _short_release_notes(data.get("body", ""))
            msg = tr("upd_new_msg", cur=APP_VERSION, new=tag, name=name)
            if notes:
                msg = notes + "\n\n" + msg
            ret = QMessageBox.question(
                self, tr("upd_new_title"),
                msg,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if ret != QMessageBox.StandardButton.Yes:
                return
            # Download new EXE
            self._do_update(dl_url)
        except Exception as e:
            import urllib.error
            err_str = str(e)
            if isinstance(e, urllib.error.HTTPError) and e.code == 403:
                QMessageBox.warning(self, tr("upd_title"), tr("upd_rate_limit"))
            elif isinstance(e, (urllib.error.URLError, TimeoutError, ConnectionError, OSError)):
                QMessageBox.warning(self, tr("upd_title"), tr("upd_network_err"))
            else:
                QMessageBox.warning(self, tr("upd_title"), tr("upd_failed", e=err_str))

    def _do_update(self, dl_url):
        """Download new EXE in-app with live progress, then replace + restart."""
        try:
            # Determine current EXE path
            if getattr(sys, "frozen", False):
                current_exe = os.path.abspath(sys.executable)
            else:
                current_exe = os.path.abspath(sys.argv[0])
            exe_dir = os.path.dirname(current_exe)
            tmp_exe = os.path.join(exe_dir, "_YouBoard_update.exe")

            # Candidate URLs: direct GitHub first (works worldwide, including
            # overseas users), then a curated set of accelerators as fallbacks
            # (mainly for users in mainland China where GitHub can be unreliable).
            urls = [dl_url]
            if "github.com" in dl_url:
                for mirror in ("https://ghproxy.net/", "https://gh-proxy.com/",
                               "https://mirror.ghproxy.com/", "https://ghfast.top/",
                               "https://github.moeyy.xyz/", "https://ghproxy.com/"):
                    urls.append(mirror + dl_url)

            # Real-time progress dialog
            dlg = QProgressDialog("正在准备下载...", "取消", 0, 100, self)
            dlg.setWindowTitle("正在更新")
            dlg.setWindowModality(Qt.WindowModality.WindowModal)
            dlg.setMinimumDuration(0)
            dlg.setAutoClose(False)
            dlg.setAutoReset(False)
            dlg.resize(380, 130)

            self._dl_current = current_exe
            self._dl_worker = _DownloadWorker(urls, tmp_exe, self)

            def on_progress(recv, total):
                if dlg.wasCanceled():
                    self._dl_worker.abort()
                    return
                if total > 0:
                    pct = int(recv * 100 / total)
                    dlg.setMaximum(100)
                    dlg.setValue(pct)
                    dlg.setLabelText(
                        f"正在下载新版本... {recv / 1048576:.1f} / {total / 1048576:.1f} MB  ({pct}%)")
                else:
                    dlg.setMaximum(0)  # indeterminate
                    dlg.setLabelText(f"正在下载新版本... {recv / 1048576:.1f} MB")

            def on_status(text):
                if not dlg.wasCanceled():
                    dlg.setLabelText(text)

            def on_ok(path):
                dlg.close()
                self._finish_update(path)

            def on_fail(err):
                dlg.close()
                QMessageBox.warning(self, "更新失败", f"下载失败: {err}")

            self._dl_worker.progress.connect(on_progress)
            self._dl_worker.status.connect(on_status)
            self._dl_worker.finished_ok.connect(on_ok)
            self._dl_worker.failed.connect(on_fail)
            dlg.canceled.connect(self._dl_worker.abort)
            self._dl_worker.start()
        except Exception as e:
            QMessageBox.warning(self, "更新失败", f"下载或替换失败: {e}")

    def _finish_update(self, tmp_exe):
        """Write replace-and-restart batch script, launch it, and quit."""
        try:
            current_exe = self._dl_current
            exe_dir = os.path.dirname(current_exe)
            bat_path = os.path.join(exe_dir, "_update.bat")
            bat_content = f"""@echo off
chcp 65001 >nul 2>&1
timeout /t 3 /nobreak >nul
:del_loop
del /f "{current_exe}" >nul 2>&1
if exist "{current_exe}" (
    timeout /t 1 /nobreak >nul
    goto del_loop
)
move /y "{tmp_exe}" "{current_exe}" >nul 2>&1
for /d %%i in ("%TEMP%\\_MEI*") do rd /s /q "%%i" >nul 2>&1
timeout /t 1 /nobreak >nul
start "" "{current_exe}"
del "%~f0"
"""
            with open(bat_path, "w", encoding="utf-8") as f:
                f.write(bat_content)
            import subprocess
            subprocess.Popen(["cmd.exe", "/c", bat_path],
                             creationflags=0x00000008)  # DETACHED_PROCESS
            self.app._real_quit()
        except Exception as e:
            QMessageBox.warning(self, "更新失败", f"替换失败: {e}")


class CloudSyncDialog(QDialog):
    """云同步独立窗口：Gist / WebDAV 配置 + 手动上传下载（不占用设置页空间）。"""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self._sync_worker = None
        self.setWindowTitle(tr("set_sync"))
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)
        self.setFixedSize(500, 590)
        if LOGO_ICO and os.path.exists(LOGO_ICO):
            self.setWindowIcon(QIcon(LOGO_ICO))
        self.setStyleSheet(f"""
            QDialog {{ background-color: {C['BG']}; }}
            QLabel {{ background: transparent; color: {C['TEXT']}; }}
            QLabel#muted {{ color: {C['TEXT_MUTED']}; font-size: 11px; }}
            QComboBox, QLineEdit {{ background: {C['SURFACE2']}; color: {C['TEXT']};
                border: 1px solid {C['BORDER']}; border-radius: 6px; padding: 5px 8px; font-size: 12px; }}
            QPushButton {{ background: {C['SURFACE2']}; color: {C['TEXT_SEC']};
                border: 1px solid {C['BORDER']}; border-radius: 6px; padding: 6px 14px; font-size: 12px; }}
            QPushButton:hover {{ background: {C['SURFACE3']}; color: {C['TEXT']}; }}
            QPushButton[cssClass="accent"] {{ background: {C['ACCENT']}; color: #fff; border: none; font-weight: bold; }}
            QPushButton[cssClass="accent"]:hover {{ background: {C['ACCENT_HV']}; }}
        """)

        cfg = load_config()
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        title = QLabel(tr("set_sync"))
        title.setStyleSheet(f"color: {C['TEXT']}; font-size: 16px; font-weight: bold;")
        root.addWidget(title)
        desc = QLabel(tr("set_sync_desc"))
        desc.setStyleSheet(f"color: {C['TEXT_SEC']}; font-size: 11px;")
        desc.setWordWrap(True)
        root.addWidget(desc)

        backend_row = QHBoxLayout()
        bl = QLabel(tr("set_sync_backend"))
        bl.setStyleSheet(f"color: {C['TEXT']}; font-weight: bold;")
        backend_row.addWidget(bl)
        self._sync_backend_combo = QComboBox()
        self._sync_backend_combo.addItem(tr("set_sync_off"), "")
        self._sync_backend_combo.addItem("GitHub Gist", "gist")
        self._sync_backend_combo.addItem("WebDAV", "webdav")
        _sb = cfg.get("sync_backend", "")
        for _i in range(self._sync_backend_combo.count()):
            if self._sync_backend_combo.itemData(_i) == _sb:
                self._sync_backend_combo.setCurrentIndex(_i)
                break
        self._sync_backend_combo.currentIndexChanged.connect(self._sync_toggle_fields)
        backend_row.addWidget(self._sync_backend_combo, 1)
        root.addLayout(backend_row)

        self._sync_gist_box = QWidget()
        gist_lay = QVBoxLayout(self._sync_gist_box)
        gist_lay.setContentsMargins(0, 0, 0, 0)
        gist_lay.setSpacing(6)
        gist_row = QHBoxLayout()
        gl = QLabel(tr("set_sync_gist_token"))
        gl.setStyleSheet(f"color: {C['TEXT_SEC']}; font-size: 11px;")
        gist_row.addWidget(gl, 1)
        self._gist_token_edit = QLineEdit()
        self._gist_token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._gist_token_edit.setPlaceholderText("ghp_…")
        gist_row.addWidget(self._gist_token_edit, 2)
        gist_lay.addLayout(gist_row)
        self._sync_gist_id_lbl = QLabel("")
        self._sync_gist_id_lbl.setObjectName("muted")
        gist_lay.addWidget(self._sync_gist_id_lbl)
        root.addWidget(self._sync_gist_box)

        self._sync_dav_box = QWidget()
        dav_lay = QVBoxLayout(self._sync_dav_box)
        dav_lay.setContentsMargins(0, 0, 0, 0)
        dav_lay.setSpacing(6)

        def _dav_row(label, edit):
            row = QHBoxLayout()
            l = QLabel(label)
            l.setStyleSheet(f"color: {C['TEXT_SEC']}; font-size: 11px;")
            row.addWidget(l, 1)
            row.addWidget(edit, 2)
            dav_lay.addLayout(row)

        self._dav_url_edit = QLineEdit()
        self._dav_url_edit.setPlaceholderText("https://dav.example.com/YouBoard/")
        _dav_row(tr("set_sync_dav_url"), self._dav_url_edit)
        self._dav_user_edit = QLineEdit()
        _dav_row(tr("set_sync_dav_user"), self._dav_user_edit)
        self._dav_pass_edit = QLineEdit()
        self._dav_pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        _dav_row(tr("set_sync_dav_pass"), self._dav_pass_edit)
        root.addWidget(self._sync_dav_box)

        pass_row = QHBoxLayout()
        pl = QLabel(tr("set_sync_pass"))
        pl.setStyleSheet(f"color: {C['TEXT_SEC']}; font-size: 11px;")
        pass_row.addWidget(pl, 1)
        self._sync_pass_edit = QLineEdit()
        self._sync_pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        pass_row.addWidget(self._sync_pass_edit, 2)
        root.addLayout(pass_row)

        sync_btns = QHBoxLayout()
        up = QPushButton(tr("btn_sync_upload"))
        up.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        up.clicked.connect(lambda: self._do_sync("upload"))
        sync_btns.addWidget(up)
        down = QPushButton(tr("btn_sync_download"))
        down.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        down.clicked.connect(lambda: self._do_sync("download"))
        sync_btns.addWidget(down)
        clr = QPushButton(tr("btn_sync_clear"))
        clr.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        clr.clicked.connect(self._clear_sync)
        sync_btns.addWidget(clr)
        self._sync_btns = [up, down, clr]
        root.addLayout(sync_btns)

        self._sync_status_lbl = QLabel("")
        self._sync_status_lbl.setObjectName("muted")
        self._sync_status_lbl.setWordWrap(True)
        root.addWidget(self._sync_status_lbl)

        footer = QHBoxLayout()
        footer.addStretch()
        close_btn = QPushButton(tr("phone_close"))
        close_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        close_btn.setProperty("cssClass", "accent")
        close_btn.clicked.connect(self.accept)
        footer.addWidget(close_btn)
        root.addLayout(footer)

        # 载入已保存的云同步配置
        self._gist_token_edit.setText(unprotect_secret(cfg.get("sync_gist_token", "")))
        self._sync_gist_id = cfg.get("sync_gist_id", "")
        self._dav_url_edit.setText(cfg.get("sync_webdav_url", ""))
        self._dav_user_edit.setText(cfg.get("sync_webdav_user", ""))
        self._dav_pass_edit.setText(unprotect_secret(cfg.get("sync_webdav_pass", "")))
        self._sync_pass_edit.setText(unprotect_secret(cfg.get("sync_passphrase", "")))
        self._sync_toggle_fields()
        self._update_sync_status()
        # 兜底光标刷新：保证按钮手型光标在任何进入方向都立即生效
        self._cursor_timer = QTimer(self)
        self._cursor_timer.timeout.connect(self._force_cursor_refresh)
        self._cursor_timer.start(120)

    def _force_cursor_refresh(self):
        """鼠标下的控件若是手型按钮，强制重设光标（任何进入方向都立即生效）。"""
        try:
            w = QApplication.widgetAt(QCursor.pos())
            if w is not None and w.cursor().shape() != Qt.CursorShape.ArrowCursor:
                w.setCursor(w.cursor())
        except Exception:
            pass

    # ---- 配置 ----

    def _save_config(self):
        cfg = load_config()
        cfg["sync_backend"] = self._sync_backend_combo.currentData() or ""
        cfg["sync_gist_token"] = protect_secret(self._gist_token_edit.text().strip())
        cfg["sync_webdav_url"] = self._dav_url_edit.text().strip()
        cfg["sync_webdav_user"] = self._dav_user_edit.text().strip()
        cfg["sync_webdav_pass"] = protect_secret(self._dav_pass_edit.text())
        cfg["sync_passphrase"] = protect_secret(self._sync_pass_edit.text())
        if self._sync_gist_id:
            cfg["sync_gist_id"] = self._sync_gist_id
        save_config(cfg)

    def closeEvent(self, event):
        try:
            self._save_config()
        except Exception:
            pass
        event.accept()

    # ---- 交互 ----

    def _sync_toggle_fields(self):
        backend = self._sync_backend_combo.currentData() or ""
        self._sync_gist_box.setVisible(backend == "gist")
        self._sync_dav_box.setVisible(backend == "webdav")
        self._update_sync_status()

    def _update_sync_status(self):
        cfg = load_config()
        parts = []
        last = cfg.get("sync_last", "")
        parts.append(tr("sync_last", time=last[:19]) if last else tr("sync_never"))
        gid = self._sync_gist_id or cfg.get("sync_gist_id", "")
        if gid:
            parts.append(tr("sync_gist_id", gid=gid))
        self._sync_status_lbl.setText(" · ".join(parts))
        self._sync_gist_id_lbl.setText(
            tr("sync_gist_id", gid=self._sync_gist_id) if self._sync_gist_id else "")

    def _do_sync(self, action):
        backend = self._sync_backend_combo.currentData() or ""
        if not backend:
            self._sync_status_lbl.setText(tr("set_sync_off"))
            return
        passphrase = self._sync_pass_edit.text()
        if len(passphrase) < 4:
            self._sync_status_lbl.setText(tr("sync_pass_hint"))
            return
        try:
            if backend == "gist":
                token = self._gist_token_edit.text().strip()
                if not token:
                    self._sync_status_lbl.setText(tr("set_sync_gist_token"))
                    return
                client = GistSyncClient(token, gist_id=self._sync_gist_id)
            else:
                client = WebDAVSyncClient(
                    self._dav_url_edit.text().strip(),
                    self._dav_user_edit.text().strip(),
                    self._dav_pass_edit.text())
        except SyncError as e:
            self._sync_status_lbl.setText(str(e))
            return
        self._sync_status_lbl.setText(tr("sync_syncing"))
        self._set_sync_enabled(False)
        self._sync_worker = SyncWorker(action, client, passphrase,
                                       self.app.store, self)
        self._sync_worker.sig_done.connect(self._on_sync_done)
        self._sync_worker.start()

    def _set_sync_enabled(self, enabled):
        for w in (self._sync_backend_combo, self._gist_token_edit,
                  self._dav_url_edit, self._dav_user_edit,
                  self._dav_pass_edit, self._sync_pass_edit):
            w.setEnabled(enabled)
        for b in self._sync_btns:
            b.setEnabled(enabled)

    def _on_sync_done(self, ok, msg):
        self._set_sync_enabled(True)
        self._sync_status_lbl.setText(msg)
        worker = getattr(self, "_sync_worker", None)
        if ok:
            try:
                cfg = load_config()
                if worker is not None and worker.result_gid:
                    self._sync_gist_id = worker.result_gid
                    cfg["sync_gist_id"] = self._sync_gist_id
                cfg["sync_last"] = datetime.now().isoformat()
                save_config(cfg)
                self._update_sync_status()
                if worker is not None and worker.action == "download":
                    self.app._refresh_all()
                    self.app._update_desk_widget()
            except Exception:
                pass
        self._sync_worker = None

    def _clear_sync(self):
        try:
            cfg = load_config()
            for k in ("sync_backend", "sync_gist_token", "sync_gist_id",
                      "sync_webdav_url", "sync_webdav_user", "sync_webdav_pass",
                      "sync_passphrase", "sync_last"):
                cfg.pop(k, None)
            save_config(cfg)
        except Exception:
            pass
        self._sync_backend_combo.setCurrentIndex(0)
        self._gist_token_edit.clear()
        self._dav_url_edit.clear()
        self._dav_user_edit.clear()
        self._dav_pass_edit.clear()
        self._sync_pass_edit.clear()
        self._sync_gist_id = ""
        self._sync_toggle_fields()
        self._sync_status_lbl.setText(tr("sync_cleared"))


# ===========================================================================
# CLI functions
# ===========================================================================
def cli_list(store, n=20, entry_type=None):
    entries = store.get_all() if entry_type is None else store.get_by_type(entry_type)
    entries = entries[:n]
    if not entries:
        print(tr("cli_empty"))
        return
    pinned_hashes = set()
    for cat in store.categories.values():
        for e in cat["pinned"]:
            pinned_hashes.add(e["hash"])
    print(f"\n{'=' * 100}")
    print(f"  {'#':>3}  {tr('cli_h_pin'):<4}  {tr('cli_h_type'):<6}  "
          f"{tr('cli_h_time'):<21}  {tr('cli_h_preview')}")
    print(f"{'=' * 100}")
    for i, e in enumerate(entries):
        ts = e.get("timestamp", "")
        try:
            ts = datetime.fromisoformat(ts).strftime(TIME_FORMAT)
        except ValueError:
            ts = ts[:19].replace("T", " ")
        pin = "\U0001f4cc" if e["hash"] in pinned_hashes else ""
        etype = e.get("type", "text")
        if etype == "text":
            preview = e["content"][:60].replace("\n", "\\n")
        elif etype == "image":
            preview = f"[IMG] {os.path.basename(e.get('filename', ''))} ({e.get('width', '?')}x{e.get('height', '?')})"
        elif etype == "url":
            preview = e.get("content", "")[:70]
        else:
            paths = e.get("file_paths", [])
            preview = f"[{len(paths)} files] " + ", ".join(os.path.basename(p) for p in paths[:3])
        print(f"  {i + 1:>3}  {pin:<4}  {etype:<6}  {ts:<21}  {preview}")
    print(f"{'=' * 100}")


def cli_search(store, keyword, entry_type=None):
    results = store.search(keyword, entry_type)
    if not results:
        print(tr("cli_not_found", kw=keyword))
        return
    pinned_hashes = set()
    for cat in store.categories.values():
        for e in cat["pinned"]:
            pinned_hashes.add(e["hash"])
    print(f"\n{tr('cli_found', n=len(results))}")
    print(f"{'=' * 100}")
    for i, e in enumerate(results):
        ts = e.get("timestamp", "")[:19].replace("T", " ")
        pin = "\U0001f4cc" if e["hash"] in pinned_hashes else ""
        etype = e.get("type", "text")
        if etype == "text":
            preview = e.get("content", "")[:70]
        elif etype == "url":
            preview = e.get("content", "")[:70]
        else:
            preview = repr(e.get("filename", e.get("file_paths", "")))[:70]
        print(f"  {i + 1:>3}  {pin:<4}  [{etype}]  {ts}  {preview}")
    print(f"{'=' * 100}")


# ===========================================================================
# Single instance mutex
# ===========================================================================
def _single_instance():
    """Prevent multiple GUI instances via a named Win32 mutex."""
    if not IS_WIN:
        # macOS：用文件锁保证单实例
        import tempfile
        import fcntl
        lock_path = os.path.join(tempfile.gettempdir(),
                                 "YouBoard_single_instance.lock")
        try:
            lock_file = open(lock_path, "w")
        except OSError:
            sys.exit(0)
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            sys.exit(0)
        return lock_file

    mutex_name = "YouBoard_SingleInstance_Mutex"
    handle = ctypes.windll.kernel32.CreateMutexW(None, False, mutex_name)
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        hwnd = ctypes.windll.user32.FindWindowW(None, "YouBoard")
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            ctypes.windll.user32.SetForegroundWindow(hwnd)
        sys.exit(0)
    return handle


# ===========================================================================
# Main entry point
# ===========================================================================
def main():
    _mutex_handle = _single_instance()  # noqa: F841

    store = ClipboardStore()
    apply_language(load_config().get("language", "zh"))

    # ---- CLI modes ----
    if "--clear" in sys.argv:
        store.clear()
        print(tr("cli_cleared"))
        return

    if "--list" in sys.argv:
        try:
            idx = sys.argv.index("--list")
            n = int(sys.argv[idx + 1]) if idx + 1 < len(sys.argv) and sys.argv[idx + 1].isdigit() else 20
        except (ValueError, IndexError):
            n = 20
        etype = None
        if "--type" in sys.argv:
            try:
                ti = sys.argv.index("--type")
                etype = sys.argv[ti + 1] if ti + 1 < len(sys.argv) else None
            except (ValueError, IndexError):
                pass
        cli_list(store, n, etype)
        return

    if "--search" in sys.argv:
        try:
            idx = sys.argv.index("--search")
            kw = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else ""
        except (ValueError, IndexError):
            kw = ""
        etype = None
        if "--type" in sys.argv:
            try:
                ti = sys.argv.index("--type")
                etype = sys.argv[ti + 1] if ti + 1 < len(sys.argv) else None
            except (ValueError, IndexError):
                pass
        if kw:
            cli_search(store, kw, etype)
        return

    if "--daemon" in sys.argv:
        print(tr("cli_daemon_started"))
        print(tr("cli_history_file", path=HISTORY_FILE))
        print(tr("cli_ctrl_c"))
        monitor = ClipboardMonitor(store)
        monitor.start()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            monitor.stop()
            print(tr("cli_stopped"))
        return

    # ---- GUI mode ----
    monitor = ClipboardMonitor(store)
    monitor.start()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.installEventFilter(_ThemeTitleBarFilter(app))
    # 任何槽函数/Signal 里的未捕获异常都打印并继续，避免直接退出应用（"卡退"）
    def _safe_excepthook(tp, val, tb):
        import traceback as _tb
        try:
            _tb.print_exception(tp, val, tb)
        except Exception:
            pass
    sys.excepthook = _safe_excepthook
    # Set app-level icon for correct taskbar display
    if LOGO_ICO and os.path.exists(LOGO_ICO):
        app.setWindowIcon(QIcon(LOGO_ICO))
    try:
        restart = True
        while restart:
            cfg = load_config()
            theme_name = cfg.get("theme", "dark")
            apply_language(cfg.get("language", "zh"))
            apply_theme(theme_name)
            apply_global_palette(theme_name)
            app.setStyleSheet(build_qss(theme_name))
            gui = YouBoardApp(store, monitor)
            gui.run()
            app.exec()
            restart = gui.restart_flag
    finally:
        monitor.stop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        log_path = os.path.join(os.path.dirname(os.path.abspath(
            sys.executable if getattr(sys, "frozen", False) else __file__)),
            "youboard_error.log")
        with open(log_path, "w", encoding="utf-8") as f:
            traceback.print_exc(file=f)
        # Also show a message box if possible
        try:
            from PyQt6.QtWidgets import QApplication, QMessageBox
            app = QApplication.instance() or QApplication(sys.argv)
            QMessageBox.critical(None, "YouBoard Error",
                                 f"启动失败，详见:\n{log_path}\n\n{traceback.format_exc()[-500:]}")
        except Exception:
            pass
