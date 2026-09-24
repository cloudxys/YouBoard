#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
YouBoard v3.0.0 — 剪贴板历史管理器 / Clipboard History Manager
PyQt6 重构版：透明毛玻璃背景、QPropertyAnimation 动效、原生系统托盘。
"""

import ctypes
import gc
import hashlib
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
    QPlainTextEdit,
    QStyle, QProgressDialog, QProgressBar, QStyledItemDelegate,
    QStyleOptionViewItem, QStyleOptionHeader, QGridLayout, QSpinBox,
    QDateTimeEdit, QSizeGrip, QDoubleSpinBox,
    QColorDialog, QStackedWidget,
)
from PyQt6.QtCore import (
    Qt, QTimer, QPropertyAnimation, QEasingCurve, pyqtSignal,
    QThread, QObject, QSize, QRect, QRectF, QPoint, QPointF, QEvent,
    QAbstractNativeEventFilter, QUrl, QDateTime,
)
from PyQt6.QtGui import (
    QIcon, QPixmap, QImage, QPainter, QColor, QFont,
    QAction, QActionGroup, QKeySequence, QShortcut, QBrush, QPen,
    QPalette, QPainterPath, QCursor, QMovie, QTextDocument, QFontMetrics,
    QTextCharFormat, QTextCursor,
    QRegion,
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
    VaultStore, vault_image_path,
    set_clipboard_text, set_clipboard_image, set_clipboard_files,
    load_config, save_config, get_autostart, set_autostart,
    get_icon_path, get_app_icon,
    CONTENT_DIR, entry_full_text, read_external_head,
    find_installations, copy_installation_assets, read_foreign_history,
    normalize_tag, normalize_tags, entry_tags, entry_is_fav,
)
from youboard_phone import (
    PhoneTransferServer, get_lan_ip, get_lan_ips, make_qr_pil,
    pick_free_port,
)
from youboard_sync import (
    SyncError, GistSyncClient, WebDAVSyncClient,
    encrypt_bundle, decrypt_bundle, protect_secret, unprotect_secret,
)
from youboard_ai import (
    AIClient, AIError, AI_ACTIONS, AI_MAX_INPUT_CHARS, AI_PROVIDER_ORDER,
    PROVIDERS, ai_text, load_ai_settings, save_ai_settings, provider_label,
    provider_info, prepare_request, default_ai_settings,
    prepare_chat_context, build_chat_messages, AI_CHAT_MAX_TURNS,
    AI_IMAGE_ACTIONS, AI_FILE_ACTIONS, prepare_image_request,
    prepare_image_chat_context, file_entries_text,
    model_display_name,
)
# 版本号唯一来源：youboard_version.py（改版本只改那一个文件）
from youboard_version import APP_NAME, APP_VERSION
from youboard_bridge import (BridgeServer, ensure_bridge_config, conn_line,
                             DEFAULT_BRIDGE_PORT)

# ===========================================================================
# Constants
# ===========================================================================
LOGO_ICO = get_icon_path()
# 四个数据分类；「全部」是 3.1.0 新增的聚合标签，只用于界面展示
DATA_TYPES = ("text", "image", "file", "url")
TAB_TYPES = ("all", "text", "image", "file", "url")
# 表头文字的左内边距：内容列文字按这个值对齐表头（与 build_qss 里的
# QHeaderView::section padding 保持一致，改一处必须改另一处）
HEADER_TEXT_PAD = 10
DISPLAY_LIMIT = 400
HIST_DISPLAY = 60
# AI 结果弹层里，说明文字与结果区之间额外留的高度（往下挪一点，别贴着结果）
AI_STATUS_TOP_GAP = 12
# 文字预览一次性显示的字数上限：正常大小的文本都会完整显示（超过才提示截断）
PREVIEW_TEXT_LIMIT = 2000000
PREVIEW_MAX = 1600
TAB_ICONS = {"text": "\u270e", "image": "\u25a3", "file": "\u25a0", "url": "\u25c9"}
TAB_ICON_FILES = {"text": "wenben.ico", "image": "tupian.ico",
                  "file": "wenjian.ico", "url": "wangzhi.ico"}

# ---------------------------------------------------------------------------
# 文件细分：只在「文件」标签里按扩展名分类（视频 / 图片 / 设计源文件 / 音频 /
# 文档 / 压缩包 / 程序 / 代码 / 字体 / 其他）；「全部」标签里这些记录依旧是"文件"。
# ---------------------------------------------------------------------------
FILE_KINDS = ("video", "image", "design", "audio", "doc",
              "archive", "app", "code", "font", "other")
FILE_KIND_EXT = {
    "video": {
        "mp4", "m4v", "mkv", "avi", "mov", "wmv", "flv", "f4v", "webm", "mpg",
        "mpeg", "mpe", "m2v", "ts", "m2ts", "mts", "vob", "rm", "rmvb", "3gp",
        "3g2", "asf", "ogv", "divx", "xvid", "mxf", "swf", "dv", "wtv", "amv",
    },
    "image": {
        "jpg", "jpeg", "jpe", "jfif", "png", "gif", "bmp", "dib", "tif", "tiff",
        "webp", "heic", "heif", "avif", "ico", "icns", "tga", "pcx", "wmf",
        "emf", "svg", "dds", "exr", "hdr", "jp2", "j2k", "pbm", "pgm", "ppm",
        "xbm", "xpm", "raw", "cr2", "cr3", "nef", "arw", "dng", "orf", "rw2",
    },
    "design": {
        "psd", "psb", "ai", "eps", "indd", "indt", "indb", "xd", "fig",
        "sketch", "afdesign", "afphoto", "afpub", "cdr", "cdrx", "c4d", "blend",
        "blend1", "max", "3ds", "obj", "fbx", "stl", "dae", "glb", "gltf",
        "skp", "unitypackage", "uasset", "umap", "ase", "aseprite", "procreate",
        "xcf", "kra", "sai", "sai2", "clip", "csp", "ptg", "graffle", "dwg",
        "dxf", "step", "stp", "igs", "iges", "3dm", "sldprt", "sldasm", "f3d",
    },
    "audio": {
        "wav", "wave", "mp3", "flac", "aac", "m4a", "m4b", "ogg", "oga", "opus",
        "wma", "ape", "alac", "aiff", "aif", "aifc", "mid", "midi", "amr",
        "ac3", "dts", "mka", "au", "snd", "ra", "wv", "tta", "dsf", "dff",
        "caf", "aax", "m3u", "m3u8", "cue", "sf2", "sfz",
    },
    "doc": {
        "pdf", "doc", "docx", "docm", "dot", "dotx", "rtf", "txt", "md",
        "markdown", "csv", "tsv", "xls", "xlsx", "xlsm", "xlsb", "xlt", "xltx",
        "ppt", "pptx", "pptm", "pps", "ppsx", "pot", "potx", "odt", "ods",
        "odp", "odg", "epub", "mobi", "azw", "azw3", "djvu", "chm", "wps",
        "et", "dps", "pages", "numbers", "key", "tex", "bib", "log", "msg",
        "eml", "oft", "one", "vsdx", "vsd", "pub", "xmind", "mmap", "opml",
    },
    "archive": {
        "zip", "zipx", "rar", "r00", "r01", "7z", "tar", "gz", "tgz", "bz2",
        "tbz", "tbz2", "xz", "txz", "zst", "lz", "lzma", "lzh", "cab", "arj",
        "ace", "iso", "img", "vhd", "vhdx", "wim", "esd", "jar", "war", "ear",
        "cpio", "z", "001", "br", "lzo", "pak", "xar", "sit", "sitx", "gz2",
    },
    "app": {
        "exe", "msi", "msp", "mst", "msix", "appx", "appxbundle", "com", "scr",
        "bat", "cmd", "ps1", "psm1", "vbs", "vbe", "wsf", "wsh", "lnk", "url",
        "dll", "ocx", "sys", "drv", "cpl", "hta", "reg", "inf", "so", "dylib",
        "apk", "aab", "ipa", "deb", "rpm", "pkg", "dmg", "appimage", "snap",
        "flatpak", "run", "bin", "elf", "class", "pdb", "lib", "a", "o",
    },
    "code": {
        "py", "pyw", "pyi", "pyx", "ipynb", "js", "mjs", "cjs", "jsx", "ts",
        "tsx", "vue", "svelte", "java", "kt", "kts", "groovy", "scala", "c",
        "h", "cc", "cpp", "cxx", "hpp", "hh", "hxx", "cs", "go", "rs", "rb",
        "php", "pl", "pm", "lua", "swift", "m", "mm", "dart", "r", "jl", "hs",
        "erl", "ex", "exs", "clj", "lisp", "asm", "s", "sql", "sh", "bash",
        "zsh", "fish", "json", "json5", "jsonc", "xml", "yaml", "yml", "toml",
        "ini", "cfg", "conf", "config", "properties", "env", "html", "htm",
        "xhtml", "css", "scss", "sass", "less", "styl", "spec", "iss", "nsi",
        "nsh", "gradle", "cmake", "make", "mk", "dockerfile", "gitignore",
        "editorconfig", "diff", "patch", "rego", "tf", "tfvars", "proto",
    },
    "font": {
        "ttf", "ttc", "otf", "otc", "woff", "woff2", "eot", "fon", "fnt",
        "pfb", "pfm", "dfont", "suit", "bdf", "pcf",
    },
}


def file_kind_of_path(path):
    """单个文件属于哪个细分（看扩展名，认不出来就是 other）。"""
    ext = os.path.splitext(str(path or ""))[1].lower().lstrip(".")
    if not ext:
        name = os.path.basename(str(path or "")).lower()
        if name in ("dockerfile", "makefile", "cmakelists.txt", "gemfile"):
            return "code"
        return "other"
    for kind in FILE_KINDS:
        if ext in FILE_KIND_EXT.get(kind, ()):
            return kind
    return "other"


def file_kind_of_paths(paths):
    """一条记录（可能含多个文件）的细分：取数量最多的那一类，并列时按 FILE_KINDS 顺序。"""
    counts = {}
    for p in (paths or []):
        k = file_kind_of_path(p)
        counts[k] = counts.get(k, 0) + 1
    if not counts:
        return "other"
    return max(FILE_KINDS, key=lambda k: (counts.get(k, 0), -FILE_KINDS.index(k)))


def _all_tab_icon(size=18):
    """「全部」分类图标：运行时绘制的四宫格小方块（不新增资源文件）。"""
    try:
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(C.get("TEXT_SEC", "#c9d1d9")))
        cell = size / 2.0 - 1.0
        for dx in (0, 1):
            for dy in (0, 1):
                p.drawRoundedRect(
                    QRectF(dx * (size / 2.0), dy * (size / 2.0), cell, cell),
                    2.0, 2.0)
        p.end()
        return QIcon(pm)
    except Exception:
        return QIcon()


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


# ---------------------------------------------------------------------------
# 收藏星标：矢量绘制（不依赖字体是否有 ★ 字形；缺字体的机器上不会掉成方块）
# ---------------------------------------------------------------------------
_STAR_PM_CACHE = {}


def _star_path(rect):
    """正五角星路径：外接圆按短边算，10 个顶点在内 / 外半径间交替。"""
    r = min(rect.width(), rect.height()) / 2.0
    inner = r * 0.382
    cx, cy = rect.center().x(), rect.center().y()
    path = QPainterPath()
    for i in range(10):
        rad = r if i % 2 == 0 else inner
        ang = -math.pi / 2.0 + i * math.pi / 5.0
        x = cx + rad * math.cos(ang)
        y = cy + rad * math.sin(ang)
        if i == 0:
            path.moveTo(x, y)
        else:
            path.lineTo(x, y)
    path.closeSubpath()
    return path


def _star_pixmap(size=14, color=None, filled=True):
    """画一颗五角星（filled=实心 / 空心描边），结果带缓存。"""
    color = color or C.get("ACCENT", "#58c98a")
    key = (int(size), str(color), bool(filled))
    cached = _STAR_PM_CACHE.get(key)
    if cached is not None:
        return cached
    pm = QPixmap(int(size), int(size))
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    path = _star_path(QRectF(1.0, 1.0, float(size) - 2.0, float(size) - 2.0))
    if filled:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        painter.drawPath(path)
    else:
        pen = QPen(QColor(color))
        pen.setWidthF(1.4)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
    painter.end()
    _STAR_PM_CACHE[key] = pm
    return pm


def _star_icon(size=14, color=None, filled=True):
    return QIcon(_star_pixmap(size, color, filled))


class _InlineImageDelegate(QStyledItemDelegate):
    """内容列渲染：文本预览的内联换行图标 + 普通文本。

    关键点是起绘 x 统一取「同一列表头文字的左边缘」，这样表头「内容预览」
    和它下面的文字在「全部」「文本」等分类里都从同一个位置开始。
    """

    HTML_ROLE = Qt.ItemDataRole.UserRole + 1
    # 内容列开头的类型标签（只有「全部」分类用）
    BADGE_ROLE = Qt.ItemDataRole.UserRole + 2
    PIN_ROLE = Qt.ItemDataRole.UserRole + 3
    # 已收藏：内容前画一颗主题色星（在「置顶」胶囊之前）
    FAV_ROLE = Qt.ItemDataRole.UserRole + 4
    BADGE_GAP = 8
    BADGE_H = 18

    @staticmethod
    def _header_text_left(widget, column):
        """算出同一列表头文字的左边缘 x（拿不到时返回 None）。"""
        try:
            header = widget.horizontalHeader()
            item = widget.horizontalHeaderItem(column)
            opt = QStyleOptionHeader()
            opt.initFrom(header)
            opt.rect = QRect(header.sectionViewportPosition(column), 0,
                             header.sectionSize(column), header.height())
            opt.section = column
            opt.orientation = Qt.Orientation.Horizontal
            opt.text = item.text() if item is not None else ""
            if item is not None:
                # PyQt6 下 textAlignment() 返回 int，需要转回枚举
                opt.textAlignment = Qt.AlignmentFlag(int(item.textAlignment()))
            else:
                opt.textAlignment = (Qt.AlignmentFlag.AlignLeft |
                                     Qt.AlignmentFlag.AlignVCenter)
            rect = header.style().subElementRect(
                QStyle.SubElement.SE_HeaderLabel, opt, header)
            # 该矩形已经包含 QSS 表头的左内边距，直接作为文字起绘位置
            return rect.left()
        except Exception:
            return None

    def paint(self, painter, option, index):
        html = index.data(self.HTML_ROLE)
        widget = option.widget
        target_left = (self._header_text_left(widget, index.column())
                       if widget is not None else None)
        if not html and target_left is None:
            opt = QStyleOptionViewItem(option)
            # 去掉“获得焦点”状态：否则单元格文字外面会出现一圈细直角框
            opt.state &= ~QStyle.StateFlag.State_HasFocus
            super().paint(painter, opt, index)
            return
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.state &= ~QStyle.StateFlag.State_HasFocus
        display = opt.text or ""
        opt.text = ""
        opt.icon = QIcon()
        style = widget.style() if widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem,
                          opt, painter, widget)
        if target_left is None:
            target_left = option.rect.left() + 3
        left = target_left
        width = max(1, option.rect.left() + option.rect.width() - left)
        painter.save()
        fav = index.data(self.FAV_ROLE)
        if fav:
            # 收藏标记：一颗主题色五角星（矢量画，缺字体的机器也不会掉成方块）
            _s = 13
            _pm = _star_pixmap(_s)
            painter.drawPixmap(
                int(left) + 1,
                int(option.rect.top() + (option.rect.height() - _s) / 2.0),
                _pm)
            left += _s + 5
            width = max(1, option.rect.left() + option.rect.width() - left)
        pin = index.data(self.PIN_ROLE)
        if pin:
            # 置顶标记：主题色小胶囊，画在类型/细分标签前面，一眼能看出来
            _fm = QFontMetrics(opt.font)
            _pw = _fm.horizontalAdvance(str(pin)) + 16
            _ph = min(self.BADGE_H, max(12, option.rect.height() - 8))
            _py = option.rect.top() + (option.rect.height() - _ph) / 2.0
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(C['ACCENT']))
            painter.drawRoundedRect(
                QRectF(float(left), float(_py), float(_pw), float(_ph)),
                _ph / 2.0, _ph / 2.0)
            painter.setPen(QPen(QColor(_on_accent_color())))
            painter.drawText(
                QRectF(float(left), float(_py), float(_pw), float(_ph)),
                Qt.AlignmentFlag.AlignCenter, str(pin))
            left += _pw + self.BADGE_GAP
            width = max(1, option.rect.left() + option.rect.width() - left)
        badge = index.data(self.BADGE_ROLE)
        if badge:
            # 圆角实心标签：半透明、比行底色更深一点，文字用次要色
            fm = QFontMetrics(opt.font)
            bw = fm.horizontalAdvance(str(badge)) + 16
            bh = min(self.BADGE_H, max(12, option.rect.height() - 8))
            by = option.rect.top() + (option.rect.height() - bh) / 2.0
            badge_bg = (QColor(0, 0, 0, 42) if _is_light_theme()
                        else QColor(0, 0, 0, 104))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(badge_bg)
            painter.drawRoundedRect(QRectF(float(left), float(by),
                                          float(bw), float(bh)),
                                   bh / 2.0, bh / 2.0)
            painter.setPen(QPen(QColor(C['TEXT_SEC'])))
            painter.drawText(QRectF(float(left), float(by), float(bw),
                                    float(bh)),
                             Qt.AlignmentFlag.AlignCenter, str(badge))
            left += bw + self.BADGE_GAP
            width = max(1, option.rect.left() + option.rect.width() - left)
        if html:
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
            y = option.rect.top() + max(
                0.0, (option.rect.height() - doc_h) / 2.0)
            painter.translate(left, y)
            doc.drawContents(painter, QRectF(0, 0, float(width),
                                             float(option.rect.height())))
        else:
            # 普通文本：与表头对齐、按列宽省略
            metrics = QFontMetrics(opt.font)
            elided = metrics.elidedText(display, Qt.TextElideMode.ElideRight,
                                        width)
            color = opt.palette.color(
                QPalette.ColorRole.HighlightedText
                if (opt.state & QStyle.StateFlag.State_Selected)
                else QPalette.ColorRole.Text)
            painter.setPen(QPen(color))
            painter.drawText(
                QRect(left, option.rect.top(), width, option.rect.height()),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                elided)
        painter.restore()


class _NoFocusDelegate(QStyledItemDelegate):
    """普通单元格同样去掉焦点框（点选后不要文字外那圈细直角框）。"""

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        opt.state &= ~QStyle.StateFlag.State_HasFocus
        super().paint(painter, opt, index)


class _Table(QTableWidget):
    """列表控件：选中/拖选时不要横向乱跳。

    内容列按内容撑开后非常宽，Qt 默认会在选中某格时调用 scrollTo() 试图把整格
    露出来，于是拖选多行时视图会突然横向跳到右边。这里保持横向位置不动。
    """

    _owner = None

    def scrollTo(self, index, hint=QAbstractItemView.ScrollHint.EnsureVisible):
        bar = self.horizontalScrollBar()
        keep = bar.value()
        super().scrollTo(index, hint)
        if bar.value() != keep:
            bar.setValue(keep)

    def keyPressEvent(self, event):
        """Ctrl+C / Ctrl+Insert：复制"选中记录的完整内容"。

        Qt 默认会把「当前单元格里的文字」放进剪贴板——而内容预览列显示的是
        预览文字（换行被写成 ⏎、超长还会截断加 …），这样复制出去的内容既不完整，
        还会因为内容不同被记成一条新记录（看起来就是"重复"）。
        """
        is_copy = event.matches(QKeySequence.StandardKey.Copy) or (
            event.key() == Qt.Key.Key_Insert
            and bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier))
        if is_copy and self._owner is not None:
            try:
                self._owner._copy_selected()
            except Exception:
                pass
            event.accept()
            return
        super().keyPressEvent(event)


class _RoundMenu(QMenu):
    """圆角弹层：四角裁成圆角，角外不绘制（透出后面的桌面，不会留方块底）。"""

    RADIUS = 9

    def __init__(self, *args):
        super().__init__(*args)
        self.setWindowFlag(Qt.WindowType.NoDropShadowWindowHint, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def showEvent(self, event):
        super().showEvent(event)
        _round_window_corners(self, self.RADIUS)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        _round_window_corners(self, self.RADIUS)


def _round_window_corners(widget, radius=9):
    """把顶层弹层的四角裁圆，角外完全不绘制，避免露出一块方块底。

    一律用物理像素的圆角区域把窗口四角裁掉：这样无论系统的分层透明是否生效，
    圆角面板之外都不会再画东西，也就不可能露出方块底。
    """
    try:
        if IS_WIN and widget.isWindow():
            hwnd = int(widget.winId())
            try:
                dpr = float(widget.devicePixelRatioF()) or 1.0
            except Exception:
                dpr = 1.0
            w = max(1, int(round(widget.width() * dpr)))
            h = max(1, int(round(widget.height() * dpr)))
            d = max(2, int(round(radius * dpr)) * 2)
            rgn = ctypes.windll.gdi32.CreateRoundRectRgn(0, 0, w + 1, h + 1, d, d)
            if rgn:
                ctypes.windll.user32.SetWindowRgn(hwnd, rgn, True)
                return
    except Exception:
        pass
    try:
        path = QPainterPath()
        path.addRoundedRect(QRectF(widget.rect()), float(radius), float(radius))
        widget.setMask(QRegion(path.toFillPolygon().toPolygon()))
    except Exception:
        pass


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


def _lock_icon(size=20, color=None):
    """密库用的挂锁图标：随主题色现画（不额外往 res/ 里加图片资源）。"""
    color = color or C["TEXT"]
    pm = QPixmap(int(size), int(size))
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = float(size)
    # 锁梁（上半圆环）
    pen = QPen(QColor(color), max(1.4, s * 0.11))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    shackle_w = s * 0.46
    p.drawArc(QRectF((s - shackle_w) / 2.0, s * 0.13, shackle_w, s * 0.44),
              0, 180 * 16)
    # 锁体
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    p.drawRoundedRect(QRectF(s * 0.18, s * 0.42, s * 0.64, s * 0.44),
                      s * 0.13, s * 0.13)
    p.end()
    return QIcon(pm)


_VAULT_LOCK_PM_CACHE = {}


def _vault_lock_pixmap(size=64):
    """密库的挂锁图标：优先用 res/suo.png（用户给的图片），没有才退回现画的那个。"""
    size = int(size)
    cached = _VAULT_LOCK_PM_CACHE.get(size)
    if cached is not None:
        return cached
    pm = QPixmap(_res_icon("suo.png"))
    if pm.isNull():
        pm = _lock_icon(size).pixmap(size, size)
    else:
        pm = pm.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                       Qt.TransformationMode.SmoothTransformation)
    _VAULT_LOCK_PM_CACHE[size] = pm
    return pm


_EYE_PM_CACHE = {}


def _eye_pixmap(size=16, color=None, open_=True):
    """密码框右侧"显示 / 隐藏"的小眼睛（点开看明文，再点回隐藏）。"""
    color = color or C.get("TEXT_SEC", "#aeb5bd")
    key = (int(size), str(color), bool(open_))
    cached = _EYE_PM_CACHE.get(key)
    if cached is not None:
        return cached
    s = float(size)
    pm = QPixmap(int(size), int(size))
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color))
    pen.setWidthF(max(1.2, s * 0.09))
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath()
    path.moveTo(s * 0.08, s * 0.5)
    path.cubicTo(s * 0.30, s * 0.16, s * 0.70, s * 0.16, s * 0.92, s * 0.5)
    path.cubicTo(s * 0.70, s * 0.84, s * 0.30, s * 0.84, s * 0.08, s * 0.5)
    p.drawPath(path)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    r = s * 0.15
    p.drawEllipse(QPointF(s * 0.5, s * 0.5), r, r)
    if not open_:
        slash = QPen(QColor(color))
        slash.setWidthF(max(1.4, s * 0.1))
        slash.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(slash)
        p.drawLine(QPointF(s * 0.16, s * 0.86), QPointF(s * 0.84, s * 0.14))
    p.end()
    _EYE_PM_CACHE[key] = pm
    return pm


def _attach_password_toggle(edit):
    """给密码框右侧挂一个"显示 / 隐藏"按钮（点一下在明文和圆点之间切换）。"""
    def _toggle(*_a):
        show = edit.echoMode() == QLineEdit.EchoMode.Password
        edit.setEchoMode(QLineEdit.EchoMode.Normal if show
                         else QLineEdit.EchoMode.Password)
        act.setIcon(QIcon(_eye_pixmap(16, open_=not show)))
        act.setToolTip(tr("vault_pw_hide") if show else tr("vault_pw_show"))

    act = edit.addAction(QIcon(_eye_pixmap(16, open_=False)),
                         QLineEdit.ActionPosition.TrailingPosition)
    act.setToolTip(tr("vault_pw_show"))
    act.triggered.connect(_toggle)
    return act


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
    "BG": "#15171b", "SURFACE": "#1d1f24", "SURFACE2": "#26292f",
    "SURFACE3": "#30343b", "ROW_ALT": "#1b1d21", "INPUT_BG": "#111316",
    "BORDER": "#3a4049", "BORDER_LT": "#4c535e",
    "TEXT": "#f1f3f5", "TEXT_SEC": "#aeb5bd", "TEXT_MUTED": "#737b86",
    "ACCENT": "#36bdf7", "ACCENT_HV": "#62cdff", "ACCENT_DIM": "#153849",
    "TEAL": "#43d6c2", "AMBER": "#f4b84d", "PIN_BG": "#3a3118",
    "DANGER": "#f0645b", "SUCCESS": "#43d17a", "FLASH_BG": "#143a2d",
    "PANEL_ALPHA": "rgba(29, 31, 36, 252)",
    "PANEL_ALPHA2": "rgba(38, 41, 47, 250)",
    "HEADER_ALPHA": "rgba(19, 20, 23, 250)",
    "DIALOG_BG": "#24272d",
    "DIALOG_EDGE": "#5c6673",
    # 有自定义背景时才启用：内容区 / 卡片 / 顶栏的半透明底色（让壁纸透出来）
    "GLASS_PANE": "rgba(29, 31, 36, 132)",
    "GLASS_CARD": "rgba(29, 31, 36, 146)",
    "GLASS_HEADER": "rgba(19, 20, 23, 140)",
    "GLASS_INPUT": "rgba(17, 19, 22, 150)",
    "GLASS_BUTTON": "rgba(38, 41, 47, 152)",
    "GLASS_ROW_ALT": "rgba(0, 0, 0, 66)",
}

THEME_LIGHT = {
    "BG": "#f3f4f6", "SURFACE": "#ffffff", "SURFACE2": "#eceef1",
    "SURFACE3": "#dfe3e8", "ROW_ALT": "#f7f8fa", "INPUT_BG": "#f7f8fa",
    "BORDER": "#cbd0d8", "BORDER_LT": "#aeb6c1",
    "TEXT": "#17191d", "TEXT_SEC": "#4b515a", "TEXT_MUTED": "#7d848e",
    "ACCENT": "#0b83d8", "ACCENT_HV": "#0a6fb8", "ACCENT_DIM": "#d9edfb",
    "TEAL": "#0f9f8f", "AMBER": "#b45309", "PIN_BG": "#fff3c4",
    "DANGER": "#d92d20", "SUCCESS": "#178a45", "FLASH_BG": "#d8f5e4",
    "PANEL_ALPHA": "rgba(255, 255, 255, 252)",
    "PANEL_ALPHA2": "rgba(248, 249, 251, 250)",
    "HEADER_ALPHA": "rgba(255, 255, 255, 252)",
    "DIALOG_BG": "#ffffff",
    "DIALOG_EDGE": "#98a2b0",
    "GLASS_PANE": "rgba(255, 255, 255, 128)",
    "GLASS_CARD": "rgba(255, 255, 255, 142)",
    "GLASS_HEADER": "rgba(255, 255, 255, 136)",
    "GLASS_INPUT": "rgba(247, 248, 250, 146)",
    "GLASS_BUTTON": "rgba(236, 238, 241, 148)",
    "GLASS_ROW_ALT": "rgba(0, 0, 0, 22)",
}

# ---------------------------------------------------------------------------
# 皮肤（和暗色 / 亮色同级，只能选一种）
#   核心色在下面声明，其余（半透明面板 / 弹窗 / 玻璃层）由核心色推导，
#   避免漏配某个键导致某处看不清。
# ---------------------------------------------------------------------------


def _hex_rgb(color):
    c = QColor(color)
    return c.red(), c.green(), c.blue()


def _mix(color_a, color_b, ratio):
    """按比例把 color_a 混向 color_b（ratio=0 全是 a，1 全是 b）。"""
    r1, g1, b1 = _hex_rgb(color_a)
    r2, g2, b2 = _hex_rgb(color_b)
    r = int(round(r1 + (r2 - r1) * ratio))
    g = int(round(g1 + (g2 - g1) * ratio))
    b = int(round(b1 + (b2 - b1) * ratio))
    return "#%02x%02x%02x" % (max(0, min(255, r)),
                              max(0, min(255, g)),
                              max(0, min(255, b)))


def _rgba(color, alpha):
    r, g, b = _hex_rgb(color)
    return f"rgba({r}, {g}, {b}, {alpha})"


def _luminance(color):
    r, g, b = _hex_rgb(color)

    def _ch(v):
        v = v / 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    return 0.2126 * _ch(r) + 0.7152 * _ch(g) + 0.0722 * _ch(b)


def _contrast_ratio(color_a, color_b):
    la, lb = _luminance(color_a), _luminance(color_b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _on_accent_color(accent=None):
    """压在强调色上的文字色：保证对比度够。"""
    accent = accent or C.get("ACCENT", "#0b83d8")
    return "#0c1420" if _luminance(accent) > 0.5 else "#ffffff"


# 每套皮肤只需要声明核心色，半透明/弹窗/玻璃层会由这些颜色推出来
_SKIN_SPECS = (
    {
        "id": "beige", "light": True,
        "colors": {
            "BG": "#f4eee0", "SURFACE": "#fffaf0", "SURFACE2": "#efe7d6",
            "SURFACE3": "#e2d7bf", "ROW_ALT": "#faf4e8", "INPUT_BG": "#fffdf7",
            "BORDER": "#d5c8ac", "BORDER_LT": "#b9a781",
            "TEXT": "#2e2a1f", "TEXT_SEC": "#584f3c", "TEXT_MUTED": "#857a62",
            "ACCENT": "#9a6516", "ACCENT_HV": "#b87a1c", "ACCENT_DIM": "#ecdfc0",
            "TEAL": "#1a7a6c", "AMBER": "#9a5b06", "PIN_BG": "#f7e7ba",
            "DANGER": "#b3261e", "SUCCESS": "#247a45", "FLASH_BG": "#dcefd9",
        },
    },
    {
        "id": "ocean", "light": False,
        "colors": {
            "BG": "#0a1524", "SURFACE": "#0f2039", "SURFACE2": "#152f4f",
            "SURFACE3": "#1d3d63", "ROW_ALT": "#0d1c30", "INPUT_BG": "#08121f",
            "BORDER": "#2a4a72", "BORDER_LT": "#3d6faf",
            "TEXT": "#eaf2ff", "TEXT_SEC": "#a9c0dd", "TEXT_MUTED": "#7791b3",
            "ACCENT": "#4da3ff", "ACCENT_HV": "#7cbcff", "ACCENT_DIM": "#16395c",
            "TEAL": "#35d0c0", "AMBER": "#f2b24a", "PIN_BG": "#33290f",
            "DANGER": "#ff6b61", "SUCCESS": "#3fd07f", "FLASH_BG": "#10352c",
        },
    },
    {
        "id": "violet", "light": False,
        "colors": {
            "BG": "#150f20", "SURFACE": "#1e1630", "SURFACE2": "#2a2042",
            "SURFACE3": "#372a56", "ROW_ALT": "#191129", "INPUT_BG": "#100a1a",
            "BORDER": "#443262", "BORDER_LT": "#5f4787",
            "TEXT": "#f4eeff", "TEXT_SEC": "#c0b0dd", "TEXT_MUTED": "#8b7bab",
            "ACCENT": "#b18cff", "ACCENT_HV": "#c9adff", "ACCENT_DIM": "#33254f",
            "TEAL": "#43d6c2", "AMBER": "#f4b84d", "PIN_BG": "#332a12",
            "DANGER": "#f0645b", "SUCCESS": "#4cd88a", "FLASH_BG": "#153a2e",
        },
    },
    {
        "id": "forest", "light": False,
        "colors": {
            "BG": "#0e1a13", "SURFACE": "#15241b", "SURFACE2": "#1d3226",
            "SURFACE3": "#274232", "ROW_ALT": "#122016", "INPUT_BG": "#0a140e",
            "BORDER": "#2c4a36", "BORDER_LT": "#406a4d",
            "TEXT": "#e9f6ec", "TEXT_SEC": "#a9c8b1", "TEXT_MUTED": "#75937d",
            "ACCENT": "#58c98a", "ACCENT_HV": "#7fdcab", "ACCENT_DIM": "#1d3b2a",
            "TEAL": "#3fd0c0", "AMBER": "#e0b04a", "PIN_BG": "#2f2a12",
            "DANGER": "#f27a6f", "SUCCESS": "#4ed07e", "FLASH_BG": "#153a2b",
        },
    },
    {
        "id": "contrast", "light": False,
        "colors": {
            "BG": "#000000", "SURFACE": "#0b0b0b", "SURFACE2": "#161616",
            "SURFACE3": "#212121", "ROW_ALT": "#0d0d0d", "INPUT_BG": "#050505",
            "BORDER": "#6d6d6d", "BORDER_LT": "#9c9c9c",
            "TEXT": "#ffffff", "TEXT_SEC": "#e6e6e6", "TEXT_MUTED": "#bdbdbd",
            "ACCENT": "#ffd400", "ACCENT_HV": "#ffe14d", "ACCENT_DIM": "#3a3300",
            "TEAL": "#00e5d0", "AMBER": "#ffb300", "PIN_BG": "#3a3300",
            "DANGER": "#ff5a52", "SUCCESS": "#4dff88", "FLASH_BG": "#0d3a22",
        },
    },
)
THEME_SKINS = {spec["id"]: spec for spec in _SKIN_SPECS}
CUSTOM_SKIN_ID = "custom"
# 自定义皮肤的默认配色（深青蓝，颜色可自己改）
_CUSTOM_SKIN_DEFAULTS = {
    "skin_custom_bg": "#0f1a1d",
    "skin_custom_surface": "#16262b",
    "skin_custom_text": "#eaf5f7",
    "skin_custom_accent": "#2fb3a0",
}


def _build_palette(base_name, colors):
    """按核心色补全整套调色板（半透明层、弹窗、玻璃层都由核心色推导）。"""
    palette = dict(THEME_LIGHT if base_name == "light" else THEME_DARK)
    palette.update(colors)
    palette["PANEL_ALPHA"] = _rgba(palette["SURFACE"], 252)
    palette["PANEL_ALPHA2"] = _rgba(palette["SURFACE2"], 250)
    palette["HEADER_ALPHA"] = _rgba(palette["BG"], 250)
    palette["DIALOG_BG"] = palette["SURFACE"]
    palette["DIALOG_EDGE"] = palette["BORDER_LT"]
    palette["GLASS_PANE"] = _rgba(palette["SURFACE"], 132)
    palette["GLASS_CARD"] = _rgba(palette["SURFACE"], 146)
    palette["GLASS_HEADER"] = _rgba(palette["BG"], 140)
    palette["GLASS_INPUT"] = _rgba(palette["INPUT_BG"], 150)
    palette["GLASS_BUTTON"] = _rgba(palette["SURFACE2"], 152)
    palette["GLASS_ROW_ALT"] = ("rgba(0, 0, 0, 22)" if base_name == "light"
                                else "rgba(0, 0, 0, 66)")
    return palette


def _custom_skin_colors(cfg=None):
    """读取自定义皮肤配色，并保证正文对比度足够（不够就自动换成黑/白）。"""
    cfg = cfg if isinstance(cfg, dict) else load_config()
    colors = {}
    for key, default in _CUSTOM_SKIN_DEFAULTS.items():
        value = str(cfg.get(key, default) or default)
        colors[key] = value if QColor(value).isValid() else default
    bg = colors["skin_custom_bg"]
    surface = colors["skin_custom_surface"]
    text = colors["skin_custom_text"]
    accent = colors["skin_custom_accent"]
    if _contrast_ratio(text, surface) < 3.0:
        text = "#111111" if _luminance(surface) > 0.5 else "#f6f8f8"
    text_sec = _mix(text, surface, 0.25)
    if _contrast_ratio(text_sec, surface) < 2.5:
        text_sec = _mix(text, surface, 0.12)
    text_muted = _mix(text, surface, 0.45)
    if _contrast_ratio(text_muted, surface) < 2.0:
        text_muted = _mix(text, surface, 0.3)
    light = _luminance(bg) > 0.5
    return {
        "light": light,
        "colors": {
            "BG": bg, "SURFACE": surface,
            "SURFACE2": _mix(surface, bg, 0.45),
            "SURFACE3": _mix(surface, text, 0.16),
            "ROW_ALT": _mix(bg, surface, 0.35),
            "INPUT_BG": _mix(bg, surface, 0.2),
            "BORDER": _mix(surface, text, 0.26),
            "BORDER_LT": _mix(surface, text, 0.42),
            "TEXT": text, "TEXT_SEC": text_sec, "TEXT_MUTED": text_muted,
            "ACCENT": accent,
            "ACCENT_HV": (_mix(accent, "#000000", 0.18) if light
                          else _mix(accent, "#ffffff", 0.22)),
            "ACCENT_DIM": _mix(surface, accent, 0.28),
            "TEAL": _mix(accent, "#00b3a0", 0.5),
            "AMBER": "#9a5b06" if light else "#f4b84d",
            "PIN_BG": _mix(surface, "#ffd400", 0.22),
            "DANGER": "#b3261e" if light else "#f0645b",
            "SUCCESS": "#1c7a45" if light else "#43d17a",
            "FLASH_BG": _mix(surface, "#3fd07f", 0.22),
        },
    }


def resolve_palette(name="dark"):
    """把主题名解析成完整调色板：dark / light / 皮肤 id / custom。"""
    name = str(name or "dark")
    if name in ("dark", "light"):
        return dict(THEME_LIGHT if name == "light" else THEME_DARK)
    skin = THEME_SKINS.get(name)
    if skin is not None:
        return _build_palette("light" if skin["light"] else "dark",
                              skin["colors"])
    if name == CUSTOM_SKIN_ID:
        custom = _custom_skin_colors()
        return _build_palette("light" if custom["light"] else "dark",
                              custom["colors"])
    return dict(THEME_DARK)


def _skin_swatch(skin_id):
    """皮肤小色块：上半是面板色、下半是强调色，用来一眼区分皮肤。"""
    palette = resolve_palette(skin_id)
    pm = QPixmap(14, 14)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0.5, 0.5, 13.0, 13.0), 4.0, 4.0)
    p.fillPath(path, qcolor(palette["SURFACE"]))
    p.setClipPath(path)
    p.fillRect(QRectF(0.0, 7.0, 14.0, 7.0), qcolor(palette["ACCENT"]))
    p.setClipping(False)
    p.setPen(QPen(qcolor(palette["BORDER_LT"]), 1.0))
    p.drawPath(path)
    p.end()
    return pm


def _skin_preview_pixmap(skin_id, w=112, h=44):
    """皮肤预览小图：模拟一小块界面（底色 + 侧栏 + 面板 + 文字条 + 强调色）。"""
    palette = resolve_palette(skin_id)
    pm = QPixmap(w, h)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0.5, 0.5, w - 1.0, h - 1.0), 6.0, 6.0)
    p.setClipPath(path)
    p.fillPath(path, qcolor(palette["BG"]))
    p.fillRect(QRectF(0, 0, w, 9), qcolor(palette["SURFACE"]))
    p.fillRect(QRectF(0, 0, 11, h), qcolor(palette["SURFACE2"]))
    p.fillRect(QRectF(16, 14, w - 22, h - 19), qcolor(palette["SURFACE"]))
    p.fillRect(QRectF(21, 19, (w - 32) * 0.72, 3), qcolor(palette["TEXT"]))
    p.fillRect(QRectF(21, 25, (w - 32) * 0.45, 3), qcolor(palette["TEXT_SEC"]))
    p.fillRect(QRectF(21, h - 12, 17, 6), qcolor(palette["ACCENT"]))
    p.setClipping(False)
    p.setPen(QPen(qcolor(palette["BORDER_LT"]), 1.0))
    p.drawPath(path)
    p.end()
    return pm


C = {}
_THEME_IS_LIGHT = False


def apply_theme(name="dark"):
    """Set the active theme palette into global C dict."""
    global C, _THEME_IS_LIGHT
    name = str(name or "dark")
    if name in ("dark", "light"):
        _THEME_IS_LIGHT = (name == "light")
    elif name in THEME_SKINS:
        _THEME_IS_LIGHT = bool(THEME_SKINS[name]["light"])
    elif name == CUSTOM_SKIN_ID:
        _THEME_IS_LIGHT = bool(_custom_skin_colors()["light"])
    else:
        _THEME_IS_LIGHT = False
    C = resolve_palette(name)


apply_theme(load_config().get("theme", "dark"))


# ---------------------------------------------------------------------------
# 自定义背景（壁纸）：只有真正设置了背景图时才让面板半透明，其它情况观感完全不变
# ---------------------------------------------------------------------------
_GLASS_ACTIVE = False


def set_glass_active(flag):
    global _GLASS_ACTIVE
    _GLASS_ACTIVE = bool(flag)


def is_glass_active():
    return _GLASS_ACTIVE


def bg_configured(cfg=None):
    """配置里是否有一张真实存在的背景图。"""
    try:
        cfg = cfg if isinstance(cfg, dict) else load_config()
        path = (cfg.get("bg_image") or "").strip()
        return bool(path) and os.path.exists(path)
    except Exception:
        return False


def surface_bg(key="SURFACE", glass_key="GLASS_PANE"):
    """面板底色：有背景图时取半透明色（壁纸透出），否则用原来的实色。"""
    if _GLASS_ACTIVE:
        return C.get(glass_key, C.get("PANEL_ALPHA", C[key]))
    return C[key]


def qcolor(spec):
    """把主题色字符串转成 QColor：支持 "#rrggbb" 与 "rgba(r, g, b, a)"。

    注意：QColor("rgba(...)") 在 Qt 里是无效色，直接喂给 QPainter 会画成黑色，
    所以这里手动解析。
    """
    s = str(spec).strip()
    if s.startswith("rgba(") and s.endswith(")"):
        parts = [p.strip() for p in s[5:-1].split(",")]
        if len(parts) == 4:
            try:
                return QColor(int(float(parts[0])), int(float(parts[1])),
                              int(float(parts[2])), int(float(parts[3])))
            except ValueError:
                pass
    return QColor(s)


def glass_transparent():
    """透明底色：Qt 对关键字 transparent 的处理不可靠，显式用 rgba 全透明。"""
    return "rgba(0, 0, 0, 0)"


def apply_global_palette(name="dark"):
    """把 QApplication 的整体调色板也设为主题色。

    QComboBox 的弹出浮层（以及部分系统控件）不看 QSS，而是依赖 QPalette。
    不设的话它们会沿用系统浅色，导致下拉弹层出现白底。这里统一按主题配色。
    """
    app = QApplication.instance()
    if app is None:
        return
    c = resolve_palette(name)
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
    return bool(_THEME_IS_LIGHT)


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
    # 有自定义背景时内容区/标签栏用半透明底色，让壁纸透出来
    pane_bg = c['GLASS_PANE'] if _GLASS_ACTIVE else c['SURFACE']
    pane_radius = "0" if flush else "10px"
    flush_rules = ""
    # 有背景时，滚动区域/列表的 viewport 也要透明，否则会把壁纸整块挡住
    glass_rules = "" if not _GLASS_ACTIVE else """
    QWidget#qt_scrollarea_viewport, QWidget#qt_scrollarea_vcontainer,
    QWidget#qt_scrollarea_hcontainer { background: rgba(0, 0, 0, 0); }
    QScrollArea, QAbstractScrollArea { background: rgba(0, 0, 0, 0); }
    QScrollArea > QWidget > QWidget { background: rgba(0, 0, 0, 0); }
    QAbstractItemView { background: rgba(0, 0, 0, 0); }
    QListWidget, QListView, QTreeView, QTableView, QTableWidget,
    QStackedWidget { background: rgba(0, 0, 0, 0); }
    QTableWidget, QTableView, QTreeView {
        alternate-background-color: %s; }
    QLineEdit, QComboBox, QSpinBox, QDateTimeEdit, QTextEdit, QPlainTextEdit {
        background: %s; }
    QPushButton { background: %s; }
    QCheckBox::indicator { background: %s; }
""" % (c['GLASS_ROW_ALT'], c['GLASS_INPUT'], c['GLASS_BUTTON'], c['GLASS_INPUT'])
    return f"""
    /* 无边框窗口：窗体底色留出 1px 当描边，和桌面区分开（否则浅色主题下边界看不清） */
    QMainWindow {{ background-color: {c['BORDER_LT']}; }}
    QDialog {{ background-color: {c['BG']}; }}
    QWidget#rootSurface {{ background-color: {c['BG']}; }}
    QWidget {{ color: {c['TEXT']}; font-family: "Microsoft YaHei UI","Segoe UI",sans-serif; font-size: 13px; }}
    QToolTip {{ background: {c['SURFACE']}; color: {c['TEXT']};
        border: 1px solid {c['BORDER_LT']}; padding: 6px 9px; border-radius: 6px; }}
    QTabWidget {{ background: {pane_bg}; }}
    QTabWidget::tab-bar {{ left: 0; background: {pane_bg}; }}
    QTabWidget::pane {{ border: 1px solid {c['BORDER_LT']};
        background: {pane_bg}; border-radius: {pane_radius}; }}
    QTabBar {{ background: {pane_bg}; qproperty-drawBase: 0; }}
    QTabBar::tab {{ background: transparent; color: {c['TEXT_MUTED']}; padding: 10px 18px;
        margin: 0 3px; border: none; border-bottom: 2px solid transparent; font-weight: 600; }}
    QTabBar::tab:selected {{ color: {c['TEXT']}; border-bottom-color: {c['ACCENT']}; }}
    QTabBar::tab:hover:!selected {{ color: {c['TEXT_SEC']}; }}
    QTableWidget {{ background: transparent; alternate-background-color: {c['ROW_ALT']};
        border: none; gridline-color: transparent;
        selection-background-color: {c['ACCENT_DIM']}; selection-color: {c['TEXT']}; }}
    QTableWidget::item {{ padding: 8px 10px; border-bottom: 1px solid {c['BORDER']}; }}
    /* 横竖滚动条交汇处的小方块（角控件）不要 */
    QAbstractScrollArea::corner {{ background: transparent; border: none; }}
            QHeaderView::section {{ background: transparent; color: {c['TEXT_MUTED']};
                padding: 7px {HEADER_TEXT_PAD}px; border: none; border-bottom: 1px solid {c['BORDER']};
        font-weight: 600; font-size: 11px; }}
    QPushButton {{ background: {c['SURFACE2']}; color: {c['TEXT_SEC']}; border: 1px solid transparent;
        border-radius: 8px; padding: 7px 14px; font-size: 12px; font-weight: 600; }}
    QPushButton:hover {{ background: {c['SURFACE3']}; color: {c['TEXT']}; border-color: {c['BORDER_LT']}; }}
    QPushButton:pressed {{ background: {c['BORDER']}; color: {c['TEXT']}; }}
    QPushButton[cssClass="accent"] {{ background: {c['ACCENT']}; color: #ffffff;
        border: none; font-weight: 700; }}
    QPushButton[cssClass="accent"]:hover {{ background: {c['ACCENT_HV']}; }}
    QPushButton[cssClass="danger"] {{ color: {c['DANGER']}; border-color: {c['DANGER']}; }}
    QPushButton[cssClass="danger"]:hover {{ background: {c['DANGER']}; color: #ffffff; }}
    QLineEdit {{ background: {c['INPUT_BG']}; color: {c['TEXT']}; border: 1px solid {c['BORDER']};
        border-radius: 8px; padding: 8px 11px; font-size: 12px; }}
    QLineEdit:focus {{ border-color: {c['ACCENT']}; }}
    QComboBox, QSpinBox, QDateTimeEdit {{ background: {c['INPUT_BG']}; color: {c['TEXT_SEC']};
        border: 1px solid {c['BORDER']}; border-radius: 8px; padding: 6px 10px; font-size: 12px; }}
    QComboBox:focus, QSpinBox:focus, QDateTimeEdit:focus {{ border-color: {c['ACCENT']}; }}
    QComboBox::drop-down {{ border: none; width: 20px; }}
    QComboBox QAbstractItemView {{ background: {c['SURFACE']}; color: {c['TEXT']};
        selection-background-color: {c['ACCENT_DIM']}; border: 1px solid {c['BORDER_LT']};
        border-radius: 8px; padding: 4px; outline: none; }}
    QSplitter::handle {{ background: {c['BORDER']}; width: 2px; height: 2px; }}
    QLabel {{ background: transparent; color: {c['TEXT']}; }}
    QTextEdit, QPlainTextEdit {{ background: transparent; color: {c['TEXT']}; border: none;
        font-family: "Consolas","Microsoft YaHei UI",monospace; font-size: 12px; }}
    QListWidget {{ background: transparent; border: none; font-size: 11px; outline: none; }}
    QListWidget::item {{ padding: 8px 10px; margin: 1px 2px; border-radius: 7px;
        color: {c['TEXT_SEC']}; background: transparent; }}
    QListWidget::item:selected {{ background: {c['ACCENT_DIM']}; color: {c['TEXT']}; }}
    QListWidget::item:hover:!selected {{ background: {c['SURFACE3']}; color: {c['TEXT']}; }}
    QMenu {{ background: {c['SURFACE']}; color: {c['TEXT']}; border: 1px solid {c['BORDER_LT']};
        border-radius: 9px; padding: 5px; }}
    QMenu::item {{ padding: 7px 24px; border-radius: 6px; }}
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
        border-radius: 5px; background: {c['INPUT_BG']}; }}
    QCheckBox::indicator:checked {{ background: {c['ACCENT']}; border-color: {c['ACCENT']}; }}
    QGroupBox {{ background: {c['PANEL_ALPHA2']}; border: 1px solid {c['BORDER']}; border-radius: 10px;
        margin-top: 12px; padding-top: 16px; font-weight: bold; font-size: 11px; color: {c['TEXT_MUTED']}; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 14px; padding: 0 6px; }}
    QScrollArea {{ background: transparent; border: none; }}
    {glass_rules}
    QFrame[cssClass="glass"] {{ background: {c['PANEL_ALPHA']}; border: 1px solid {c['BORDER']};
        border-radius: 12px; }}
    QFrame[cssClass="glass2"] {{ background: {c['PANEL_ALPHA2']}; border: 1px solid {c['BORDER']};
        border-radius: 10px; }}
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
    # 「文件」标签里的细分（「全部」里仍然是"文件"）
    "fk_all": "全部", "fk_video": "视频", "fk_image": "图片", "fk_design": "设计源文件",
    "fk_audio": "音频", "fk_doc": "文档", "fk_archive": "压缩包", "fk_app": "程序",
    "fk_code": "代码", "fk_font": "字体", "fk_other": "其他",
        "panel_preview": " 预览 ", "panel_snapshots": " 历史快照 ", "panel_urls": " 网址 ",
        "preview_placeholder": "选择一条记录\n即可预览",
        "btn_restore": "恢复选中状态", "btn_clear_history": "清空历史",
        "sort_default": "默认(时间最新)", "sort_oldest": "时间(最早)",
        "sort_name_az": "文件名(A-Z)", "sort_name_za": "文件名(Z-A)",
        "sort_fmt_az": "格式(A-Z)", "sort_fmt_za": "格式(Z-A)",
        "sort_size_desc": "大小(最大)", "sort_size_asc": "大小(最小)",
        "btn_copy": "复制  Enter", "btn_pin": "置顶", "btn_unpin": "取消置顶",
        # 标签 / 收藏（3.2.7）：纯本地的分组与标记
        "btn_fav": "收藏", "btn_faved": "已收藏",
        "filter_fav": "收藏",
        "btn_edit_tags": "编辑标签…",
        "m_fav_on": "加入收藏", "m_fav_off": "取消收藏", "m_edit_tags": "编辑标签…",
        "m_vault_add": "加入密库…",
        "m_vault_out": "移出密库…",
        "vault_move_out_done": "已移出密库，按原来的时间回到剪贴板历史",
        "vault_move_out_failed": "移出失败：{err}",
        # 输入框右键菜单（替掉 Qt 自带的英文直角菜单）
        "ctx_cut": "剪切", "ctx_copy": "复制", "ctx_paste": "粘贴",
        "vault_copy_content": "复制内容",
        "st_fav_set": "已收藏 {n} 条", "st_fav_unset": "已取消收藏 {n} 条",
        "st_fav_filter": "只看收藏（{n} 条）", "st_fav_filter_off": "已取消收藏筛选",
        "st_tag_filter": "只看标签 #{tag}", "st_tag_filter_off": "已取消标签筛选",
        "st_tags_saved": "标签已更新（{n} 条）",
        "st_tags_none": "没有可编辑标签的记录",
        "tags_title": "编辑标签", "tags_title_add": "添加标签",
        "tags_sub": "标签存在本机：搜索框里输标签名，也能找到这条记录",
        "tags_sub_add": "为选中的 {n} 条记录添加标签（已有的不会重复）",
        "tags_placeholder": "输入标签后回车", "tags_none": "无标签",
        "tags_known": "历史标签", "tags_hint_existing": "已有",
        "hk_fav": "收藏 / 取消收藏",
        # 密库（3.3.1）：用户主动放进去的私密内容，名称可选、四类归档
        "vault_btn": "密库",
        "vault_title": "密库",
        "vault_sub": "只存在本机、加密保存；可以设主密码，打开需解锁、闲置 / 关窗自动锁定；内容不进剪贴板历史，也不同步到手机 / 云端",
        "vault_add": "新增", "vault_edit": "编辑", "vault_delete": "删除",
        "vault_copy": "复制", "vault_open": "打开", "vault_rename": "重命名",
        "vault_search_ph": "搜索名称 / 内容",
        "vault_col_name": "名称 / 内容", "vault_col_type": "类型",
        "vault_col_time": "时间",
        "vault_kind_all": "全部", "vault_kind_text": "文本",
        "vault_kind_image": "图片", "vault_kind_file": "文件",
        "vault_kind_url": "网址",
        "vault_empty": "还没有内容，点「新增」放一条进来",
        "vault_count": "共 {n} 条",
        "vault_count_kind": "{kind}：{n} 条 · 全部 {total} 条",
        "vault_count_search": "筛选出 {n} 条 · 全部 {total} 条",
        "vault_new_title": "添加到密库", "vault_edit_title": "编辑密库内容",
        "vault_new_sub": "内容加密后存在本机，只有你自己能看到；名称可以不填",
        "vault_f_name": "名称（可选）", "vault_ph_name": "留空就按内容显示",
        "vault_f_content": "内容", "vault_ph_content": "粘贴或输入要存的内容",
        "vault_pick_image": "选图片…", "vault_pick_file": "选文件…",
        "vault_pick_clear": "清除",
        "vault_summary_image": "图片 · {name}",
        "vault_summary_file": "文件 · {n} 个",
        "vault_need_content": "名称和内容至少填一个",
        "vault_saved": "已保存到密库", "vault_deleted": "已从密库删除",
        "st_vault_moved": "已移入密库，剪贴板历史里的那条已删除",
        "vault_renamed": "名称已更新",
        "vault_copied": "已复制到剪贴板（不会进剪贴板历史）",
        "vault_copied_image": "图片已复制到剪贴板",
        "vault_copied_files": "文件已复制到剪贴板",
        "vault_no_file": "文件已不在了",
        "vault_rename_title": "重命名",
        "vault_rename_sub": "留空就按内容显示",
        "vault_confirm_delete": "删除这条密库内容？",
        "vault_confirm_delete_sub": "删除后无法恢复（密库不参与历史快照回滚）",
        # 主密码（3.3.4）：密库单独一把钥匙，打开要解锁，闲置 / 关窗自动锁定
        "vault_pw_btn": "主密码…", "vault_lock_now": "锁定",
        "vault_locked_title": "密库已锁定",
        "vault_locked_sub": "输入主密码才能查看和修改密库内容",
        "vault_pw_ph": "主密码",
        "vault_unlock": "解锁",
        "vault_pw_wrong": "主密码不对，再试一次",
        "vault_pw_len": "主密码至少 6 位",
        "vault_pw_mismatch": "两次输入的新主密码不一样",
        "vault_pw_set_title": "设置主密码",
        "vault_pw_change_title": "修改主密码",
        "vault_pw_set_sub": "给密库单独设一把主密码：打开密库要解锁，闲置 5 分钟或关掉窗口自动锁定",
        "vault_pw_change_sub": "先验证当前主密码；新密码留空 = 取消主密码",
        "vault_f_pw_now": "当前主密码", "vault_f_pw_new": "新主密码",
        "vault_f_pw_again": "再输一次",
        "vault_pw_set_ok": "已启用主密码：下次打开密库需要解锁",
        "vault_pw_change_ok": "主密码已修改",
        "vault_pw_removed": "已取消主密码，密库改回本机密钥加密",
        "vault_pw_show": "显示密码", "vault_pw_hide": "隐藏密码",
        "vault_pw_off": "关闭密码",
        "vault_pw_off_confirm": "关闭主密码？",
        "vault_pw_off_sub": "关闭后打开密库不再需要密码（直接就能看）；随时可以再设回来。",
        "vault_pw_off_done": "已关闭主密码，密库现在可以直接打开",
        "vault_pw_forget": "忘记主密码？",
        "vault_pw_forget_sub": "主密码没有找回功能。清空密库可以重设主密码，但密库里的内容会全部删除。",
        "vault_pw_wipe": "清空密库并重设",
        "vault_wiped": "密库已清空，可以重新设置主密码",
        "vault_need_unlock": "密库已锁定，请先解锁",
        # AI 就地处理（3.2.7+）
        "ai_menu": "AI 处理",
        "ai_act_summarize": "总结要点", "ai_act_translate": "翻译",
        "ai_act_rewrite": "改写润色", "ai_act_extract": "提取关键信息",
        "ai_act_custom": "自定义提示…",
        "ai_title": "AI 处理", "ai_sub": "只把选中的这一条发给你配置的服务商",
        "ai_prompt_title": "自定义提示",
        "ai_prompt_hint": "只对选中的这一条生效；例如「改成给同事看的汇报口径」",
        "ai_prompt_placeholder": "想怎么处理这段内容…",
        "ai_running": "正在生成…", "ai_done": "生成完成（{n} 字符）",
        "ai_stopped": "已停止，保留了已生成的部分",
        "ai_btn_stop": "停止", "ai_btn_retry": "重新生成",
        "ai_btn_copy": "复制结果", "ai_btn_save": "另存为新条",
        "ai_btn_replace": "替换本条", "ai_btn_close": "关闭",
        "ai_need_config": "还没配置 AI 服务（服务商 / 接口地址 / 模型 / API Key）",
        "ai_open_settings": "打开设置",
        "ai_only_text": "AI 处理只支持文本与网址记录",
        "ai_copied": "AI 结果已复制（{n} 字符）",
        "ai_saved_new": "AI 结果已存为新记录（{n} 字符）",
        "ai_replaced": "已用 AI 结果替换本条",
        "ai_model": "模型：{model}",
        "set_ai": "AI 服务",
        "set_ai_desc": "自带 Key，按量计费；留空即不用这个功能",
        "set_ai_provider": "服务商", "set_ai_base": "接口地址",
        "set_ai_model": "模型", "set_ai_key": "API Key",
        "set_ai_key_ph": "已保存，留空表示不修改",
        "set_ai_key_new": "粘贴你的 API Key",
        "set_ai_key_clear": "清除 Key",
        "set_ai_temp": "温度（推荐 0.3，越低越稳）", "set_ai_proxy": "代理（可留空）",
        "set_ai_proxy_ph": "如 http://127.0.0.1:7890",
        "set_ai_note": "Key 在本机加密保存（Windows 用系统 DPAPI），不写日志；"
                       "请求只发送你在列表里选中的那一条记录（单次最多 2.4 万字符、"
                       "最多生成约 1200 字，不会带上别的历史记录）。",
        "set_ai_test": "测试连接",
        "set_ai_test_ok": "连接成功：{text}",
        "set_ai_open": "配置",
        "set_ai_summary": "{provider} · {model} · {state}",
        "set_ai_key_saved": "Key 已保存", "set_ai_key_missing": "未填 Key",
        "set_ai_unset": "还没配置",
        "hk_ai": "AI 处理（总结）",
        # AI 对话 + 导出 TXT
        "ai_menu_chat": "和 AI 聊聊…",
        "ai_chat_title": "和 AI 聊聊",
        "ai_chat_sub": "已把选中的这条记录作为上下文（模型：{model}）",
        "ai_chat_intro": "已把这条记录作为上下文，下面直接提问就行。\n"
                         "例如：这段内容里有哪些需要我跟进的？帮我写一条回复？",
        "ai_chat_placeholder": "和 AI 说点什么…（回车发送）",
        "ai_chat_send": "发送",
        "ai_chat_me": "你：{text}\n",
        "ai_chat_ai": "AI：",
        "ai_chat_hint": "多轮对话只带最近 {n} 轮，不会把整段历史都发出去。",
        "ai_btn_export": "导出 TXT",
        "msg_export_failed": "导出失败：{err}",
        # 图片 / 文件分类的 AI + 关闭行为开关（3.2.9）
        "ai_act_describe": "描述这张图片", "ai_act_ocr": "提取图中文字",
        "ai_act_img_translate": "翻译图中文字",
        "ai_act_file_summary": "总结文件清单",
        "ai_act_file_suggest": "给整理建议",
        "ai_only_supported": "这条记录不支持 AI 处理",
        "ai_image_note": "图片会先压到长边 1280 的 JPEG 再发送。",
        "tray_still_running": "已收进托盘，还在后台运行（托盘图标右键可退出）",
        "win_hidden_tray": "已收进托盘",
        "set_close_tray": "关闭窗口行为",
        "set_bridge": "浏览器扩展",
        "set_bridge_sub": "只连本机 127.0.0.1",
        "set_bridge_off": "未启用",
        "set_bridge_enable": "启用本机桥接",
        "set_bridge_conn": "连接信息",
        "set_bridge_copy": "复制连接信息",
        "set_bridge_status_on": "运行中：127.0.0.1:{port}",
        "set_bridge_status_off": "未运行",
        "set_bridge_failed": "启动失败：{err}",
        "set_bridge_seen": "扩展上次连接：{t}",
        "set_bridge_hint": "在扩展的「设置」里粘贴这一行",
        "bridge_copied": "已按扩展的请求复制到系统剪贴板",
        "set_close_tray_on": "收进托盘", "set_close_tray_off": "直接退出",
        "set_ai_model_label": "显示名",
        "set_ai_model_ph": "接口真正用的模型 id",
        "set_ai_model_label_ph": "显示名（可自行更改）",
        "set_ai_model_tip": "左边是接口真正使用的模型 id；右边只是显示名，"
                            "随便改，不影响调用",
        "btn_delete": "删除  Del", "btn_export": "导出", "btn_open": "打开  双击",
        "col_time": "时间", "col_preview": "内容预览", "col_filename": "文件名",
        "col_format": "格式", "col_dims": "尺寸", "col_size": "大小",
        "col_count": "数量", "col_files": "文件列表", "col_url": "网址",
        "empty_state": "还没有记录\n复制任意内容即可自动捕获",
        "count_total": "共 {total} 条 · 置顶 {pinned}",
        "count_shown": "显示 {shown} / {total} 条 · 置顶 {pinned}",
        "count_match": "匹配 {n} 条", "selected_n": "已选 {n} 项", "no_ext": "无后缀",
        "hint_text": "{copy}/双击 复制 · {pin} 置顶 · {delete} 删除 · Ctrl+A 全选 · F5 刷新",
        "hint_image": "{copy} 复制图片 · Ctrl+O 打开 · Ctrl+E 导出",
        "hint_file": "双击 打开文件 · {copy} 复制文件 · Ctrl+O 打开 · 右键查看更多",
        "hint_url": "双击/{copy} 在浏览器打开 · {pin} 置顶 · {delete} 删除 · Ctrl+A 全选",
        "hint_all": "全部记录：{copy}/双击 复制 · {pin} 置顶 · {delete} 删除 · Ctrl+A 全选 · F5 刷新",
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
        "set_language": "语言", "set_lang_zh": "简体中文", "set_lang_en": "English",
        "set_lang_note": "切换语言后应用将立即重启",
        "set_general": "通用", "set_autostart": "开机自启动",
        "set_autostart_desc": "登录 Windows 后自动启动 YouBoard 并监听剪贴板",
        "set_theme": "主题", "set_theme_dark": "暗色", "set_theme_light": "亮色",
        "set_theme_note": "切换主题后应用将立即重启",
        "set_skin": "皮肤",
        "set_skin_note": "暗色、亮色和皮肤只能选一种；选皮肤后立即生效并重启应用",
        "skin_beige": "护眼米黄", "skin_beige_desc": "暖米黄底，长时间看更舒服",
        "skin_ocean": "深海蓝", "skin_ocean_desc": "深蓝海面色调，偏冷偏科技",
        "skin_violet": "幽夜紫", "skin_violet_desc": "暗紫底色，柔和不刺眼",
        "skin_forest": "森林绿", "skin_forest_desc": "深绿林间色调，自然安静",
        "skin_contrast": "高对比", "skin_contrast_desc": "纯黑底白字，对比最强",
        "skin_custom": "自定义皮肤", "skin_custom_desc": "自己挑底色、面板色、文字色和强调色",
        "set_skin_custom_edit": "编辑自定义皮肤…",
        "skin_dlg_title": "自定义皮肤", "skin_dlg_bg": "底色",
        "skin_dlg_surface": "面板色", "skin_dlg_text": "文字色",
        "skin_dlg_accent": "强调色", "skin_dlg_hint":
            "选完点确定保存；正在使用自定义皮肤时会立即重启生效。文字与底色对比不够时会自动改成黑/白。",
        "skin_dlg_pick": "选择颜色",
        "set_bg": "背景", "set_bg_select": "选择背景图片",
        "set_bg_wallpaper": "使用当前壁纸",
        "set_bg_history": "历史壁纸",
        "bg_h_use": "设为背景",
        "bg_h_del": "删除该背景",
        "set_bg_wall_err": "无法获取系统壁纸，请先在系统里设置桌面壁纸",
        "set_bg_clear": "恢复默认",
        "set_bg_hint": "推荐 1920×1080 或更大，支持 PNG / JPG / BMP / GIF（动态）",
        "set_bg_current": "当前背景：默认",
        "set_about": "关于", "set_data_location": "数据位置",
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
        "set_session_desc": "开启期间照常记录；退出应用或关闭开关时清空本次记录",
        "set_retention": "历史保留",
        "set_retention_open": "设置",
        "ret_title": "历史保留策略",
        "ret_sub": "让旧记录自动清理，或指定一个时间自动清空",
        "ret_forever": "永久",
        "ret_1d": "1 天",
        "ret_3d": "3 天",
        "ret_7d": "7 天",
        "ret_custom": "自定义",
        "ret_once": "定时清空",
        "ret_keep": "保留",
        "ret_unit_hours": "小时",
        "ret_unit_days": "天",
        "ret_clear_at": "清空时间",
        "ret_note": "按时间清理不会删除置顶记录；定时清空会在指定时间清除全部历史和快照。",
        "ret_summary_forever": "永久保留",
        "ret_summary_hours": "保留最近 {n} 小时",
        "ret_summary_days": "保留最近 {n} 天",
        "ret_summary_expire": "{time} 自动清空",
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
        "hk_quick_paste": "快速面板 · 粘贴回窗口",
        "hk_quick_copy": "快速面板 · 复制",
        "hk_change": "更改",
        "hk_dialog_title": "设置快捷键",
        "hk_dialog_hint": "点击下方按钮后，按下新的快捷键组合（支持 Ctrl/Alt/Shift/Win + 字母、数字、F1-F12 及 Tab/Enter/Del/Space）",
        "btn_ok": "确定",
        "btn_confirm_delete": "删除",
        "btn_confirm_clear": "清空",
        "btn_confirm_restore": "还原",
        "set_widget_title": "桌面小组件",
        "set_widget_desc": "在桌面显示一个置顶小窗口，实时更新当前剪贴板内容与最近记录，点击条目即可复制（默认开启）",
        "widget_title": "剪贴板 · 实时",
        "widget_empty": "暂无剪贴内容",
        "widget_history": "最近记录（点击可复制）",
        "widget_click_copy": "点击复制回剪贴板",
        "widget_close": "关闭小组件",
        "st_copied": "已复制回剪贴板",
        "set_phone": "手机传输",
        "set_phone_desc": "手机扫码即可查看 / 复制剪贴板历史，也能把手机文字发回电脑（同一 Wi-Fi）",
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
        "phone_image_received": "已收到来自手机的图片",
        "phone_file_received": "已收到来自手机的文件",
        "set_sync": "云同步",
        "set_sync_desc": "加密后同步到云端（GitHub Gist / WebDAV），换设备用同一密码恢复",
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
        "upd_latest_title": "已是最新版本",
        "upd_latest_meta": "YouBoard v{v} 已是最新版本，无需更新",
        "upd_new_title": "发现新版本",
        "upd_new_msg": "当前版本: v{cur}\n最新版本: v{new} ({name})\n\n是否立即更新？（将下载并替换当前程序）",
        "upd_found_meta": "当前 v{cur}  →  最新 v{new}",
        "upd_ready_hint": "点击「立即更新」开始下载",
        "upd_download": "正在下载（{pct}%）",
        "upd_download_detail": "已下载 {done} MB / {total} MB",
        "upd_connecting": "正在连接更新服务器…",
        "upd_prepare": "下载完成，正在准备安装…",
        "upd_whats_new": "本次更新",
        "upd_new_contributors": "新贡献者",
        "upd_no_notes": "本次更新以稳定性与体验优化为主。",
        "upd_update_now": "立即更新",
        "upd_later": "稍后",
        "upd_cancel": "取消下载",
        "upd_cancelling": "正在取消…",
        "upd_retry": "重试",
        "upd_close": "关闭",
        "upd_error_title": "更新失败",
        "upd_download_failed": "下载失败：{err}",
        "upd_replace_failed": "替换失败：{err}",
        "upd_verify_failed": "更新包校验未通过，已保留当前版本：{err}",
        "upd_rollback_title": "更新未完成，已自动回到更新前的版本",
        "upd_rollback_detail": "新版本装好后校验没通过，为了不让你打不开软件，已经自动还原成更新前的版本（数据都在）。原因：{err}",
        "upd_preparing_status": "正在准备更新 {pct}%",
        "upd_replacing_status": "正在替换文件 {pct}%",
        "upd_finishing_status": "即将完成 {pct}%",
        "upd_restarting": "更新完成，正在启动新版本…",
        "upd_failed": "检查失败: {e}",
        "upd_network_err": "无法连接到更新服务器，请检查网络后重试",
        "upd_rate_limit": "请求过于频繁，请稍后再试（GitHub 限流）",
        "type_all": "全部", "col_type": "类型",
        "chip_external": "正文已存本地文件",
        "set_sound": "提示音",
        "set_sound_copy": "复制提示音",
        "set_sound_paste": "粘贴提示音",
        "set_sound_desc": "默认关闭；复制 / 粘贴可分别开关，并各自选择 YouBoard 音效或自定义音频文件",
        "set_sound_pick": "选择提示音文件…",
        "set_sound_default": "系统默认提示音",
        "set_sound_builtin": "YouBoard 音效",
        "set_sound_custom": "自定义：{name}",
        "set_quick": "快速面板",
        "set_quick_desc": "按快捷键呼出搜索面板（再按一次收起），Enter 或单击即复制选中项，Esc 关闭",
        "set_quick_hotkey": "呼出快捷键",
        "set_quick_open": "打开快速面板",
        "quick_title": "YouBoard 快速面板",
        "quick_placeholder": "输入关键词搜索，Enter 粘贴…",
        "quick_empty": "没有匹配的记录",
        "quick_hint": "↑↓ 选择 · Enter / 单击 复制 · Ctrl+Enter 粘贴回窗口 · Alt+0~9 复制前 10 条 · Esc 关闭",
        "quick_copied": "已复制",
        "quick_pasted": "已粘贴到之前的窗口",
        "quick_paste_fail": "已复制，但无法自动粘贴（目标窗口可能以管理员身份运行）",
        "quick_only_pin": "仅置顶",
        "quick_filter_all": "全部",
        "set_winv": "Win+V",
        "set_winv_takeover": "接管 Win+V 打开 / 收起 YouBoard",
        "set_winv_desc": "开启后按 Win+V 直接打开或收起本工具（再按一次即收起）；开启前建议先关闭系统剪贴板历史，否则两者会同时响应",
        "set_winv_disable": "关闭系统剪贴板历史",
        "set_winv_on": "已开启",
        "set_winv_off": "未开启",
        "set_winv_already_off": "系统剪贴板历史已关闭",
        "set_winv_state_on": "系统剪贴板历史：已开启",
        "set_winv_state_off": "系统剪贴板历史：已关闭",
        "set_winv_state_unknown": "系统剪贴板历史：读取失败",
        "set_winv_done": "已关闭系统剪贴板历史（Win+V 现在由本工具接管）",
        "set_winv_failed": "自动关闭失败，请手动到「设置 → 系统 → 剪贴板」关闭",
        "set_port": "数据移植",
        "set_port_desc": "自动识别本机其它 YouBoard 安装的历史，选择后合并到当前数据",
        "set_port_open": "扫描其它安装…",
        "port_title": "导入其它 YouBoard 数据",
        "port_scanning": "正在扫描…",
        "port_none": "没有找到其它安装（可手动选择目录）",
        "port_browse": "手动选择目录…",
        "port_import": "导入选中",
        "port_rescan": "重新扫描",
        "port_found": "找到 {n} 个可导入的安装",
        "port_col_path": "位置", "port_col_count": "记录", "port_col_time": "最后修改",
        "port_unreadable": "无法读取（缺少密钥文件）",
        "port_confirm": "确定导入这个安装的数据吗？\n\n位置：{path}\n记录：{n} 条\n\n条目按内容去重，已有的不会重复添加。",
        "port_done": "导入完成：新增 {n} 条记录",
        "port_nothing": "没有新的记录可导入",
        "port_failed": "读取失败，请确认选择的是 YouBoard 数据目录",
        "tray_quick": "快速面板",
        "tray_import": "导入其它安装的数据…",
    },
    "en": {
        "win_title": "YouBoard · Clipboard History", "brand_sub": "Clipboard History",
        "manage": " Manage ▾ ", "settings_btn": " ⚙ Settings ",
        "total_records": "{n} records", "monitor_live": "Live monitoring",
        "monitor_off": "Not monitoring", "monitor_stopped": "Monitor stopped",
    "type_text": "Text", "type_image": "Images", "type_file": "Files", "type_url": "URLs",
    # file sub-categories (only used inside the "Files" tab)
    "fk_all": "All", "fk_video": "Video", "fk_image": "Images", "fk_design": "Design",
    "fk_audio": "Audio", "fk_doc": "Documents", "fk_archive": "Archives", "fk_app": "Apps",
    "fk_code": "Code", "fk_font": "Fonts", "fk_other": "Other",
        "panel_preview": " Preview ", "panel_snapshots": " Snapshots ", "panel_urls": " URLs ",
        "preview_placeholder": "Select a record\nto preview",
        "btn_restore": "Restore selected", "btn_clear_history": "Clear history",
        "sort_default": "Default (newest)", "sort_oldest": "Oldest first",
        "sort_name_az": "Name (A-Z)", "sort_name_za": "Name (Z-A)",
        "sort_fmt_az": "Format (A-Z)", "sort_fmt_za": "Format (Z-A)",
        "sort_size_desc": "Size (largest)", "sort_size_asc": "Size (smallest)",
        "btn_copy": "Copy  Enter", "btn_pin": "Pin", "btn_unpin": "Unpin",
        # tags / favorites (3.2.7) — stored locally only
        "btn_fav": "Favorite", "btn_faved": "Favorited",
        "filter_fav": "Favorites",
        "btn_edit_tags": "Edit tags…",
        "m_fav_on": "Add to favorites", "m_fav_off": "Remove from favorites",
        "m_edit_tags": "Edit tags…",
        "m_vault_add": "Save to Vault…",
        "m_vault_out": "Move out of Vault…",
        "vault_move_out_done": "Moved out of the vault — back in history at its original time",
        "vault_move_out_failed": "Move out failed: {err}",
        "ctx_cut": "Cut", "ctx_copy": "Copy", "ctx_paste": "Paste",
        "vault_copy_content": "Copy content",
        "st_fav_set": "{n} added to favorites", "st_fav_unset": "{n} removed from favorites",
        "st_fav_filter": "Favorites only ({n})", "st_fav_filter_off": "Favorites filter off",
        "st_tag_filter": "Filtering by #{tag}", "st_tag_filter_off": "Tag filter off",
        "st_tags_saved": "Tags updated ({n})",
        "st_tags_none": "Nothing to tag",
        "tags_title": "Edit tags", "tags_title_add": "Add tags",
        "tags_sub": "Tags stay on this device — typing a tag in the search box finds the record",
        "tags_sub_add": "Add tags to {n} selected records (existing ones are skipped)",
        "tags_placeholder": "Type a tag, press Enter", "tags_none": "No tags",
        "tags_known": "Known tags", "tags_hint_existing": "existing",
        "hk_fav": "Favorite / unfavorite",
        # Vault (3.3.1) — private items you add by hand; name is optional
        "vault_btn": "Vault",
        "vault_title": "Vault",
        "vault_sub": "Encrypted on this device only; an optional master password is required to open it and it locks when idle or closed — nothing here enters clipboard history or syncs to phone/cloud",
        "vault_add": "Add", "vault_edit": "Edit", "vault_delete": "Delete",
        "vault_copy": "Copy", "vault_open": "Open", "vault_rename": "Rename",
        "vault_search_ph": "Search name / content",
        "vault_col_name": "Name / content", "vault_col_type": "Type",
        "vault_col_time": "Time",
        "vault_kind_all": "All", "vault_kind_text": "Text",
        "vault_kind_image": "Images", "vault_kind_file": "Files",
        "vault_kind_url": "Links",
        "vault_empty": "Nothing stored yet — click Add",
        "vault_count": "{n} items",
        "vault_count_kind": "{kind}: {n} · {total} total",
        "vault_count_search": "{n} matched · {total} total",
        "vault_new_title": "Add to vault", "vault_edit_title": "Edit vault item",
        "vault_new_sub": "Encrypted and stored on this device only; the name is optional",
        "vault_f_name": "Name (optional)", "vault_ph_name": "Leave empty to show the content",
        "vault_f_content": "Content", "vault_ph_content": "Paste or type what to keep",
        "vault_pick_image": "Pick image…", "vault_pick_file": "Pick file…",
        "vault_pick_clear": "Clear",
        "vault_summary_image": "Image · {name}",
        "vault_summary_file": "File · {n} item(s)",
        "vault_need_content": "Fill in a name or some content",
        "vault_saved": "Saved to vault", "vault_deleted": "Removed from vault",
        "st_vault_moved": "Moved into the vault — the copy in clipboard history was deleted",
        "vault_renamed": "Name updated",
        "vault_copied": "Copied (not added to clipboard history)",
        "vault_copied_image": "Image copied to clipboard",
        "vault_copied_files": "Files copied to clipboard",
        "vault_no_file": "File is gone",
        "vault_rename_title": "Rename",
        "vault_rename_sub": "Leave empty to show the content instead",
        "vault_confirm_delete": "Delete this vault item?",
        "vault_confirm_delete_sub": "This cannot be undone (the vault is not part of history snapshots)",
        # Master password (3.3.4)
        "vault_pw_btn": "Master password…", "vault_lock_now": "Lock",
        "vault_locked_title": "Vault is locked",
        "vault_locked_sub": "Enter your master password to view or edit the vault",
        "vault_pw_ph": "Master password",
        "vault_unlock": "Unlock",
        "vault_pw_wrong": "Wrong master password — try again",
        "vault_pw_len": "Use at least 6 characters",
        "vault_pw_mismatch": "The two passwords don't match",
        "vault_pw_set_title": "Set master password",
        "vault_pw_change_title": "Change master password",
        "vault_pw_set_sub": "Give the vault its own master password: opening it asks for the password, and it locks after 5 idle minutes or when you close the window",
        "vault_pw_change_sub": "Verify the current password first; leave the new one empty to remove it",
        "vault_f_pw_now": "Current password", "vault_f_pw_new": "New password",
        "vault_f_pw_again": "Repeat it",
        "vault_pw_set_ok": "Master password enabled — the vault will ask for it next time",
        "vault_pw_change_ok": "Master password changed",
        "vault_pw_removed": "Master password removed — the vault is back on the local key",
        "vault_pw_show": "Show password", "vault_pw_hide": "Hide password",
        "vault_pw_off": "Turn password off",
        "vault_pw_off_confirm": "Turn the master password off?",
        "vault_pw_off_sub": "The vault will open without asking for a password; you can turn it back on any time.",
        "vault_pw_off_done": "Master password turned off — the vault opens directly now",
        "vault_pw_forget": "Forgot the master password?",
        "vault_pw_forget_sub": "There is no recovery. Wiping the vault lets you set a new master password, but every item inside is deleted.",
        "vault_pw_wipe": "Wipe vault and start over",
        "vault_wiped": "Vault wiped — you can set a master password again",
        "vault_need_unlock": "The vault is locked — unlock it first",
        # AI actions (3.2.7+)
        "ai_menu": "AI actions",
        "ai_act_summarize": "Summarize", "ai_act_translate": "Translate",
        "ai_act_rewrite": "Rewrite", "ai_act_extract": "Extract key info",
        "ai_act_custom": "Custom prompt…",
        "ai_title": "AI actions",
        "ai_sub": "Only the selected entry is sent, to the provider you configured",
        "ai_prompt_title": "Custom prompt",
        "ai_prompt_hint": "Applies to the selected entry only",
        "ai_prompt_placeholder": "What should be done with this content…",
        "ai_running": "Generating…", "ai_done": "Done ({n} chars)",
        "ai_stopped": "Stopped — the partial result was kept",
        "ai_btn_stop": "Stop", "ai_btn_retry": "Regenerate",
        "ai_btn_copy": "Copy result", "ai_btn_save": "Save as new entry",
        "ai_btn_replace": "Replace this entry", "ai_btn_close": "Close",
        "ai_need_config": "AI is not configured yet (provider / base URL / model / API key)",
        "ai_open_settings": "Open settings",
        "ai_only_text": "AI actions work on text and URL entries",
        "ai_copied": "AI result copied ({n} chars)",
        "ai_saved_new": "AI result saved as a new entry ({n} chars)",
        "ai_replaced": "This entry was replaced with the AI result",
        "ai_model": "Model: {model}",
        "set_ai": "AI service",
        "set_ai_desc": "Bring your own key, pay per use; leave it empty to skip",
        "set_ai_provider": "Provider", "set_ai_base": "Base URL",
        "set_ai_model": "Model", "set_ai_key": "API key",
        "set_ai_key_ph": "Saved — leave empty to keep it",
        "set_ai_key_new": "Paste your API key",
        "set_ai_key_clear": "Clear key",
        "set_ai_temp": "Temperature (0.3 recommended; lower = steadier)",
        "set_ai_proxy": "Proxy (optional)",
        "set_ai_proxy_ph": "e.g. http://127.0.0.1:7890",
        "set_ai_note": "The key is encrypted on this machine (DPAPI on Windows) and "
                       "never logged; requests contain only the entry you selected "
                       "(max 24k characters in, ~1200 tokens out, no other history).",
        "set_ai_test": "Test connection",
        "set_ai_test_ok": "Connected: {text}",
        "set_ai_open": "Configure",
        "set_ai_summary": "{provider} · {model} · {state}",
        "set_ai_key_saved": "key saved", "set_ai_key_missing": "no key",
        "set_ai_unset": "Not configured",
        "hk_ai": "AI actions (summarize)",
        # AI chat + TXT export
        "ai_menu_chat": "Chat with AI…",
        "ai_chat_title": "Chat with AI",
        "ai_chat_sub": "The selected entry is used as context (model: {model})",
        "ai_chat_intro": "This entry is the context — just ask below.\n"
                         "e.g. What should I follow up on here? Draft a reply.",
        "ai_chat_placeholder": "Say something to the AI… (Enter to send)",
        "ai_chat_send": "Send",
        "ai_chat_me": "You: {text}\n",
        "ai_chat_ai": "AI: ",
        "ai_chat_hint": "Only the last {n} turns are sent, so tokens stay bounded.",
        "ai_btn_export": "Export TXT",
        "msg_export_failed": "Export failed: {err}",
        # image / file AI + close behaviour (3.2.9)
        "ai_act_describe": "Describe this image",
        "ai_act_ocr": "Extract text in the image",
        "ai_act_img_translate": "Translate text in the image",
        "ai_act_file_summary": "Summarize the file list",
        "ai_act_file_suggest": "Suggest how to tidy it",
        "ai_only_supported": "This entry does not support AI actions",
        "ai_image_note": "Images are sent as JPEG, longest side 1280.",
        "tray_still_running": "Kept in the tray and still running "
                              "(right-click the tray icon to quit)",
        "win_hidden_tray": "Moved to the tray",
        "set_close_tray": "Close window behavior",
        "set_bridge": "Browser extension",
        "set_bridge_sub": "Localhost only (127.0.0.1)",
        "set_bridge_off": "Not enabled",
        "set_bridge_enable": "Enable local bridge",
        "set_bridge_conn": "Connection info",
        "set_bridge_copy": "Copy connection info",
        "set_bridge_status_on": "Running on 127.0.0.1:{port}",
        "set_bridge_status_off": "Not running",
        "set_bridge_failed": "Failed to start: {err}",
        "set_bridge_seen": "Extension last connected: {t}",
        "set_bridge_hint": "Paste this line into the extension's settings",
        "bridge_copied": "Copied to the system clipboard (requested by the extension)",
        "set_close_tray_on": "Keep in tray", "set_close_tray_off": "Quit",
        "set_ai_model_label": "Display name",
        "set_ai_model_ph": "real model id used by the API",
        "set_ai_model_label_ph": "display name (editable)",
        "set_ai_model_tip": "Left is the real model id sent to the API; "
                            "right is just a display name you can change freely",
        "btn_delete": "Delete  Del", "btn_export": "Export", "btn_open": "Open  Dbl-click",
        "col_time": "Time", "col_preview": "Preview", "col_filename": "Filename",
        "col_format": "Format", "col_dims": "Dimensions", "col_size": "Size",
        "col_count": "Count", "col_files": "Files", "col_url": "URL",
        "empty_state": "No records yet\nCopy anything and it will be captured",
        "count_total": "{total} records · {pinned} pinned",
        "count_shown": "Showing {shown} / {total} · {pinned} pinned",
        "count_match": "{n} matched", "selected_n": "{n} selected", "no_ext": "no ext",
        "hint_text": "{copy}/double-click copy · {pin} pin · {delete} delete · Ctrl+A select all · F5 refresh",
        "hint_image": "{copy} copy image · Ctrl+O open · Ctrl+E export",
        "hint_file": "Double-click open file · {copy} copy files · Ctrl+O open · Right-click for more",
        "hint_url": "Double-click/{copy} open in browser · {pin} pin · {delete} delete · Ctrl+A select all",
        "hint_all": "All records: {copy}/double-click copy · {pin} pin · {delete} delete · Ctrl+A select all · F5 refresh",
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
        "set_language": "Language", "set_lang_zh": "简体中文", "set_lang_en": "English",
        "set_lang_note": "The app restarts immediately after switching language",
        "set_general": "General", "set_autostart": "Start with Windows",
        "set_autostart_desc": "Automatically start YouBoard and monitor the clipboard when you sign in",
        "set_theme": "Theme", "set_theme_dark": "Dark", "set_theme_light": "Light",
        "set_theme_note": "The app restarts immediately after switching theme",
        "set_skin": "Skin",
        "set_skin_note": "Dark, Light and skins are mutually exclusive; picking a skin applies immediately and restarts the app",
        "skin_beige": "Warm Beige", "skin_beige_desc": "Warm beige background, easier for long reading",
        "skin_ocean": "Deep Ocean", "skin_ocean_desc": "Cool deep-blue tones with a techy feel",
        "skin_violet": "Night Violet", "skin_violet_desc": "Soft dark violet, gentle on the eyes",
        "skin_forest": "Forest Green", "skin_forest_desc": "Deep green woodland tones, calm and natural",
        "skin_contrast": "High Contrast", "skin_contrast_desc": "Pure black with white text, maximum contrast",
        "skin_custom": "Custom Skin", "skin_custom_desc": "Pick your own background, panel, text and accent colors",
        "set_skin_custom_edit": "Edit custom skin…",
        "skin_dlg_title": "Custom skin", "skin_dlg_bg": "Background",
        "skin_dlg_surface": "Panel", "skin_dlg_text": "Text",
        "skin_dlg_accent": "Accent", "skin_dlg_hint":
            "Click OK to save; if the custom skin is active the app restarts right away. Text is auto-switched to black/white when contrast is too low.",
        "skin_dlg_pick": "Pick color",
        "set_bg": "Background", "set_bg_select": "Choose background image",
        "set_bg_wallpaper": "Use current wallpaper",
        "set_bg_history": "Wallpaper history",
        "bg_h_use": "Use as background",
        "bg_h_del": "Delete this background",
        "set_bg_wall_err": "Cannot get the system wallpaper. Set a desktop wallpaper first.",
        "set_bg_clear": "Reset to default",
        "set_bg_hint": "Recommended 1920×1080 or larger, PNG / JPG / BMP / GIF (animated)",
        "set_bg_current": "Current: Default",
        "set_about": "About", "set_data_location": "Data location",
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
        "set_session_desc": "Records normally while on; quitting or switching it off clears what was recorded in this session",
        "set_retention": "History Retention / RETENTION",
        "set_retention_open": "Configure",
        "ret_title": "History retention",
        "ret_sub": "Automatically remove old records or clear history at a chosen time",
        "ret_forever": "Forever",
        "ret_1d": "1 day",
        "ret_3d": "3 days",
        "ret_7d": "7 days",
        "ret_custom": "Custom",
        "ret_once": "Scheduled clear",
        "ret_keep": "Keep",
        "ret_unit_hours": "hours",
        "ret_unit_days": "days",
        "ret_clear_at": "Clear at",
        "ret_note": "Age-based cleanup keeps pinned records. Scheduled clear removes all history and snapshots.",
        "ret_summary_forever": "Kept forever",
        "ret_summary_hours": "Keep the last {n} hours",
        "ret_summary_days": "Keep the last {n} days",
        "ret_summary_expire": "Clear at {time}",
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
        "hk_quick_paste": "Quick panel · Paste to window",
        "hk_quick_copy": "Quick panel · Copy",
        "hk_change": "Change",
        "hk_dialog_title": "Set Shortcut",
        "hk_dialog_hint": "Click below, then press the new key combination (Ctrl/Alt/Shift/Win + letter, digit, F1-F12, Tab/Enter/Del/Space)",
        "btn_ok": "OK",
        "btn_confirm_delete": "Delete",
        "btn_confirm_clear": "Clear",
        "btn_confirm_restore": "Restore",
        "set_widget_title": "Desktop Widget",
        "set_widget_desc": "Show a small always-on-top window with live clipboard content and recent history; click an item to copy it (on by default)",
        "widget_title": "Clipboard · Live",
        "widget_empty": "Clipboard is empty",
        "widget_history": "Recent (click to copy)",
        "widget_click_copy": "Click to copy back to clipboard",
        "widget_close": "Close widget",
        "st_copied": "Copied back to clipboard",
        "set_phone": "Phone Transfer / PHONE",
        "set_phone_desc": "Scan the QR code to browse / copy history on your phone, or send phone text back to the PC (same Wi-Fi)",
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
        "phone_image_received": "Received image from phone",
        "phone_file_received": "Received file from phone",
        "set_sync": "Cloud Sync / CLOUD SYNC",
        "set_sync_desc": "Sync encrypted history to the cloud (GitHub Gist / WebDAV); restore elsewhere with the same passphrase",
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
        "upd_title": "Check Update",
        "upd_latest": "Already on the latest version v{v}",
        "upd_latest_title": "You're up to date",
        "upd_latest_meta": "YouBoard v{v} is the latest version.",
        "upd_new_title": "New version available",
        "upd_new_msg": "Current: v{cur} → Latest: v{new} ({name})\nUpdate now? (Will download and replace the program)",
        "upd_found_meta": "Current v{cur}  →  Latest v{new}",
        "upd_ready_hint": "Choose Update now to start downloading",
        "upd_download": "Downloading ({pct}%)",
        "upd_download_detail": "{done} MB / {total} MB downloaded",
        "upd_connecting": "Connecting to the update server…",
        "upd_prepare": "Download complete. Preparing to install…",
        "upd_whats_new": "What's new",
        "upd_new_contributors": "New contributors",
        "upd_no_notes": "This update focuses on stability and experience improvements.",
        "upd_update_now": "Update now",
        "upd_later": "Later",
        "upd_cancel": "Cancel download",
        "upd_cancelling": "Cancelling…",
        "upd_retry": "Retry",
        "upd_close": "Close",
        "upd_error_title": "Update failed",
        "upd_download_failed": "Download failed: {err}",
        "upd_replace_failed": "Replace failed: {err}",
        "upd_verify_failed": "Update package failed verification — keeping the current version: {err}",
        "upd_rollback_title": "Update did not complete — rolled back automatically",
        "upd_rollback_detail": "The new build failed its integrity check after install, so YouBoard restored the previous version for you (your data is safe). Reason: {err}",
        "upd_preparing_status": "Preparing update {pct}%",
        "upd_replacing_status": "Replacing files {pct}%",
        "upd_finishing_status": "Finishing {pct}%",
        "upd_restarting": "Update complete. Starting the new version…",
        "upd_failed": "Check failed: {e}",
        "upd_network_err": "Cannot connect to update server. Please check your network and try again.",
        "upd_rate_limit": "Too many requests. Please try again later (GitHub rate limit).",
        "type_all": "All", "col_type": "Type",
        "chip_external": "Body stored locally",
        "set_sound": "Sound",
        "set_sound_copy": "Copy sound",
        "set_sound_paste": "Paste sound",
        "set_sound_desc": "Off by default; copy / paste each can be toggled and set to YouBoard sound or a custom audio file",
        "set_sound_pick": "Choose sound file…",
        "set_sound_default": "System default",
        "set_sound_builtin": "YouBoard sound",
        "set_sound_custom": "Custom: {name}",
        "set_quick": "Quick Panel",
        "set_quick_desc": "Press the hotkey to open a search panel (press again to hide); Enter or a click copies the selected item",
        "set_quick_hotkey": "Panel hotkey",
        "set_quick_open": "Open Quick Panel",
        "quick_title": "YouBoard Quick Panel",
        "quick_placeholder": "Type to search, Enter to paste…",
        "quick_empty": "No matching records",
        "quick_hint": "↑↓ Select · Enter / Click to copy · Ctrl+Enter Paste to window · Alt+0~9 Copy top 10 · Esc Close",
        "quick_copied": "Copied",
        "quick_pasted": "Pasted into the previous window",
        "quick_paste_fail": "Copied, but auto-paste failed (target window may run as administrator)",
        "quick_only_pin": "Pinned only",
        "quick_filter_all": "All",
        "set_winv": "Win+V",
        "set_winv_takeover": "Take over Win+V to open / hide YouBoard",
        "set_winv_desc": "Once on, Win+V opens or hides this tool (press again to hide); turning off Windows clipboard history first is recommended, otherwise both will respond",
        "set_winv_disable": "Turn off Windows clipboard history",
        "set_winv_on": "On",
        "set_winv_off": "Off",
        "set_winv_already_off": "Windows clipboard history is already off",
        "set_winv_state_on": "Windows clipboard history: ON",
        "set_winv_state_off": "Windows clipboard history: OFF",
        "set_winv_state_unknown": "Windows clipboard history: unknown",
        "set_winv_done": "Windows clipboard history disabled (Win+V now handled by YouBoard)",
        "set_winv_failed": "Failed to disable automatically; do it manually in Settings → System → Clipboard",
        "set_port": "Import",
        "set_port_desc": "Detect other YouBoard installations on this PC and merge their history",
        "set_port_open": "Scan other installations…",
        "port_title": "Import data from another YouBoard",
        "port_scanning": "Scanning…",
        "port_none": "No other installation found (you can pick a folder manually)",
        "port_browse": "Choose folder…",
        "port_import": "Import selected",
        "port_rescan": "Rescan",
        "port_found": "Found {n} installations",
        "port_col_path": "Location", "port_col_count": "Records", "port_col_time": "Modified",
        "port_unreadable": "Unreadable (key file missing)",
        "port_confirm": "Import the data of this installation?\n\nLocation: {path}\nRecords: {n}\n\nDuplicates are skipped.",
        "port_done": "Import finished: {n} new records",
        "port_nothing": "Nothing new to import",
        "port_failed": "Read failed; please pick a YouBoard data folder",
        "tray_quick": "Quick Panel",
        "tray_import": "Import data from another install…",
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
    """Format a byte count into a human-readable string (B / KB / MB / GB / TB)."""
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "?"
    if n < 0:
        n = 0.0
    for unit, step in (("TB", 1024.0 ** 4), ("GB", 1024.0 ** 3),
                       ("MB", 1024.0 ** 2), ("KB", 1024.0)):
        if n >= step:
            return f"{n / step:.1f} {unit}"
    return f"{int(n)} B"


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
    HEIGHT = 2

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
        """Draw a restrained single-accent ambient line."""
        w = self.width()
        if w < 20:
            return
        t = time.perf_counter() - self._t0
        breath = 0.5 + 0.5 * math.sin(t * 2.0 * math.pi / 4.2)
        p = QPainter(self)
        p.setPen(Qt.PenStyle.NoPen)
        p.fillRect(self.rect(), QColor(C['BG']))
        accent = QColor(C['ACCENT'])
        accent.setAlpha(min(150, int(34 + breath * 42 + self._surge * 74)))
        p.fillRect(self.rect(), accent)
        p.end()


# ===========================================================================
# ImageLoader — background thread for loading preview images
# ===========================================================================
class ImageLoader(QThread):
    loaded = pyqtSignal(int, str, object)

    def __init__(self, path, gen, parent=None):
        super().__init__(parent)
        self.setObjectName("YouBoardImageLoader")
        self.path = path
        self.gen = gen
        self._cancelled = threading.Event()

    def cancel(self):
        self._cancelled.set()

    def run(self):
        try:
            if self._cancelled.is_set():
                return
            from PIL import Image as PILImage
            img = PILImage.open(self.path)
            img.load()
            if self._cancelled.is_set():
                return
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGB")
            if self._cancelled.is_set():
                return
            if max(img.size) > PREVIEW_MAX:
                ratio = PREVIEW_MAX / max(img.size)
                img = img.resize((max(1, int(img.width * ratio)),
                                  max(1, int(img.height * ratio))), PILImage.LANCZOS)
            if self._cancelled.is_set():
                return
            self.loaded.emit(self.gen, self.path, img)
        except Exception:
            pass


class _FileStatusWorker(QThread):
    """后台检查文件条目是否失效，避免 UI 线程批量访问磁盘。"""
    done = pyqtSignal(object)

    def __init__(self, entries, parent=None):
        super().__init__(parent)
        self.setObjectName("YouBoardFileStatus")
        self._entries = list(entries)

    def run(self):
        result = {}
        for entry in self._entries:
            try:
                result[entry.get("hash", "")] = \
                    ClipboardStore.file_entry_missing(entry)
            except Exception:
                result[entry.get("hash", "")] = False
        self.done.emit(result)


class _CacheCleanupWorker(QThread):
    """后台回收不再被历史或快照引用的图片与文件缓存。"""
    done = pyqtSignal(int, int, int)

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.setObjectName("YouBoardCacheCleanup")
        self._store = store

    def run(self):
        try:
            removed_entries = self._store.apply_retention()
            removed, freed = self._store.garbage_collect()
        except Exception:
            removed_entries, removed, freed = 0, 0, 0
        self.done.emit(removed_entries, removed, freed)


# ===========================================================================
# Update Downloader (multi-threaded segmented download, maximizes bandwidth)
# ===========================================================================
# 更新包最小可信体积：真实构建都在 30MB 上下，低于这个数一律当成没下完
UPDATE_MIN_BYTES = 4 * 1024 * 1024
# PyInstaller 归档结尾的 cookie 魔数（onefile 包的最后一段固定带它）
_PEI_MAGIC = b"MEI\x0c\x0b\x0a\x0b\x0e"


def _coverage_complete(spans, total):
    """分片区间是否无缝覆盖 [0, total)——判断"整份文件都下到了"。"""
    pos = 0
    for start, end in spans:
        if start > pos:
            return False
        if end + 1 > pos:
            pos = end + 1
    return pos >= total


def sha256_file(path):
    """算文件的 SHA256（更新包校验用）。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_update_file(path, expected_size=0, expected_sha256=""):
    """校验下载到的更新包是不是"完整、可启动"的可执行镜像，返回 (ok, reason)。

    这一步是 v3.3.1 补上的关键防线：以前的下载器会先把文件按总大小填成零字节，
    只要有一段分片失败也会当成功，于是可能装上一个 87% 全是零的"假 EXE"
    （用户的真实事故）。现在装包前必须过：体积、MZ 头、PE 头、PyInstaller
    归档标记、以及与发布资源一致的 SHA256。
    """
    try:
        size = os.path.getsize(path)
    except OSError as ex:
        return False, "无法读取更新包: %s" % ex
    if size < UPDATE_MIN_BYTES:
        return False, "更新包过小（%d 字节）" % size
    if expected_size and size != int(expected_size):
        return False, "更新包大小不符（实际 %d / 官方 %s）" % (size, expected_size)
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
            if size > 8192:
                f.seek(-8192, os.SEEK_END)
                tail = f.read(8192)
            else:
                tail = head
    except OSError as ex:
        return False, "读取更新包失败: %s" % ex
    if head[:2] != b"MZ":
        return False, "缺少 MZ 可执行文件头（下载不完整）"
    pe_off = int.from_bytes(head[0x3C:0x40], "little")
    if pe_off <= 0 or pe_off > size - 4:
        return False, "PE 头偏移无效（文件损坏）"
    if pe_off + 4 <= len(head):
        pe_sig = head[pe_off:pe_off + 4]
    else:
        try:
            with open(path, "rb") as f:
                f.seek(pe_off)
                pe_sig = f.read(4)
        except OSError as ex:
            return False, "读取 PE 头失败: %s" % ex
    if pe_sig != b"PE\x00\x00":
        return False, "PE 头签名不正确（文件损坏）"
    if _PEI_MAGIC not in tail:
        return False, "缺少 PyInstaller 归档标记（下载不完整）"
    if expected_sha256:
        try:
            got = sha256_file(path)
        except OSError as ex:
            return False, "计算校验值失败: %s" % ex
        if got.lower() != str(expected_sha256).lower():
            return False, "SHA256 与发布包不一致"
    return True, ""


class _DownloadWorker(QThread):
    """Download using parallel Range segments (like IDM) to saturate bandwidth.
    Falls back to mirror racing if server doesn't support Range requests."""
    progress = pyqtSignal(int, int)   # received_bytes, total_bytes
    status = pyqtSignal(str)          # status line
    finished_ok = pyqtSignal(str, str)   # downloaded temp file path, sha256
    failed = pyqtSignal(str)          # error message

    SEGMENTS = 8          # parallel segments per source (like download managers)
    CHUNK = 512 * 1024    # 512KB read buffer
    TIMEOUT = 12          # connection timeout seconds
    MAX_PARALLEL = 4      # mirror racing fallback: race up to 4 sources
    SPEED_CHECK_TIME = 5  # seconds to wait before judging speed
    MIN_SPEED = 200 * 1024  # 200KB/s minimum acceptable speed

    def __init__(self, urls, dest_path, parent=None, expected_size=0,
                 expected_sha256=""):
        super().__init__(parent)
        self._urls = urls
        self._dest = dest_path
        # 发布页给出的体积 / 摘要（拿不到时只做结构性校验）
        self._expected_size = int(expected_size or 0)
        self._expected_sha256 = str(expected_sha256 or "")
        self._abort = False
        self._slow_abort = False  # set when current source is too slow
        self._lock = threading.Lock()
        self._received = 0  # total bytes received across all segments
        self._total = 0
        self._covered = []  # 已经完整写好的分片区间，用来确认"整份都下到了"

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
                    ok2, info = self._verify_package(total)
                    if ok2:
                        self.finished_ok.emit(self._dest, info)
                        return
                    err = info
                last_err = err
            else:
                # Fallback: simple single-stream download from this URL
                self.status.emit(f"正在下载: {host}")
                ok, err = self._simple_download(url)
                if ok:
                    ok2, info = self._verify_package(total)
                    if ok2:
                        self.finished_ok.emit(self._dest, info)
                        return
                    err = info
                last_err = err
            if self._abort:
                return

        if not self._abort:
            self.failed.emit(last_err)

    def _verify_package(self, size_hint=0):
        """下载完必须校验通过才允许安装；不通过就丢掉文件换下一个源重下。

        返回 (ok, sha256 或失败原因)。
        """
        self.status.emit("正在校验更新包…")
        ok, reason = verify_update_file(
            self._dest, self._expected_size or size_hint,
            self._expected_sha256)
        if not ok:
            try:
                os.remove(self._dest)
            except OSError:
                pass
            return False, "更新包校验失败：%s" % reason
        try:
            return True, sha256_file(self._dest)
        except OSError as ex:
            return False, "计算校验值失败：%s" % ex

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
            self._covered = []
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

        with self._lock:
            covered = sorted(self._covered)
        # 关键：只要有一段没下完整，整包就是坏的（以前这里会当成功，
        # 结果装上去的是"部分零字节"的假 EXE）。必须整段无缝覆盖才算通过。
        if errors or not _coverage_complete(covered, total):
            try:
                os.remove(self._dest)
            except OSError:
                pass
            return False, (errors[0] if errors else "分片下载不完整")
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
                # 服务器若忽略 Range 会回 200 + 整份内容，那样写进偏移位就是垃圾
                if getattr(resp, "status", 206) != 206:
                    return False, "服务器未按分片返回（%s）" % resp.status
                needed = end - start + 1
                written = 0
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
                        written += len(buf)
                        with self._lock:
                            self._received += len(buf)
                            recv = self._received
                            tot = self._total
                        self.progress.emit(recv, tot)
                if written != needed:
                    return False, "分片长度不足（%d/%d）" % (written, needed)
                with self._lock:
                    self._covered.append((start, end))
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


def _plain_markdown_text(text):
    """Strip the small amount of Markdown GitHub uses in release notes."""
    if not text:
        return ""
    s = str(text)
    s = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", s)
    s = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"`([^`]*)`", r"\1", s)
    s = re.sub(r"(\*\*|__)(.*?)\1", r"\2", s)
    s = re.sub(r"(\*|_)(.*?)\1", r"\2", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = (s.replace("&amp;", "&").replace("&lt;", "<")
         .replace("&gt;", ">").replace("&quot;", '"')
         .replace("&#39;", "'"))
    return re.sub(r"\s+", " ", s).strip()


def _release_notes_sections(body):
    """Turn GitHub release Markdown into a compact list of
    ``(section_title, [bullet, ...])`` tuples."""
    sections = []
    current = None
    for raw_line in (body or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        heading = re.match(r"^#{1,4}\s+(.+?)\s*#*$", line)
        if heading:
            title = _plain_markdown_text(heading.group(1))
            low = title.lower()
            if "full changelog" in low:
                current = None
                continue
            if "what's changed" in low or "whats changed" in low:
                title = tr("upd_whats_new")
            elif "new contributor" in low:
                title = tr("upd_new_contributors")
            current = [title, []]
            sections.append(current)
            continue
        bullet = re.match(r"^(?:[-*+]|\d+[.)])\s+(.+)$", line)
        if bullet:
            item = _plain_markdown_text(bullet.group(1))
            if not item or "full changelog" in item.lower():
                continue
            if current is None:
                current = [tr("upd_whats_new"), []]
                sections.append(current)
            if len(current[1]) < 8:
                current[1].append(item)
            continue
        if not sections:
            text = _plain_markdown_text(line)
            if text and "full changelog" not in text.lower():
                sections.append([tr("upd_whats_new"), [text[:240]]])
    result = [(title, items) for title, items in sections if items]
    if not result:
        result = [(tr("upd_whats_new"), [tr("upd_no_notes")])]
    return result[:8]


class _UpdateNotesView(QScrollArea):
    """Readable release-notes column used inside the update card."""

    def __init__(self, sections, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMinimumHeight(110)
        self.setMaximumHeight(300)
        self.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollArea > QWidget > QWidget { background: transparent; }"
            "QScrollBar:vertical { background: transparent; width: 6px; margin: 0; }"
            f"QScrollBar::handle:vertical {{ background: {C['BORDER_LT']};"
            " border-radius: 3px;"
            " min-height: 24px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }")

        content = QWidget()
        content.setObjectName("updNotesContent")
        content.setAutoFillBackground(False)
        content.setStyleSheet("background: transparent;")
        self.viewport().setAutoFillBackground(False)
        self.viewport().setStyleSheet("background: transparent;")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(7)
        for title, items in sections:
            heading = QLabel(title)
            heading.setObjectName("updSectionTitle")
            heading.setWordWrap(True)
            layout.addWidget(heading)
            for item in items:
                bullet = QLabel("•  " + item)
                bullet.setObjectName("updBullet")
                bullet.setWordWrap(True)
                bullet.setTextInteractionFlags(
                    Qt.TextInteractionFlag.TextSelectableByMouse)
                layout.addWidget(bullet)
            layout.addSpacing(4)
        layout.addStretch()
        self.setWidget(content)


class _UpdateDialog(QDialog):
    """Modal update card: release notes + in-app download progress."""

    def __init__(self, owner, app, current_version, new_version,
                 release_name, sections, urls, tmp_exe,
                 expected_size=0, expected_sha256=""):
        super().__init__(owner)
        self._app = app
        self._host = app if app is not None and app.isVisible() else owner
        self._new_version = str(new_version)
        self._release_name = str(release_name or "")
        self._urls = list(urls)
        self._tmp_exe = tmp_exe
        self._expected_size = int(expected_size or 0)
        self._expected_sha256 = str(expected_sha256 or "")
        self._worker = None
        self._downloaded_path = ""
        self._downloaded_sha256 = ""
        self._state = "ready"
        self._cancelling = False

        self.setWindowTitle(tr("upd_new_title"))
        self.setWindowFlags(
            Qt.WindowType.Dialog |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        # 窗口级模态：只挡住宿主窗口，桌面小组件（独立顶层窗口）仍然可以点、可以复制
        # （应用级模态会把整个程序的所有窗口禁用，包括小组件）
        self.setModal(True)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(900, 700)

        outer = QVBoxLayout(self)
        # 窗口正好等于卡片大小（不留外边距）：截图工具按窗口矩形截出来的就是卡片本身，
        # 不会把卡片后面/周围的界面一起带进来（用户反馈"截图太大了"）。
        outer.setContentsMargins(0, 0, 0, 0)
        card = QFrame()
        card.setObjectName("updateCard")
        card.setFixedWidth(600)
        self._card = card
        # 铺满窗口（窗口尺寸按卡片首选尺寸算）：窗口矩形 = 卡片，不裁切、不压小
        outer.addWidget(card)
        # 卡片可拖动 + 内容变化时窗口跟着长（与其它卡片弹层一致）
        self._drag_off = None
        self._user_moved = False
        self._placed = False
        card.installEventFilter(self)
        self._fit_timer = QTimer(self)
        self._fit_timer.timeout.connect(self._fit_window)
        self._fit_timer.start(300)

        lay = QVBoxLayout(card)
        lay.setContentsMargins(30, 26, 30, 22)
        lay.setSpacing(10)

        icon_row = QHBoxLayout()
        icon_row.addStretch()
        self._icon = QLabel()
        self._icon.setObjectName("updIcon")
        self._icon.setFixedSize(92, 92)
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if LOGO_ICO and os.path.exists(LOGO_ICO):
            self._icon.setPixmap(QIcon(LOGO_ICO).pixmap(58, 58))
        badge = QLabel("✓", self._icon)
        badge.setObjectName("updBadge")
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setFixedSize(22, 22)
        badge.move(63, 5)
        self._badge = badge
        icon_row.addWidget(self._icon)
        icon_row.addStretch()
        lay.addLayout(icon_row)

        self._title = QLabel()
        self._title.setObjectName("updTitle")
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._title)

        self._meta = QLabel(tr("upd_found_meta",
                               cur=current_version, new=self._new_version))
        self._meta.setObjectName("updMeta")
        self._meta.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._meta)

        self._bar = QProgressBar()
        self._bar.setObjectName("updBar")
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(7)
        lay.addWidget(self._bar)

        self._detail = QLabel(tr("upd_connecting"))
        self._detail.setObjectName("updDetail")
        self._detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._detail.setWordWrap(True)
        lay.addWidget(self._detail)

        self._notes = _UpdateNotesView(sections)
        lay.addWidget(self._notes, 1)

        button_row = QHBoxLayout()
        button_row.addStretch()
        self._secondary_btn = QPushButton(tr("upd_later"))
        self._secondary_btn.setObjectName("updSecondary")
        self._secondary_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._secondary_btn.clicked.connect(self._secondary_clicked)
        button_row.addWidget(self._secondary_btn)
        self._primary_btn = QPushButton(tr("upd_update_now"))
        self._primary_btn.setObjectName("updPrimary")
        self._primary_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._primary_btn.clicked.connect(self._primary_clicked)
        button_row.addWidget(self._primary_btn)
        lay.addLayout(button_row)

        card.setStyleSheet(f"""
            QFrame#updateCard {{ background-color: {C['SURFACE']};
                border: 1px solid {C['BORDER']}; border-radius: 14px; }}
            QLabel {{ background: transparent; color: {C['TEXT']};
                font-family: "Microsoft YaHei UI","Segoe UI",sans-serif; }}
            QLabel#updIcon {{ background-color: {C['SURFACE2']};
                border: 1px solid {C['BORDER']};
                border-radius: 46px; }}
            QLabel#updBadge {{ background-color: {C['SUCCESS']}; color: #071116;
                border-radius: 11px; font-size: 12px; font-weight: bold; }}
            QLabel#updTitle {{ color: {C['TEXT']}; font-size: 21px; font-weight: 700; }}
            QLabel#updMeta {{ color: {C['TEXT_SEC']}; font-size: 12px; }}
            QLabel#updDetail {{ color: {C['TEXT_MUTED']}; font-size: 11px; }}
            QLabel#updSectionTitle {{ color: {C['TEXT']}; font-size: 14px;
                font-weight: 700; padding-top: 4px; }}
            QLabel#updBullet {{ color: {C['TEXT_SEC']}; font-size: 12px; }}
            QProgressBar#updBar {{ background-color: {C['SURFACE3']}; border: none;
                border-radius: 3px; }}
            QProgressBar#updBar::chunk {{ background-color: {C['ACCENT']};
                border-radius: 3px; }}
            QPushButton {{ background-color: {C['SURFACE2']}; color: {C['TEXT_SEC']};
                border: 1px solid transparent; border-radius: 8px;
                padding: 7px 18px; font-size: 12px; }}
            QPushButton:hover {{ background-color: {C['SURFACE3']}; color: {C['TEXT']}; }}
            QPushButton#updPrimary {{ background-color: {C['ACCENT']}; color: #071116;
                border: none; font-weight: 700; }}
            QPushButton#updPrimary:hover {{ background-color: {C['ACCENT_HV']}; }}
        """)
        self._set_ready_state()

    def _set_ready_state(self):
        self._state = "ready"
        self._title.setText(f"{tr('upd_new_title')}  v{self._new_version}")
        detail = tr("upd_ready_hint")
        if self._release_name and self._release_name != self._new_version:
            detail = f"{self._release_name}   ·   {detail}"
        self._detail.setText(detail)
        self._bar.setValue(0)
        self._primary_btn.setText(tr("upd_update_now"))
        self._primary_btn.show()
        self._secondary_btn.setText(tr("upd_later"))
        self._secondary_btn.show()

    def _primary_clicked(self):
        if self._state in ("ready", "error"):
            self._start_download()

    def _secondary_clicked(self):
        if self._state == "downloading":
            self._cancel_download()
        else:
            self.reject()

    def _start_download(self):
        self._state = "downloading"
        self._cancelling = False
        self._title.setText(tr("upd_download", pct=0))
        self._detail.setText(tr("upd_connecting"))
        self._bar.setValue(0)
        self._primary_btn.hide()
        self._secondary_btn.setText(tr("upd_cancel"))
        self._secondary_btn.setEnabled(True)
        self._worker = _DownloadWorker(self._urls, self._tmp_exe, self,
                                       expected_size=self._expected_size,
                                       expected_sha256=self._expected_sha256)
        self._worker.progress.connect(self._on_progress)
        self._worker.status.connect(self._on_status)
        self._worker.finished_ok.connect(self._on_download_ok)
        self._worker.failed.connect(self._on_download_failed)
        self._worker.start()

    def _on_progress(self, received, total):
        if self._state != "downloading":
            return
        if total > 0:
            pct = max(0, min(100, int(received * 100 / total)))
            self._title.setText(tr("upd_download", pct=pct))
            self._bar.setValue(pct)
            self._detail.setText(tr(
                "upd_download_detail",
                done=f"{received / 1048576:.1f}",
                total=f"{total / 1048576:.1f}"))

    def _on_status(self, text):
        if self._state == "downloading" and self._bar.value() == 0:
            self._detail.setText(text)

    def _on_download_ok(self, path, sha256=""):
        if self._state != "downloading":
            return
        self._downloaded_path = path
        self._downloaded_sha256 = str(sha256 or "")
        self._state = "done"
        self._title.setText(tr("upd_prepare"))
        self._detail.setText(tr("upd_prepare"))
        self._bar.setValue(100)
        self._primary_btn.hide()
        self._secondary_btn.hide()
        QTimer.singleShot(650, self.accept)

    def _on_download_failed(self, error):
        if self._cancelling:
            return
        self._state = "error"
        self._title.setText(tr("upd_error_title"))
        self._detail.setText(tr("upd_download_failed",
                                err=_plain_markdown_text(str(error))[:180]))
        self._bar.setValue(0)
        self._primary_btn.setText(tr("upd_retry"))
        self._primary_btn.show()
        self._secondary_btn.setText(tr("upd_close"))
        self._secondary_btn.show()
        self._secondary_btn.setEnabled(True)

    def _cancel_download(self):
        if self._cancelling:
            return
        if self._worker is None or not self._worker.isRunning():
            self._finish_cancel()
            return
        self._cancelling = True
        self._state = "cancelling"
        self._title.setText(tr("upd_cancelling"))
        self._secondary_btn.setEnabled(False)
        self._worker.finished.connect(self._finish_cancel)
        self._worker.abort()

    def _finish_cancel(self):
        self._worker = None
        super().reject()

    @property
    def downloaded_path(self):
        return self._downloaded_path

    @property
    def downloaded_sha256(self):
        return self._downloaded_sha256

    def paintEvent(self, event):
        """窗口只包住卡片，不再画"压暗整屏"那一层（否则卡片周围多一圈半透明黑边）。"""

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._sync_to_host)
        # 内容布局稳定后再贴合一次（有些卡片会先隐藏/显示几行，sizeHint 会晚一拍）
        QTimer.singleShot(80, self._sync_to_host)

    def _sync_to_host(self):
        """窗口严格贴合更新卡片：首次摆到宿主中央，之后只调尺寸（拖过就不动位置）。"""
        w, h = self._target_size()
        if self._placed:
            try:
                self.resize(int(w), int(h))
                self._notes.setMaximumHeight(max(120, int(h) - 260))
            except Exception:
                pass
            return
        host = self._host
        host_rect = None
        try:
            if host is not None and host.isVisible():
                host_rect = host.frameGeometry()
        except Exception:
            host_rect = None
        try:
            scr = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
            avail = scr.availableGeometry()
        except Exception:
            avail = None
        if host_rect is not None and host_rect.width() >= 200:
            x = host_rect.x() + (host_rect.width() - w) // 2
            y = host_rect.y() + (host_rect.height() - h) // 2
        elif avail is not None:
            x = avail.x() + (avail.width() - w) // 2
            y = avail.y() + (avail.height() - h) // 2
        else:
            x = y = 0
        if avail is not None:
            x = max(avail.x(), min(x, avail.x() + avail.width() - w))
            y = max(avail.y(), min(y, avail.y() + avail.height() - h))
        try:
            self.setGeometry(int(x), int(y), int(w), int(h))
            self._placed = True
            # 只限制更新说明区的高度（让长说明在卡片内滚动），卡片本身不再设上限，
            # 否则卡片会被压小、底部按钮被截断（用户实测反馈）
            self._notes.setMaximumHeight(max(120, int(h) - 260))
        except Exception:
            pass

    def _target_size(self):
        """更新卡片当前内容需要的窗口尺寸（受屏幕限制，超出时说明区滚动）。"""
        try:
            for _lay in (self._card.layout(), self.layout()):
                if _lay is not None:
                    _lay.invalidate()
                    _lay.activate()
            hint = self._card.sizeHint()
            w = max(600, int(hint.width()))
            h = max(280, int(hint.height()))
        except Exception:
            w, h = 600, 400
        try:
            scr = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
            avail = scr.availableGeometry()
            w = min(w, max(360, avail.width() - 40))
            h = min(h, max(320, avail.height() - 40))
        except Exception:
            pass
        return w, h

    def _fit_window(self):
        """内容变了（说明变长/变短等）就把窗口跟上；尺寸没变则什么都不做。"""
        if not self.isVisible():
            return
        w, h = self._target_size()
        if abs(w - self.width()) <= 2 and abs(h - self.height()) <= 2:
            return
        try:
            if self._placed:
                self.resize(int(w), int(h))
                self._notes.setMaximumHeight(max(120, int(h) - 260))
            else:
                self._sync_to_host()
        except Exception:
            pass

    # ---- 拖动卡片移动窗口 ----
    def eventFilter(self, obj, event):
        if obj is self._card:
            et = event.type()
            if et == QEvent.Type.LayoutRequest:
                # 内容一变就立刻跟随（别等兜底定时器，否则会先闪一下被挤扁的样子）
                QTimer.singleShot(0, self._fit_window)
                return False
            if (et == QEvent.Type.MouseButtonPress
                    and event.button() == Qt.MouseButton.LeftButton):
                self._drag_off = (event.globalPosition().toPoint()
                                  - self.frameGeometry().topLeft())
                return False
            if (et == QEvent.Type.MouseMove and self._drag_off is not None
                    and (event.buttons() & Qt.MouseButton.LeftButton)):
                self.move(event.globalPosition().toPoint() - self._drag_off)
                self._user_moved = True
                return True
            if et == QEvent.Type.MouseButtonRelease:
                self._drag_off = None
        return super().eventFilter(obj, event)

    def reject(self):
        if self._worker is not None and self._worker.isRunning():
            self._cancel_download()
            return
        super().reject()

    def closeEvent(self, event):
        if self._worker is not None and self._worker.isRunning():
            event.ignore()
            self._cancel_download()
            return
        super().closeEvent(event)


class _UpdateStatusDialog(_UpdateDialog):
    """更新结果状态卡片，复用更新卡片的完整视觉风格。"""

    def __init__(self, owner, app, version, title=None, detail=None,
                 kind="latest", meta=None, ok_text=None):
        super().__init__(
            owner, app, version, version, "", [], [], "")
        version = str(version or APP_VERSION)
        title = title or f"{tr('upd_latest_title')}  v{version}"
        detail = detail or tr("upd_latest_meta", v=version)
        self._state = "status"
        self._card.setMinimumHeight(360 if meta is None else 330)
        self._title.setText(title)
        if meta is None:
            self._meta.setText(f"{APP_NAME} v{version}")
        elif meta:
            self._meta.setText(str(meta))
        else:
            self._meta.hide()
        self._detail.setText(detail)
        if kind == "warning":
            self._badge.setText("!")
            self._badge.setStyleSheet(
                f"background-color: {C['AMBER']}; color: #071116;"
                " border-radius: 11px; font-size: 12px; font-weight: bold;")
            self._bar.hide()
        else:
            self._bar.setValue(100)
            self._bar.setStyleSheet(
                f"QProgressBar#updBar {{ background-color: {C['SURFACE3']};"
                " border: none; border-radius: 3px; }"
                f"QProgressBar#updBar::chunk {{ background-color: {C['SUCCESS']};"
                " border-radius: 3px; }")
        self._notes.hide()
        self._secondary_btn.hide()
        self._primary_btn.setText(ok_text or tr("btn_ok"))
        self._primary_btn.clicked.connect(self.accept)
        self.setWindowTitle(title)


def _card_host(parent):
    """找到卡片弹窗的宿主（主窗口），拿不到就退回传入的窗口。"""
    for attr in ("app", "_app"):
        w = getattr(parent, attr, None)
        if w is not None and hasattr(w, "store"):
            return w
    w = parent
    try:
        while w is not None:
            if hasattr(w, "store") and hasattr(w, "_tabs"):
                return w
            w = w.parentWidget()
    except Exception:
        return None
    return None


def _info_card(parent, title, detail, kind="latest", ok_text=None):
    """主题一致的提示卡片，替代系统原生 QMessageBox.information / warning。"""
    host = _card_host(parent)
    try:
        dlg = _UpdateStatusDialog(host or parent, host, APP_VERSION,
                                  title=title, detail=detail, kind=kind,
                                  ok_text=ok_text)
        dlg.exec()
    except Exception:
        try:
            QMessageBox.information(parent, title, detail)
        except Exception:
            pass


def _confirm_card(parent, title, detail, ok_text=None, cancel_text=None,
                  kind="warning"):
    """主题一致的确认卡片，替代 QMessageBox.question；确定返回 True。"""
    host = _card_host(parent)
    try:
        dlg = _UpdateStatusDialog(host or parent, host, APP_VERSION,
                                  title=title, detail=detail, kind=kind,
                                  meta="",
                                  ok_text=ok_text or tr("btn_ok"))
        dlg._secondary_btn.setText(cancel_text or tr("btn_cancel"))
        dlg._secondary_btn.show()
        dlg._secondary_btn.setEnabled(True)
        return dlg.exec() == QDialog.DialogCode.Accepted
    except Exception:
        ret = QMessageBox.question(
            parent, title, detail,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        return ret == QMessageBox.StandardButton.Yes


def _retention_summary(policy):
    """Return a compact one-line description of a retention policy."""
    policy = policy or {}
    mode = policy.get("mode", "forever")
    if mode == "age":
        try:
            hours = float(policy.get("hours", 0) or 0)
        except (TypeError, ValueError):
            hours = 0
        if hours > 0 and abs(hours % 24) < 0.001:
            return tr("ret_summary_days", n=int(hours // 24))
        value = int(hours) if float(hours).is_integer() else f"{hours:g}"
        return tr("ret_summary_hours", n=value)
    if mode == "expire":
        deadline = ClipboardStore._parse_time(policy.get("expire_at", ""))
        return tr("ret_summary_expire",
                  time=deadline.strftime("%Y-%m-%d %H:%M")
                  if deadline else "?")
    return tr("ret_summary_forever")


def _overlay_host(owner, app):
    """卡片弹层该跟谁对齐：优先它真正挂在的那个窗口。

    设置窗口里打开的卡片就该落在设置窗口正中间。以前这里无条件按主窗口居中
    （`app if app.isVisible() else owner`），可设置窗口经常被屏幕边挤得不在主
    窗口正中，于是卡片看着和设置窗口"对不上"（用户实测反馈）。
    """
    for cand in (owner, app):
        try:
            if cand is not None and cand.isVisible() and cand.width() >= 300:
                return cand
        except Exception:
            continue
    return owner if owner is not None else app


class _CardOverlayDialog(QDialog):
    """卡片式弹层基类：半透明遮罩 + 居中圆角卡片 + 图标 / 标题 / 副标题。

    「历史保留策略」和「自定义皮肤」都用它，两个弹层的风格天然一致；
    以后要调风格只改这一处。
    """

    CARD_WIDTH = 520

    def __init__(self, owner, app, icon_glyph, title_text, subtitle_text=""):
        super().__init__(owner)
        self._app = app
        # 卡片该跟谁对齐：优先它真正挂在的那个窗口 —— 设置窗口里打开的卡片就该落在
        # 设置窗口正中间。以前无条件按主窗口居中，而设置窗口常常被屏幕边挤得不在
        # 主窗口正中，于是卡片看着和设置窗口"对不上"（用户实测）。
        self._host = _overlay_host(owner, app)
        self.setWindowTitle(title_text)
        self.setWindowFlags(
            Qt.WindowType.Dialog |
            Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        # 同 _UpdateDialog：窗口级模态，别把桌面小组件一起禁掉
        self.setModal(True)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(700, 560)

        outer = QVBoxLayout(self)
        # 窗口正好等于卡片大小（同更新卡片）：截图工具按窗口矩形截出来就是卡片本身
        outer.setContentsMargins(0, 0, 0, 0)
        card = QFrame()
        card.setObjectName("retCard")
        card.setFixedWidth(self.CARD_WIDTH)
        # 铺满窗口（窗口尺寸按卡片首选尺寸算）：窗口矩形 = 卡片，不裁切、不压小
        outer.addWidget(card)
        self.card = card
        # 卡片可以按住拖动（空白处/标题文字上拖；按钮这类控件自己处理点击，不受影响）
        self._drag_off = None
        self._user_moved = False
        self._placed = False
        card.installEventFilter(self)
        card.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        # 内容会变的卡片（例如「历史保留策略」点「自定义」多出一行）：
        # 定时检查首选尺寸，变了就把窗口一起调整，避免内容被窗口裁掉
        self._fit_timer = QTimer(self)
        self._fit_timer.timeout.connect(self._fit_window)
        self._fit_timer.start(300)

        lay = QVBoxLayout(card)
        lay.setContentsMargins(22, 16, 22, 14)
        lay.setSpacing(8)
        self._lay = lay

        icon_row = QHBoxLayout()
        icon_row.addStretch()
        icon = QLabel(icon_glyph)
        icon.setObjectName("retIcon")
        icon.setFixedSize(56, 56)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_row.addWidget(icon)
        icon_row.addStretch()
        lay.addLayout(icon_row)
        # 子类可以把它换成自绘图标（例如密库的挂锁）
        self._icon_lbl = icon

        title = QLabel(title_text)
        title.setObjectName("retTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title)
        if subtitle_text:
            subtitle = QLabel(subtitle_text)
            subtitle.setObjectName("retSub")
            subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
            subtitle.setWordWrap(True)
            lay.addWidget(subtitle)

        card.setStyleSheet(f"""
            QFrame#retCard {{ background-color: {C['SURFACE']};
                border: 1px solid {C['BORDER']}; border-radius: 14px; }}
            QLabel {{ background: transparent; color: {C['TEXT']};
                font-family: "Microsoft YaHei UI","Segoe UI",sans-serif; }}
            QLabel#retIcon {{ background-color: {C['SURFACE2']};
                border: 1px solid {C['BORDER']};
                border-radius: 28px; color: {C['TEXT']}; font-size: 24px; }}
            QLabel#retTitle {{ color: {C['TEXT']}; font-size: 17px; font-weight: 700; }}
            QLabel#retSub, QLabel#retNote {{ color: {C['TEXT_SEC']}; font-size: 12px; }}
            QPushButton {{ background-color: {C['SURFACE2']}; color: {C['TEXT_SEC']};
                border: 1px solid transparent; border-radius: 8px;
                padding: 7px 10px; font-size: 12px; }}
            QPushButton:hover {{ background-color: {C['SURFACE3']}; color: {C['TEXT']}; }}
            QPushButton:checked {{ background-color: {C['ACCENT']}; color: #071116;
                border: none; font-weight: 700; }}
            QPushButton#retSave {{ background-color: {C['ACCENT']}; color: #071116;
                border: none; font-weight: 700; }}
            QPushButton#retSave:hover {{ background-color: {C['ACCENT_HV']}; }}
            QPushButton#unitPill {{ background-color: {C['SURFACE2']};
                color: {C['TEXT_SEC']}; border: 1px solid transparent;
                border-radius: 7px; padding: 5px 14px; font-size: 12px; }}
            QPushButton#unitPill:hover {{ background-color: {C['SURFACE3']};
                color: {C['TEXT']}; }}
            QPushButton#unitPill:checked {{ background-color: {C['ACCENT']};
                color: #071116; font-weight: 700; }}
            /* 多行输入框（密库的「内容」）要和单行输入框长得一模一样：
               以前漏了 QPlainTextEdit/QTextEdit，它落到全局样式上（透明底、无边框、
               Consolas 字体、内边距也不同），于是"内容"既没有框、文字也和"名称"对不上。*/
            QSpinBox, QDoubleSpinBox, QLineEdit, QComboBox, QDateTimeEdit,
            QPlainTextEdit, QTextEdit {{
                background-color: {C['INPUT_BG']};
                color: {C['TEXT']}; border: 1px solid {C['BORDER']};
                border-radius: 7px; padding: 5px 8px; font-size: 12px;
                font-family: "Microsoft YaHei UI","Segoe UI",sans-serif; }}
            QComboBox QAbstractItemView {{ background-color: {C['SURFACE']};
                color: {C['TEXT']}; selection-background-color: {C['ACCENT_DIM']};
                border: 1px solid {C['BORDER']}; border-radius: 7px; }}
            /* 数字框右侧的原生上下箭头是个直角小块，去掉它的底色只留箭头 */
            QSpinBox::up-button, QDoubleSpinBox::up-button,
            QSpinBox::down-button, QDoubleSpinBox::down-button {{
                width: 16px; border: none; background: transparent; }}
        """)

    def paintEvent(self, event):
        """窗口只包住卡片本身，所以不在窗口底上画"压暗整屏"那一层——
        否则卡片四周会多出一圈半透明黑边，窗口截图也会把它一起抓进去。"""

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._sync_to_host)
        # 内容布局稳定后再贴合一次（有些卡片会先隐藏/显示几行，sizeHint 会晚一拍）
        QTimer.singleShot(80, self._sync_to_host)

    def _sync_to_host(self):
        """窗口严格贴合卡片：第一次摆到宿主中央，之后只调尺寸（用户拖过就不动位置）。

        窗口尺寸按卡片"当前内容的首选尺寸"算——点「自定义」这类会让卡片变高的
        操作也会一起把窗口撑开，不会再把内容裁掉。
        """
        w, h = self._target_size()
        if self._placed:
            # 已经摆好（甚至被用户拖过）：先调尺寸；尺寸变了再决定要不要重新居中
            changed = (abs(int(w) - self.width()) > 2
                       or abs(int(h) - self.height()) > 2)
            try:
                self.resize(int(w), int(h))
                self._hug_card(w, h)
            except Exception:
                pass
            # 内容变多/变少导致窗口尺寸变化时，卡片要跟着回到宿主正中间
            # （用户自己拖过的位置要留着，不能被居中覆盖）
            if changed and not self._user_moved:
                try:
                    x, y = self._host_center(w, h)
                    self.move(int(x), int(y))
                except Exception:
                    pass
            return
        x, y = self._host_center(w, h)
        try:
            self.setGeometry(int(x), int(y), int(w), int(h))
            self._placed = True
        except Exception:
            pass
        self._hug_card(w, h)
        try:
            # 桌面小组件挂在主窗口上，跟"卡片跟谁对齐"无关，这里要认主窗口
            desk = getattr(self._app, "_desk_widget", None)
            if desk is not None and desk.isVisible():
                desk.raise_()
        except Exception:
            pass

    def _host_center(self, w, h):
        """窗口尺寸 (w, h) 时该摆在哪儿：宿主中央，宿主不可见就当前屏幕居中。"""
        host = self._host
        host_rect = None
        try:
            if host is not None and host.isVisible():
                host_rect = host.frameGeometry()
        except Exception:
            host_rect = None
        try:
            scr = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
            avail = scr.availableGeometry()
        except Exception:
            avail = None
        if host_rect is not None and host_rect.width() >= 200:
            x = host_rect.x() + (host_rect.width() - w) // 2
            y = host_rect.y() + (host_rect.height() - h) // 2
        elif avail is not None:
            x = avail.x() + (avail.width() - w) // 2
            y = avail.y() + (avail.height() - h) // 2
        else:
            x = y = 0
        if avail is not None:                     # 别跑到屏幕外
            x = max(avail.x(), min(x, avail.x() + avail.width() - w))
            y = max(avail.y(), min(y, avail.y() + avail.height() - h))
        return int(x), int(y)

    def _hug_card(self, w, h):
        """把卡片钉成窗口这么大：窗口 = 卡片，截图工具按窗口矩形抓到的就是卡片本身。

        以前只给窗口设尺寸、卡片自己还是固定宽度，两者不一致时窗口就比卡片宽出
        一大条背景（用户反馈 AI 服务卡片"识别截图给截宽了"）。
        """
        try:
            if (int(self.card.width()) != int(w)
                    or int(self.card.minimumWidth()) != int(w)):
                self.card.setFixedWidth(int(w))
        except Exception:
            pass
        try:
            # 卡片上限要一起放开，否则内容变多时卡片被旧上限卡住（底部被裁）
            self.card.setMaximumHeight(int(h))
        except Exception:
            pass

    def _target_size(self):
        """卡片当前内容需要的窗口尺寸（宽度=内容真正需要的最小宽度，受屏幕限制）。"""
        try:
            for _lay in (self.card.layout(), self.layout()):
                if _lay is not None:
                    _lay.invalidate()
                    _lay.activate()
            hint = self.card.sizeHint()
            # 宽度按"内容真正需要的最小宽度"算，不能按 sizeHint：卡片里的说明文字是
            # 自动换行的，sizeHint 会按"整段排成一行"算出八百多像素，窗口跟着变那么宽，
            # 而卡片本身就是 CARD_WIDTH 宽 —— 窗口比卡片宽出一条背景，截图全带进来。
            # 同时也不能比内容的最小宽度窄，否则卡片里的按钮/文字会被挤掉。
            need = int(self.card.minimumSizeHint().width())
            w = max(self.CARD_WIDTH, need)
            h = max(160, int(hint.height()))
        except Exception:
            w, h = self.CARD_WIDTH, 260
        try:
            scr = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
            avail = scr.availableGeometry()
            w = min(w, max(320, avail.width() - 40))
            h = min(h, max(240, avail.height() - 40))
        except Exception:
            pass
        return w, h

    def _fit_window(self):
        """内容变了（多一行/少一行）就把窗口跟上；尺寸没变则什么都不做。"""
        if not self.isVisible():
            return
        w, h = self._target_size()
        if (abs(w - self.width()) <= 2 and abs(h - self.height()) <= 2
                and abs(int(w) - self.card.width()) <= 2):
            return
        self._sync_to_host()

    # ---- 拖动卡片移动窗口 ----
    def eventFilter(self, obj, event):
        if obj is self.card:
            et = event.type()
            if et == QEvent.Type.LayoutRequest:
                # 卡片内容一变（比如点「自定义」多出一行）就立刻把窗口跟上，
                # 不能等那个 300ms 的兜底定时器——否则会先闪一下被挤扁的样子
                QTimer.singleShot(0, self._fit_window)
                return False
            if (et == QEvent.Type.MouseButtonPress
                    and event.button() == Qt.MouseButton.LeftButton):
                self._drag_off = (event.globalPosition().toPoint()
                                  - self.frameGeometry().topLeft())
                return False          # 继续正常派发（按钮/开关不受影响）
            if (et == QEvent.Type.MouseMove and self._drag_off is not None
                    and (event.buttons() & Qt.MouseButton.LeftButton)):
                self.move(event.globalPosition().toPoint() - self._drag_off)
                self._user_moved = True
                return True
            if et == QEvent.Type.MouseButtonRelease:
                self._drag_off = None
        return super().eventFilter(obj, event)


class _RetentionDialog(_CardOverlayDialog):
    """历史保留策略（卡片式弹层，样式与「自定义皮肤」一致）。"""

    def __init__(self, owner, app, policy):
        super().__init__(owner, app, "◷", tr("ret_title"), tr("ret_sub"))
        self._preset = "forever"
        self._preset_btns = {}
        lay = self._lay

        options = QGridLayout()
        options.setHorizontalSpacing(8)
        options.setVerticalSpacing(8)
        choices = [
            ("forever", tr("ret_forever")),
            ("1d", tr("ret_1d")),
            ("3d", tr("ret_3d")),
            ("7d", tr("ret_7d")),
            ("custom", tr("ret_custom")),
            ("expire", tr("ret_once")),
        ]
        for idx, (key, label) in enumerate(choices):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn.clicked.connect(lambda _, k=key: self._pick(k))
            self._preset_btns[key] = btn
            options.addWidget(btn, idx // 3, idx % 3)
        lay.addLayout(options)

        self._custom_row = QWidget()
        custom_lay = QHBoxLayout(self._custom_row)
        custom_lay.setContentsMargins(0, 0, 0, 0)
        custom_lay.setSpacing(8)
        custom_lay.addWidget(QLabel(tr("ret_keep")))
        self._custom_value = QSpinBox()
        self._custom_value.setRange(1, 8760)
        self._custom_value.setValue(12)
        custom_lay.addWidget(self._custom_value)
        # 小时 / 天：用两个小胶囊按钮，不用下拉框——下拉弹层是直角框，
        # 而且靠近窗口底部时还会翻到上面盖住别的控件
        self._custom_unit_index = 0
        self._unit_btns = []
        for _idx, _label in ((0, tr("ret_unit_hours")),
                             (1, tr("ret_unit_days"))):
            _ub = QPushButton(_label)
            _ub.setObjectName("unitPill")
            _ub.setCheckable(True)
            _ub.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            _ub.clicked.connect(lambda _, i=_idx: self._set_custom_unit(i))
            custom_lay.addWidget(_ub)
            self._unit_btns.append(_ub)
        self._set_custom_unit(0)
        custom_lay.addStretch()
        lay.addWidget(self._custom_row)

        self._expire_row = QWidget()
        expire_lay = QHBoxLayout(self._expire_row)
        expire_lay.setContentsMargins(0, 0, 0, 0)
        expire_lay.setSpacing(8)
        expire_lay.addWidget(QLabel(tr("ret_clear_at")))
        self._expire_edit = QDateTimeEdit()
        self._expire_edit.setCalendarPopup(True)
        self._expire_edit.setDisplayFormat("yyyy-MM-dd  HH:mm")
        self._expire_edit.setDateTime(
            QDateTime.currentDateTime().addSecs(3600))
        expire_lay.addWidget(self._expire_edit, 1)
        lay.addWidget(self._expire_row)

        note = QLabel(tr("ret_note"))
        note.setObjectName("retNote")
        note.setWordWrap(True)
        lay.addWidget(note)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton(tr("btn_cancel"))
        cancel.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        save = QPushButton(tr("btn_save"))
        save.setObjectName("retSave")
        save.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        save.clicked.connect(self.accept)
        buttons.addWidget(save)
        lay.addLayout(buttons)

        self._apply_policy(policy)

    def _set_custom_unit(self, index):
        """切换自定义保留的单位（0=小时 / 1=天）。"""
        self._custom_unit_index = 1 if index == 1 else 0
        for i, btn in enumerate(self._unit_btns):
            btn.setChecked(i == self._custom_unit_index)
        self._custom_value.setMaximum(
            365 if self._custom_unit_index == 1 else 8760)

    def _pick(self, key):
        self._preset = key
        for name, button in self._preset_btns.items():
            button.setChecked(name == key)
        self._custom_row.setVisible(key == "custom")
        self._expire_row.setVisible(key == "expire")

    def _apply_policy(self, policy):
        policy = policy or {}
        mode = policy.get("mode", "forever")
        key = "forever"
        if mode == "age":
            try:
                hours = max(1.0, float(policy.get("hours", 24)))
            except (TypeError, ValueError):
                hours = 24.0
            if abs(hours - 24) < 0.001:
                key = "1d"
            elif abs(hours - 72) < 0.001:
                key = "3d"
            elif abs(hours - 168) < 0.001:
                key = "7d"
            else:
                key = "custom"
                if abs(hours % 24) < 0.001:
                    self._set_custom_unit(1)
                    self._custom_value.setValue(max(1, int(hours // 24)))
                else:
                    self._set_custom_unit(0)
                    self._custom_value.setValue(max(1, int(hours)))
        elif mode == "expire":
            key = "expire"
            deadline = ClipboardStore._parse_time(policy.get("expire_at", ""))
            if deadline is not None:
                self._expire_edit.setDateTime(
                    QDateTime(deadline.year, deadline.month, deadline.day,
                              deadline.hour, deadline.minute))
        self._pick(key)

    def policy(self):
        if self._preset == "forever":
            return {"mode": "forever"}
        if self._preset in ("1d", "3d", "7d"):
            return {"mode": "age",
                    "hours": {"1d": 24, "3d": 72, "7d": 168}[self._preset]}
        if self._preset == "custom":
            value = int(self._custom_value.value())
            hours = value * 24 if self._custom_unit_index == 1 else value
            return {"mode": "age", "hours": hours}
        deadline = self._expire_edit.dateTime().toPyDateTime()
        return {"mode": "expire",
                "expire_at": deadline.replace(second=0, microsecond=0).isoformat()}

class _CustomSkinDialog(_CardOverlayDialog):
    """自定义皮肤：底色 / 面板色 / 文字色 / 强调色。

    与「历史保留策略」共用卡片式弹层（同一个基类），保证两处风格一致。
    """

    def __init__(self, owner, app, current):
        super().__init__(owner, app, "◨", tr("skin_dlg_title"),
                         tr("skin_dlg_hint"))
        self.colors = dict(current)
        lay = self._lay
        for key, label_key in (("skin_custom_bg", "skin_dlg_bg"),
                               ("skin_custom_surface", "skin_dlg_surface"),
                               ("skin_custom_text", "skin_dlg_text"),
                               ("skin_custom_accent", "skin_dlg_accent")):
            row = QHBoxLayout()
            row.setSpacing(12)
            name_lbl = QLabel(tr(label_key))
            name_lbl.setObjectName("retSub")
            name_lbl.setFixedWidth(64)
            row.addWidget(name_lbl)
            swatch = QPushButton(self.colors[key])
            swatch.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            swatch.setMinimumWidth(160)
            self._paint_swatch(swatch, self.colors[key])
            swatch.clicked.connect(
                lambda _, k=key, btn=swatch: self._pick_color(k, btn))
            row.addWidget(swatch, 1)
            lay.addLayout(row)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton(tr("btn_cancel"))
        cancel.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        save = QPushButton(tr("btn_save"))
        save.setObjectName("retSave")
        save.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        save.clicked.connect(self.accept)
        buttons.addWidget(save)
        lay.addLayout(buttons)

    @staticmethod
    def _paint_swatch(btn, color):
        btn.setText(color)
        btn.setStyleSheet(
            f"background: {color}; color: "
            f"{'#111111' if _luminance(color) > 0.5 else '#ffffff'};"
            f" border: 1px solid {C['BORDER_LT']}; border-radius: 7px;"
            f" padding: 8px 10px; font-weight: 600;")

    def _pick_color(self, key, btn):
        picked = QColorDialog.getColor(
            QColor(self.colors[key]), self, tr("skin_dlg_pick"))
        if picked.isValid():
            self.colors[key] = picked.name()
            self._paint_swatch(btn, self.colors[key])


class _TagsDialog(_CardOverlayDialog):
    """给记录打标签的卡片弹层（与「历史保留策略」「自定义皮肤」同一套样式）。

    edit 模式：整条替换这条记录的标签；add 模式：把选中的标签加到多条记录上
    （已经有的标签显示为"已有"且不可点，不会重复添加）。
    """

    def __init__(self, owner, app, current_tags, known_tags,
                 mode="edit", count=1):
        title = tr("tags_title") if mode != "add" else tr("tags_title_add")
        sub = (tr("tags_sub") if mode != "add"
               else tr("tags_sub_add", n=count))
        super().__init__(owner, app, "#", title, sub)
        self._mode = "add" if mode == "add" else "edit"
        self._count = max(1, int(count or 1))
        current = normalize_tags(current_tags)
        self._existing = {t.lower() for t in current}
        # 编辑模式：先勾上这条记录现有的标签；批量添加模式：从空白开始
        self._selected = ({t.lower(): t for t in current}
                          if self._mode == "edit" else {})
        self._known = normalize_tags(list(known_tags or []) + current)
        lay = self._lay

        self._edit = QLineEdit()
        self._edit.setPlaceholderText(tr("tags_placeholder"))
        self._edit.returnPressed.connect(self._add_typed)
        lay.addWidget(self._edit)

        self._chips_host = QWidget()
        self._chips = QGridLayout(self._chips_host)
        self._chips.setContentsMargins(0, 0, 0, 0)
        self._chips.setSpacing(6)
        lay.addWidget(self._chips_host)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton(tr("btn_cancel"))
        cancel.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        save = QPushButton(tr("btn_save"))
        save.setObjectName("retSave")
        save.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        save.clicked.connect(self.accept)
        buttons.addWidget(save)
        lay.addLayout(buttons)

        # 回车只用来「把输入框里的标签加进来」：两个按钮都不抢回车，
        # 否则输完标签一按回车，Qt 会去点默认按钮（取消），弹层被关掉、标签全丢
        for b in (cancel, save):
            b.setAutoDefault(False)
            b.setDefault(False)

        self._rebuild_chips()
        QTimer.singleShot(0, self._edit.setFocus)

    def _add_typed(self):
        """输入框回车：把新标签加进待保存列表。"""
        self._commit_typed()
        self._rebuild_chips()

    def _commit_typed(self):
        """把输入框里"已打完但还没回车"的标签收进待保存列表。

        以前只有按回车才会收字，用户打完直接点「保存」时那串文字被丢掉，
        看起来就是"编辑标签没有用"——这里补上。
        """
        tag = normalize_tag(self._edit.text())
        if not tag:
            self._edit.clear()
            return False
        self._selected[tag.lower()] = tag
        if tag.lower() not in [t.lower() for t in self._known]:
            self._known.append(tag)
        self._edit.clear()
        return True

    def accept(self):
        """点「保存」：先把输入框里的残留文字一并收下，再关闭。"""
        self._commit_typed()
        super().accept()

    def _toggle(self, tag):
        key = tag.lower()
        if self._mode == "add" and key in self._existing:
            return
        if key in self._selected:
            self._selected.pop(key, None)
        else:
            self._selected[key] = tag
        self._rebuild_chips()

    def _rebuild_chips(self):
        while self._chips.count():
            item = self._chips.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        tags = normalize_tags(list(self._known) + list(self._selected.values()))
        for i, tag in enumerate(tags):
            key = tag.lower()
            chip = QPushButton(tag)
            chip.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            if self._mode == "add" and key in self._existing:
                chip.setText(f"{tag}  {tr('tags_hint_existing')}")
                chip.setEnabled(False)
                chip.setStyleSheet(
                    f"QPushButton {{ background: {C['SURFACE3']};"
                    f" color: {C['TEXT_MUTED']}; border: 1px dashed {C['BORDER']};"
                    f" border-radius: 10px; padding: 4px 10px; font-size: 12px; }}")
            elif key in self._selected:
                chip.setStyleSheet(
                    f"QPushButton {{ background: {C['ACCENT']}; color: #071116;"
                    f" border: none; border-radius: 10px; padding: 5px 12px;"
                    f" font-size: 12px; font-weight: 700; }}")
                chip.clicked.connect(lambda _, t=tag: self._toggle(t))
            else:
                chip.setStyleSheet(
                    f"QPushButton {{ background: {C['SURFACE2']};"
                    f" color: {C['TEXT_SEC']}; border: 1px solid transparent;"
                    f" border-radius: 10px; padding: 5px 12px; font-size: 12px; }}"
                    f"QPushButton:hover {{ background: {C['SURFACE3']};"
                    f" color: {C['TEXT']}; }}")
                chip.clicked.connect(lambda _, t=tag: self._toggle(t))
            self._chips.addWidget(chip, i // 5, i % 5)

    def selected_tags(self):
        return list(self._selected.values())


class _AIWorker(QThread):
    """AI 流式请求的后台线程：每段增量发 delta，结束后发 done(ok, 消息)。

    照抄 SyncWorker 的做法——网络请求绝不放界面线程，否则会卡住窗口。
    """

    delta = pyqtSignal(str)
    done = pyqtSignal(bool, str)

    def __init__(self, client, messages, parent=None):
        super().__init__(parent)
        self._client = client
        self._messages = list(messages or [])
        self._result = ""
        self._stopped = False

    def run(self):
        try:
            def _on_chunk(chunk):
                self._result += chunk
                self.delta.emit(chunk)

            text = self._client.stream_chat(self._messages, on_delta=_on_chunk)
            if text:
                self._result = text
            self.done.emit(True, "")
        except AIError as err:
            self.done.emit(False, str(err))
        except Exception as ex:
            self.done.emit(False, str(ex))

    def result_text(self):
        return self._result

    def was_stopped(self):
        return self._stopped

    def cancel(self):
        self._stopped = True
        try:
            self._client.cancel()
        except Exception:
            pass


class _AITestWorker(QThread):
    """「测试连接」用的最小请求线程（只花 1 个字的钱，且不卡界面）。"""

    done = pyqtSignal(bool, str)

    def __init__(self, client, parent=None):
        super().__init__(parent)
        self._client = client

    def run(self):
        try:
            text = self._client.test_connection()
            self.done.emit(True, text or "ok")
        except AIError as err:
            self.done.emit(False, str(err))
        except Exception as ex:
            self.done.emit(False, str(ex))

    def cancel(self):
        try:
            self._client.cancel()
        except Exception:
            pass


class _AIPromptDialog(_CardOverlayDialog):
    """自定义提示词输入（卡片风格，与其它弹层一致）。"""

    CARD_WIDTH = 560

    def __init__(self, owner, app, entry=None):
        super().__init__(owner, app, "✎", tr("ai_prompt_title"),
                         tr("ai_prompt_hint"))
        lay = self._lay
        self._edit = QLineEdit()
        self._edit.setPlaceholderText(tr("ai_prompt_placeholder"))
        self._edit.setStyleSheet(
            f"QLineEdit {{ background: {C['INPUT_BG']}; color: {C['TEXT']};"
            f" border: 1px solid {C['BORDER']}; border-radius: 8px;"
            f" padding: 8px 10px; font-size: 13px; }}")
        self._edit.returnPressed.connect(self.accept)
        lay.addWidget(self._edit)
        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton(tr("btn_cancel"))
        cancel.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        ok = QPushButton(tr("btn_ok"))
        ok.setObjectName("retSave")
        ok.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        ok.clicked.connect(self.accept)
        buttons.addWidget(ok)
        lay.addLayout(buttons)
        QTimer.singleShot(0, self._edit.setFocus)

    def prompt_text(self):
        return self._edit.text().strip()


class _AISettingsDialog(_CardOverlayDialog):
    """AI 服务配置（卡片式弹层，与「历史保留策略」完全是同一套风格）。

    设置页里只留一行摘要 + 「配置」按钮，所以设置页不会被这一项拉得很长。
    """

    def __init__(self, owner, app, values=None):
        super().__init__(owner, app, "✦", tr("set_ai"), tr("set_ai_desc"))
        self._values = dict(values or load_ai_settings())
        self._provider = str(self._values.get("provider") or "deepseek")
        if self._provider not in PROVIDERS:
            self._provider = "custom"
        self._key_cleared = False
        self._test_worker = None
        lay = self._lay

        # 服务商：两列胶囊（与保留策略那排选项同一套画法，输入框只留圆角的）
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        self._prov_btns = {}
        for idx, pid in enumerate(AI_PROVIDER_ORDER):
            btn = QPushButton(provider_label(pid, LANG))
            btn.setCheckable(True)
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn.clicked.connect(lambda _, p=pid: self._pick_provider(p))
            self._prov_btns[pid] = btn
            grid.addWidget(btn, idx // 2, idx % 2)
        for col in range(2):
            grid.setColumnStretch(col, 1)
        lay.addLayout(grid)

        # 表单用两列网格：左列标签按内容自适应宽度（固定宽度会把中文标签裁掉，
        # 例如「温度（越低越稳）」），右列输入框起点全部对齐
        self._fields = QGridLayout()
        self._fields.setContentsMargins(0, 0, 0, 0)
        self._fields.setHorizontalSpacing(10)
        self._fields.setVerticalSpacing(8)
        self._fields.setColumnStretch(1, 1)
        lay.addLayout(self._fields)

        self._base = QLineEdit(str(self._values.get("base_url") or ""))
        # 模型：左边是接口真正用的 id，右边是用户可自行更改的显示名（参考常见客户端做法）
        self._model = QLineEdit(str(self._values.get("model") or ""))
        self._model.setPlaceholderText(tr("set_ai_model_ph"))
        self._model_label = QLineEdit(
            str(self._values.get("model_label") or ""))
        self._model_label.setPlaceholderText(tr("set_ai_model_label_ph"))
        _model_box = QWidget()
        _mb = QHBoxLayout(_model_box)
        _mb.setContentsMargins(0, 0, 0, 0)
        _mb.setSpacing(8)
        _mb.addWidget(self._model, 1)
        _mb.addWidget(self._model_label, 1)
        self._model.setToolTip(tr("set_ai_model_tip"))
        self._model_label.setToolTip(tr("set_ai_model_tip"))
        self._add_field(tr("set_ai_base"), self._base)
        self._add_field(tr("set_ai_model"), _model_box)

        self._key = QLineEdit()
        self._key.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_btn = QPushButton(tr("set_ai_key_clear"))
        self._key_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._key_btn.clicked.connect(self._on_key_clear)
        self._add_field(tr("set_ai_key"), self._key, self._key_btn)
        self._sync_key_ui()

        self._temp = QDoubleSpinBox()
        self._temp.setRange(0.0, 1.0)
        self._temp.setSingleStep(0.1)
        self._temp.setDecimals(1)
        self._temp.setValue(float(self._values.get("temperature", 0.3)))
        self._temp.setMaximumWidth(120)
        self._add_field(tr("set_ai_temp"), self._temp)

        self._proxy = QLineEdit(str(self._values.get("proxy") or ""))
        self._proxy.setPlaceholderText(tr("set_ai_proxy_ph"))
        self._add_field(tr("set_ai_proxy"), self._proxy)

        test_row = QHBoxLayout()
        test_row.setSpacing(8)
        self._test_btn = QPushButton(tr("set_ai_test"))
        self._test_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._test_btn.clicked.connect(self._test)
        test_row.addWidget(self._test_btn)
        self._test_lbl = QLabel("")
        self._test_lbl.setObjectName("retNote")
        self._test_lbl.setWordWrap(True)
        test_row.addWidget(self._test_lbl, 1)
        lay.addLayout(test_row)

        note = QLabel(tr("set_ai_note"))
        note.setObjectName("retNote")
        note.setWordWrap(True)
        lay.addWidget(note)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton(tr("btn_cancel"))
        cancel.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        save = QPushButton(tr("btn_save"))
        save.setObjectName("retSave")
        save.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        save.clicked.connect(self._save_and_close)
        buttons.addWidget(save)
        lay.addLayout(buttons)

        # 每个服务商各自记住用户填过的内容：切走再切回来不能把刚填的地址/模型清空
        # （「自定义」的服务商默认值是空的，以前切过去就直接变成一片空白）
        self._drafts = {}
        self._pick_provider(self._provider, fill=False)
        self._drafts[self._provider] = self._fields_text()

    # ---- 小组件 ----
    def _add_field(self, label_text, widget, extra=None):
        """表单加一行：左标签（宽度自适应、不裁字）+ 输入框（+ 可选小按钮）。

        标签以前写死 74px，中文长标签（「温度（越低越稳）」）会被裁成半个字，
        所以改成网格布局按内容撑开。
        """
        row = self._fields.rowCount()
        lbl = QLabel(label_text)
        lbl.setObjectName("retSub")
        lbl.setWordWrap(False)
        self._fields.addWidget(
            lbl, row, 0,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._fields.addWidget(widget, row, 1)
        if extra is not None:
            self._fields.addWidget(extra, row, 2,
                                   Qt.AlignmentFlag.AlignRight)

    def _sync_key_ui(self):
        saved = (bool(self._values.get("api_key_saved"))
                 and not self._key_cleared)
        self._key.setPlaceholderText(
            tr("set_ai_key_ph") if saved else tr("set_ai_key_new"))
        self._key_btn.setEnabled(saved)

    def _on_key_clear(self):
        self._key_cleared = True
        self._key.clear()
        self._sync_key_ui()
        self._test_lbl.setText("")

    def _fields_text(self):
        """当前三个输入框里的内容（接口地址 / 模型 / 显示名）。"""
        return (self._base.text(), self._model.text(), self._model_label.text())

    def _fill_fields(self, base, model, label):
        self._base.setText(str(base or ""))
        self._model.setText(str(model or ""))
        self._model_label.setText(str(label or ""))

    def _pick_provider(self, pid, fill=True):
        """选服务商；fill=True 时把该服务商上次填过的内容放回来（没填过就用默认值）。"""
        if pid not in PROVIDERS:
            pid = "custom"
        if fill and pid != self._provider:
            # 先把眼前这一份存到原服务商名下，切回来时原样还原
            self._drafts[self._provider] = self._fields_text()
        prev = self._provider
        self._provider = pid
        for key, btn in self._prov_btns.items():
            btn.setChecked(key == pid)
        if fill:
            draft = self._drafts.get(pid)
            if draft is None and pid == prev:
                draft = self._fields_text()
            if draft is None:
                info = provider_info(pid)
                draft = (str(info.get("base_url") or ""),
                         str(info.get("model") or ""),
                         str(info.get("model_label") or ""))
                self._drafts[pid] = draft
            self._fill_fields(*draft)
            self._test_lbl.setText("")

    # ---- 取值 / 保存 ----
    def values(self):
        values = dict(self._values)
        values["provider"] = self._provider
        values["base_url"] = self._base.text().strip()
        values["model"] = self._model.text().strip()
        values["model_label"] = self._model_label.text().strip()
        values["temperature"] = float(self._temp.value())
        values["proxy"] = self._proxy.text().strip()
        key = self._key.text().strip()
        if key:
            values["api_key"] = key
            values["api_key_saved"] = True
        elif self._key_cleared:
            values["api_key"] = ""
            values["api_key_saved"] = False
        return values

    def _save_and_close(self):
        self._stop_test()
        save_ai_settings(self.values())
        self._values = load_ai_settings()
        self.accept()

    # ---- 测试连接 ----
    def _test(self):
        worker = self._test_worker
        if worker is not None and worker.isRunning():
            return
        client = AIClient(self.values(), LANG)
        if not client.is_ready():
            self._test_lbl.setText(ai_text(client.config_problem(), LANG))
            return
        self._test_lbl.setText(tr("ai_running"))
        worker = _AITestWorker(client, self)
        worker.done.connect(self._on_test_done)
        worker.finished.connect(worker.deleteLater)
        self._test_worker = worker
        worker.start()

    def _on_test_done(self, ok, message):
        self._test_worker = None
        try:
            self._test_lbl.setText(
                tr("set_ai_test_ok", text=message[:80]) if ok else message)
            self._test_lbl.setStyleSheet(
                f"color: {C['SUCCESS'] if ok else C['DANGER']};"
                f" font-size: 12px;")
        except RuntimeError:
            pass

    def _stop_test(self):
        worker = self._test_worker
        if worker is not None and worker.isRunning():
            worker.cancel()
            worker.wait(3000)
        self._test_worker = None

    def reject(self):
        self._stop_test()
        super().reject()

    def closeEvent(self, event):
        self._stop_test()
        super().closeEvent(event)


class _StatusNote(QLabel):
    """弹层底部那行说明（生成完成 / 图片压缩提示…）。

    卡片高度不够时 Qt 会先压缩布局间距，光加 margin 会被吃掉；
    这里在文字或宽度变化时把外层容器的高度钉成「文字高度 + 上间距」，
    间距就不会再被压掉，说明文字也就稳定地待在结果区下面一点。
    """

    def __init__(self, top_gap, parent=None):
        super().__init__(parent)
        self._top_gap = int(top_gap)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._pin_wrapper_height()

    def setText(self, text):
        super().setText(text)
        self._pin_wrapper_height()

    def _pin_wrapper_height(self):
        wrap = self.parentWidget()
        if wrap is None:
            return
        try:
            need = self.heightForWidth(max(1, wrap.width()))
            target = max(1, int(need)) + self._top_gap
        except Exception:
            return
        if wrap.height() != target or wrap.minimumHeight() != target:
            wrap.setFixedHeight(target)


class _BridgeDialog(_CardOverlayDialog):
    """浏览器扩展的本机桥配置（卡片弹层，设置页因此只留一行）。"""

    def __init__(self, owner, app):
        super().__init__(owner, app, "⇄", tr("set_bridge"),
                         tr("set_bridge_sub"))
        self.app = app
        lay = self._lay
        cfg = load_config()
        _enabled, port, token = ensure_bridge_config(cfg)

        sw_row = QHBoxLayout()
        sw_lbl = QLabel(tr("set_bridge_enable"))
        sw_lbl.setObjectName("retSub")
        sw_row.addWidget(sw_lbl, 1)
        self._cb = QCheckBox()
        self._cb.setChecked(bool(cfg.get("bridge_enabled", False)))
        self._cb.toggled.connect(self._on_toggle)
        sw_row.addWidget(self._cb)
        lay.addLayout(sw_row)

        conn_row = QHBoxLayout()
        conn_row.setSpacing(8)
        conn_lbl = QLabel(tr("set_bridge_conn"))
        conn_lbl.setObjectName("retSub")
        conn_lbl.setFixedWidth(64)
        conn_row.addWidget(conn_lbl)
        self._conn = QLineEdit(conn_line(port, token))
        self._conn.setReadOnly(True)
        self._conn.setStyleSheet(
            f"QLineEdit {{ background-color: {C['INPUT_BG']};"
            f" color: {C['TEXT_SEC']}; border: 1px solid {C['BORDER']};"
            f" border-radius: 7px; padding: 5px 8px; font-size: 12px;"
            f" font-family: Consolas,monospace; }}")
        conn_row.addWidget(self._conn, 1)
        self._copy_btn = QPushButton(tr("set_bridge_copy"))
        self._copy_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._copy_btn.clicked.connect(self._copy_conn)
        conn_row.addWidget(self._copy_btn)
        lay.addLayout(conn_row)

        self._state = QLabel("")
        self._state.setObjectName("retNote")
        self._state.setWordWrap(True)
        lay.addWidget(self._state)

        hint = QLabel(tr("set_bridge_hint"))
        hint.setObjectName("retNote")
        lay.addWidget(hint)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton(tr("btn_cancel"))
        cancel.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        save = QPushButton(tr("btn_save"))
        save.setObjectName("retSave")
        save.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        save.clicked.connect(self._save_and_close)
        buttons.addWidget(save)
        lay.addLayout(buttons)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._sync_ui)
        self._timer.start(2000)
        # 打开时如果配置是"已启用"但服务没跑（比如上次启动失败），这里再试一次
        if self._cb.isChecked():
            st = self.app.bridge_status() if self.app is not None else {}
            if not st.get("running"):
                try:
                    self.app._apply_bridge()
                except Exception:
                    pass
        self._sync_ui()

    def _on_toggle(self, on):
        """开关一拨就立刻生效（以前要点了「保存」才启动，用户以为坏了）。"""
        try:
            self.app.set_bridge_enabled(bool(on))
        except Exception:
            pass
        self._sync_ui()

    def _current_line(self):
        """运行中就用真实端口，否则用配置里的端口。"""
        st = self.app.bridge_status() if self.app is not None else {}
        cfg = load_config()
        _enabled, port, token = ensure_bridge_config(cfg)
        if st.get("port"):
            port = st["port"]
        return conn_line(port or DEFAULT_BRIDGE_PORT, token)

    def _sync_ui(self, *_args):
        on = self._cb.isChecked()
        self._conn.setText(self._current_line())
        self._conn.setEnabled(on)
        self._copy_btn.setEnabled(on)
        st = self.app.bridge_status() if self.app is not None else {}
        if on and st.get("running"):
            text = tr("set_bridge_status_on", port=st.get("port") or "-")
            if st.get("last_seen_text"):
                text += "  ·  " + tr("set_bridge_seen",
                                     t=st["last_seen_text"])
            self._state.setText(text)
            self._state.setStyleSheet(
                f"color: {C['ACCENT']}; font-size: 12px;")
        else:
            err = str(st.get("error") or "")
            if on and err:
                # 启动失败要把原因说出来，否则用户只看到"未运行"无从下手
                self._state.setText(tr("set_bridge_failed", err=err[:160]))
                self._state.setStyleSheet(
                    f"color: {C['DANGER']}; font-size: 12px;")
            else:
                self._state.setText(tr("set_bridge_status_off") if on
                                    else tr("set_bridge_off"))
                self._state.setStyleSheet(
                    f"color: {C['TEXT_SEC']}; font-size: 12px;")

    def _copy_conn(self):
        try:
            set_clipboard_text(self._current_line())
        except Exception:
            return
        self._state.setText(tr("st_copied_preview",
                               n=len(self._current_line())))
        self._state.setStyleSheet(f"color: {C['SUCCESS']}; font-size: 12px;")

    def _save_and_close(self):
        cfg = load_config()
        _enabled, port, token = ensure_bridge_config(cfg)
        cfg["bridge_enabled"] = self._cb.isChecked()
        cfg["bridge_port"] = port
        cfg["bridge_token"] = token
        save_config(cfg)
        try:
            self.app._apply_bridge()      # 改完立刻生效
        except Exception:
            pass
        self.accept()

    def closeEvent(self, event):
        self._timer.stop()
        super().closeEvent(event)


class _AIDialog(_CardOverlayDialog):
    """AI 结果弹层：流式显示结果 + 复制 / 另存为新条 / 替换本条。

    与「历史保留策略」「自定义皮肤」「编辑标签」共用同一套卡片风格（同一基类）。
    client 可注入（测试用假客户端，不联网）。
    """

    CARD_WIDTH = 760

    def __init__(self, owner, app, entry, action, client=None,
                 custom_prompt="", lang=None, chat=False,
                 vault=None, vault_uid="", on_changed=None):
        self._app = app
        self._entry = entry or {}
        # 密库模式：结果落点改到密库（另存 = 存进密库，替换 = 改这条密库条目）
        self._vault = vault
        self._vault_uid = str(vault_uid or "")
        self._on_changed = on_changed
        _valid = tuple(AI_ACTIONS) + tuple(AI_IMAGE_ACTIONS) \
            + tuple(AI_FILE_ACTIONS)
        self._action = action if action in _valid else "summarize"
        self._custom = custom_prompt or ""
        self._lang = lang or LANG
        self._chat = bool(chat)
        self._settings = load_ai_settings()
        self._client = client or AIClient(self._settings, self._lang)
        self._worker = None
        self._result = ""
        self._history = []            # 对话模式：一问一答
        self._context = []            # 对话模式：上下文消息
        self._transcript = ""         # 对话模式：整段对话（导出用）
        self._answer_start = 0        # 最近一次回答在 transcript 里的起点
        self._notes = ""              # 截断 / 图片压缩这类提示，生成完也留着
        action_lbl = tr("ai_chat_title") if self._chat \
            else tr("ai_act_" + self._action)
        model = model_display_name(self._settings)
        if self._chat:
            subtitle = (tr("ai_chat_sub", model=model) if model
                        else tr("ai_chat_title"))
        else:
            subtitle = ("%s · %s" % (action_lbl, tr("ai_model", model=model))
                        if model else action_lbl)
        super().__init__(owner, app, "✦",
                         tr("ai_chat_title") if self._chat else tr("ai_title"),
                         subtitle)
        self._need_config = bool(self._client.config_problem())

        lay = self._lay
        self._out = QTextEdit()
        self._out.setReadOnly(True)
        # 200 而不是 250：给下面那行说明留出余量，否则卡片高度紧张时
        # 布局会先压掉间距（说明文字就被顶回结果区下面了）
        self._out.setMinimumHeight(200)
        self._out.setStyleSheet(
            f"QTextEdit {{ background: {C['INPUT_BG']}; color: {C['TEXT']};"
            f" border: 1px solid {C['BORDER']}; border-radius: 8px;"
            f" padding: 8px 10px; font-size: 13px; }}")
        lay.addWidget(self._out)

        self._status = _StatusNote(AI_STATUS_TOP_GAP)
        self._status.setObjectName("retNote")
        self._status.setWordWrap(True)
        # 说明文字（生成完成 / 图片压缩提示等）往下挪一点，别紧贴着结果区。
        # 用容器的上边距而不是 layout.addSpacing：卡片高度不足时 Qt 会把
        # spacer 压扁，而 contentsMargins 不会被压缩，间距稳定。
        self._status_wrap = QWidget()
        _sw = QVBoxLayout(self._status_wrap)
        _sw.setContentsMargins(0, AI_STATUS_TOP_GAP, 0, 0)
        _sw.setSpacing(0)
        _sw.addWidget(self._status)
        lay.addWidget(self._status_wrap)

        # 对话模式：一个输入框 + 发送（回车也能发）
        self._input = QLineEdit()
        self._input.setPlaceholderText(tr("ai_chat_placeholder"))
        self._input.returnPressed.connect(self._send_chat)
        self._send_btn = QPushButton(tr("ai_chat_send"))
        self._send_btn.setObjectName("retSave")
        self._send_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._send_btn.clicked.connect(self._send_chat)
        self._input_row = QWidget()
        _ir = QHBoxLayout(self._input_row)
        _ir.setContentsMargins(0, 0, 0, 0)
        _ir.setSpacing(8)
        _ir.addWidget(self._input, 1)
        _ir.addWidget(self._send_btn)
        self._input_row.setVisible(self._chat)
        lay.addWidget(self._input_row)

        top = QHBoxLayout()
        top.setSpacing(8)
        self._stop_btn = QPushButton(tr("ai_btn_stop"))
        self._retry_btn = QPushButton(tr("ai_btn_retry"))
        self._cfg_btn = QPushButton(tr("ai_open_settings"))
        for btn in (self._stop_btn, self._retry_btn, self._cfg_btn):
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._stop_btn.clicked.connect(self._stop)
        self._retry_btn.clicked.connect(self._restart)
        self._cfg_btn.clicked.connect(self._open_ai_settings)
        top.addWidget(self._stop_btn)
        top.addWidget(self._retry_btn)
        top.addWidget(self._cfg_btn)
        top.addStretch()
        lay.addLayout(top)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        bottom.addStretch()
        self._copy_btn = QPushButton(tr("ai_btn_copy"))
        self._save_btn = QPushButton(tr("ai_btn_save"))
        self._replace_btn = QPushButton(tr("ai_btn_replace"))
        self._export_btn = QPushButton(tr("ai_btn_export"))
        self._close_btn = QPushButton(tr("ai_btn_close"))
        for btn in (self._copy_btn, self._save_btn, self._replace_btn,
                    self._export_btn, self._close_btn):
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        # 这些按钮不接受键盘焦点：否则输入框一发消息就被禁用、焦点跳到「停止」，
        # 用户再按一次回车就等于按了停止（表现就是"刚发出去就 已停止生成"）
        for btn in (self._stop_btn, self._retry_btn, self._cfg_btn,
                    self._send_btn, self._copy_btn, self._save_btn,
                    self._replace_btn, self._export_btn, self._close_btn):
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._copy_btn.setObjectName("retSave")
        self._copy_btn.clicked.connect(self._use_result_copy)
        self._save_btn.clicked.connect(self._use_result_save)
        self._replace_btn.clicked.connect(self._use_result_replace)
        self._export_btn.clicked.connect(self._export_txt)
        self._close_btn.clicked.connect(self.reject)
        bottom.addWidget(self._copy_btn)
        bottom.addWidget(self._save_btn)
        bottom.addWidget(self._replace_btn)
        bottom.addWidget(self._export_btn)
        bottom.addWidget(self._close_btn)
        lay.addLayout(bottom)

        if self._entry.get("type") != "text":
            self._replace_btn.setVisible(False)
        self._sync_buttons()
        if self._need_config:
            self._out.setPlainText(tr("ai_need_config"))
            self._status.setText(ai_text(self._client.config_problem(),
                                         self._lang))
        elif self._chat:
            # 对话模式：先把上下文准备好，等用户提问
            self._out.setPlainText(tr("ai_chat_intro"))
            self._status.setText(tr("ai_chat_hint", n=AI_CHAT_MAX_TURNS))
            QTimer.singleShot(0, self._prepare_chat)
        else:
            QTimer.singleShot(0, self._start_request)

    # ---- 按钮状态 ----
    def _sync_buttons(self, running=False):
        has_result = bool(self._result.strip())
        has_export = bool((self._transcript if self._chat
                           else self._result).strip())
        self._stop_btn.setVisible(running)
        self._stop_btn.setEnabled(running)
        self._retry_btn.setEnabled(not running)
        self._cfg_btn.setVisible(self._need_config)
        self._export_btn.setEnabled(has_export)
        if self._chat:
            self._send_btn.setEnabled(not running)
            # 输入框始终可用：生成期间禁用会让焦点跳到「停止」按钮上，
            # 再按回车就变成停止生成（_send_chat 自己会忽略生成中的发送）
        for btn in (self._copy_btn, self._save_btn, self._replace_btn):
            btn.setEnabled(has_result and not running)

    def _set_body(self, text, error=False):
        """把结果区内容换掉（占位 / 最终文本 / 报错都走这里）。"""
        self._out.setPlainText(text or "")
        color = C['DANGER'] if error else C['TEXT']
        self._out.setStyleSheet(
            f"QTextEdit {{ background: {C['INPUT_BG']}; color: {color};"
            f" border: 1px solid {C['BORDER']}; border-radius: 8px;"
            f" padding: 8px 10px; font-size: 13px; }}")

    # ---- 请求 ----
    def _image_path(self):
        """图片记录对应的本地文件路径。"""
        if self._app is None:
            return ""
        try:
            name = str((self._entry or {}).get("filename") or "")
            # 密库里的图片放在 vault_files/ 下，这里直接给它绝对路径
            if name and os.path.isabs(name):
                return name
            return self._app._image_full_path(self._entry)
        except Exception:
            return ""

    def _build_request(self):
        """按记录类型组装请求：文本 / 网址按正文，图片走视觉模型，文件走清单。"""
        etype = self._entry.get("type")
        if etype == "image":
            return prepare_image_request(self._action, self._image_path(),
                                         self._settings, self._custom,
                                         self._lang)
        if etype == "file":
            return prepare_request(self._action, file_entries_text(self._entry),
                                   self._settings, self._custom, self._lang)
        return prepare_request(self._action, entry_full_text(self._entry),
                               self._settings, self._custom, self._lang)

    def _request_notes(self, request):
        """把「截断 / 图片压缩」这类提示拼成一行状态文字。"""
        notes = []
        if request.get("truncated"):
            notes.append(ai_text("truncated", self._lang,
                                 n=request.get("sent_chars", 0)))
        info = request.get("image") or {}
        if info:
            size = info.get("bytes") or 0
            size_txt = ("%.2f MB" % (size / 1048576.0)) if size else "?"
            if info.get("size"):
                notes.append(ai_text("image_sent", self._lang,
                                     w=info["size"][0], h=info["size"][1],
                                     size=size_txt))
            notes.append(ai_text("need_vision", self._lang))
        return " · ".join(notes)

    def _start_request(self):
        if self._client.config_problem():
            self._need_config = True
            self._sync_buttons()
            return
        request = self._build_request()
        self._result = ""
        self._set_body(tr("ai_running"))
        self._notes = self._request_notes(request)
        self._status.setText(self._notes)
        self._worker = _AIWorker(self._client, request["messages"], self)
        self._worker.delta.connect(self._on_delta)
        self._worker.done.connect(self._on_done)
        self._sync_buttons(running=True)
        self._worker.start()

    def _restart(self):
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(3000)
        self._worker = None
        if not self._chat:
            self._start_request()
            return
        # 对话模式：去掉上一条回答，重发最后一条提问
        while self._history and self._history[-1]["role"] == "assistant":
            self._history.pop()
        if not self._history or self._history[-1]["role"] != "user":
            return
        last = self._history.pop()["content"]
        self._transcript = self._transcript[:self._answer_start]
        self._out.setPlainText(self._transcript)
        self._send_chat_text(last, echo=False)

    def _stop(self):
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()

    def _on_delta(self, chunk):
        if not self._result and not self._chat:
            self._set_body("")          # 第一段增量到了，先把「正在生成…」清掉
        self._result += chunk
        if self._chat:
            self._append_raw(chunk)
        else:
            self._out.moveCursor(QTextCursor.MoveOperation.End)
            self._out.insertPlainText(chunk)
            self._out.moveCursor(QTextCursor.MoveOperation.End)

    def _on_done(self, ok, message):
        self._worker = None
        text = self._result
        if self._chat:
            if text.strip():
                self._history.append({"role": "assistant", "content": text})
                self._append_raw("\n")
                self._status.setText(self._compose_status(
                    tr("ai_done", n=len(text))))
            elif not ok:
                self._append_raw("\n" + message + "\n")
                self._status.setText(message)
            else:
                self._append_raw("\n")
                self._status.setText("")
            self._sync_buttons(running=False)
            return
        if text.strip():
            self._set_body(text)
        if ok:
            self._status.setText(self._compose_status(
                tr("ai_done", n=len(text))))
        elif text.strip():
            self._status.setText(self._compose_status(
                "%s · %s" % (message, tr("ai_stopped"))))
        else:
            self._set_body(message, error=True)
            self._status.setText("")
        self._sync_buttons(running=False)

    def _compose_status(self, head):
        """状态行 = 主提示 + 之前记下的说明（截断 / 图片压缩等）。"""
        parts = [p for p in (head, getattr(self, "_notes", "")) if p]
        return " · ".join(parts)

    # ---- 自由对话 ----
    def _append_raw(self, text):
        """把文字追加到对话记录区（同时镜像一份纯文本，导出 TXT 用）。"""
        if not text:
            return
        self._transcript += text
        self._out.moveCursor(QTextCursor.MoveOperation.End)
        self._out.insertPlainText(text)
        self._out.moveCursor(QTextCursor.MoveOperation.End)

    def _prepare_chat(self):
        """准备上下文（只发这一条记录 + 后续提问；图片走视觉）。"""
        etype = self._entry.get("type")
        if etype == "image":
            request = prepare_image_chat_context(self._image_path(),
                                                 self._lang)
        elif etype == "file":
            request = prepare_chat_context(file_entries_text(self._entry),
                                           self._settings, self._lang)
        else:
            request = prepare_chat_context(entry_full_text(self._entry),
                                           self._settings, self._lang)
        self._context = request["messages"]
        self._notes = self._request_notes(request)
        if self._notes:
            self._status.setText(self._notes)

    def _send_chat(self):
        if not self._chat:
            return
        text = self._input.text().strip()
        if not text:
            return
        self._input.clear()
        self._send_chat_text(text)

    def _send_chat_text(self, text, echo=True):
        if self._worker is not None and self._worker.isRunning():
            return
        if self._client.config_problem():
            self._need_config = True
            self._sync_buttons()
            return
        if not self._context:
            self._prepare_chat()
        if echo:
            self._append_raw(tr("ai_chat_me", text=text))
        self._history.append({"role": "user", "content": text})
        messages = build_chat_messages(self._context, self._history[:-1], text)
        self._start_chat_turn(messages)

    def _start_chat_turn(self, messages):
        self._result = ""
        self._answer_start = len(self._transcript)
        self._append_raw(tr("ai_chat_ai"))
        self._worker = _AIWorker(self._client, messages, self)
        self._worker.delta.connect(self._on_delta)
        self._worker.done.connect(self._on_done)
        self._sync_buttons(running=True)
        self._worker.start()

    # ---- 导出 TXT（与「文本」分类的导出同一套做法） ----
    def _export_txt(self):
        text = (self._transcript if self._chat else self._result) or ""
        if not text.strip():
            return
        path, _ = QFileDialog.getSaveFileName(
            self, tr("ai_btn_export"), "",
            f"{tr('ft_text')} (*.txt);;{tr('ft_all')} (*.*)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        except Exception as ex:
            _info_card(self, tr("dlg_error"),
                       tr("msg_export_failed", err=ex), kind="warning")
            return
        _info_card(self, tr("ai_btn_export"),
                   tr("st_exported", name=os.path.basename(path)),
                   kind="latest")

    # ---- 结果落点 ----
    def _use_result_copy(self):
        if self._app is not None:
            self._app._ai_result_copy(self._result)
        self.accept()

    def _use_result_save(self):
        if self._vault is not None:
            # 密库模式：AI 结果直接存成密库里的新条目
            name = str((self._entry or {}).get("source_name") or "")
            self._vault.add(kind="text", content=self._result, name=name)
            self._notify_changed()
        elif self._app is not None:
            self._app._ai_result_save(self._result)
        self.accept()

    def _use_result_replace(self):
        if self._vault is not None:
            if self._vault_uid:
                self._vault.update(self._vault_uid, content=self._result)
                self._notify_changed()
        elif self._app is not None:
            self._app._ai_result_replace(self._entry.get("hash"),
                                         self._result)
        self.accept()

    def _notify_changed(self):
        try:
            if callable(self._on_changed):
                self._on_changed()
        except Exception:
            pass

    def _open_ai_settings(self):
        if self._app is None:
            return
        _AISettingsDialog(self, self._app, load_ai_settings()).exec()
        self._settings = load_ai_settings()
        self._client = AIClient(self._settings, self._lang)
        self._need_config = bool(self._client.config_problem())
        self._sync_buttons()
        if not self._need_config:
            if self._chat:
                self._context = []
                self._prepare_chat()
            else:
                self._start_request()

    # ---- 关掉弹层时收线程，别把请求甩到后台 ----
    def _shutdown_worker(self):
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(3000)
        self._worker = None

    def reject(self):
        self._shutdown_worker()
        super().reject()

    def closeEvent(self, event):
        self._shutdown_worker()
        super().closeEvent(event)


class _WatermarkFrame(QFrame):
    """Light splash card with a faint tiled app-logo watermark."""

    def __init__(self, logo_path, light, parent=None):
        super().__init__(parent)
        self._pixmap = QPixmap()
        if logo_path and os.path.exists(logo_path):
            self._pixmap = QIcon(logo_path).pixmap(46, 46)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._pixmap.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5),
                            16, 16)
        painter.setClipPath(path)
        painter.setOpacity(0.045)
        marks = [
            (0.12, 0.13, -18), (0.30, 0.23, 24), (0.72, 0.10, 12),
            (0.88, 0.28, -22), (0.18, 0.48, 32), (0.82, 0.55, -14),
            (0.40, 0.70, -28), (0.10, 0.82, 16), (0.66, 0.86, 22),
        ]
        for rx, ry, angle in marks:
            painter.save()
            painter.translate(self.width() * rx, self.height() * ry)
            painter.rotate(angle)
            painter.drawPixmap(-23, -23, self._pixmap)
            painter.restore()


class _UpdateSplashDialog(QDialog):
    """Small restart window shown after the download has completed."""

    ready = pyqtSignal()

    def __init__(self, app, new_version):
        super().__init__(None)
        self._app = app
        self._pct = 3
        self._finished = False
        self._light = _is_light_theme()
        self.setWindowTitle(f"{APP_NAME} v{new_version}")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(760, 560)

        if self._light:
            card_bg, text, muted = "#f3f4f6", "#17191d", "#7d848e"
            track, chunk = "#dfe3e8", "#0b83d8"
            badge_bg = "#eceef1"
        else:
            card_bg, text, muted = "#1d1f24", "#f1f3f5", "#aeb5bd"
            track, chunk = "#30343b", "#36bdf7"
            badge_bg = "#26292f"

        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 22, 22, 22)
        card = _WatermarkFrame(LOGO_ICO, self._light)
        card.setObjectName("splashCard")
        outer.addWidget(card)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(64, 58, 64, 52)
        lay.setSpacing(8)
        lay.addStretch(1)

        logo = QLabel()
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if LOGO_ICO and os.path.exists(LOGO_ICO):
            logo.setPixmap(QIcon(LOGO_ICO).pixmap(72, 72))
        lay.addWidget(logo)

        name = QLabel(APP_NAME)
        name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name.setStyleSheet(
            f"color: {text}; font-size: 28px; font-weight: 700;"
            "font-family: \"Microsoft YaHei UI\",\"Segoe UI\",sans-serif;")
        lay.addWidget(name)

        ver = QLabel(f"v{new_version}")
        ver.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ver.setFixedHeight(28)
        ver.setStyleSheet(
            f"color: {muted}; background: {badge_bg}; border-radius: 4px;"
            "padding: 3px 12px; font-size: 13px;")
        ver_row = QHBoxLayout()
        ver_row.addStretch()
        ver_row.addWidget(ver)
        ver_row.addStretch()
        lay.addLayout(ver_row)
        lay.addStretch(2)

        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(self._pct)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(9)
        self._bar.setStyleSheet(
            f"QProgressBar {{ background: {track}; border: none;"
            f" border-radius: 4px; }}"
            f"QProgressBar::chunk {{ background: {chunk};"
            f" border-radius: 4px; }}")
        lay.addWidget(self._bar)

        self._status = QLabel()
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status.setStyleSheet(
            f"color: {muted}; font-size: 13px;"
            "font-family: \"Microsoft YaHei UI\",\"Segoe UI\",sans-serif;")
        lay.addWidget(self._status)
        card.setStyleSheet(
            "QFrame#splashCard {"
            f" background-color: {card_bg}; border: 1px solid {badge_bg};"
            " border-radius: 16px; }")

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(45)
        self._update_status()

    def _update_status(self):
        if self._pct < 82:
            text = tr("upd_preparing_status", pct=self._pct)
        elif self._pct < 97:
            text = tr("upd_replacing_status", pct=self._pct)
        else:
            text = tr("upd_finishing_status", pct=self._pct)
        self._status.setText(text)
        self._bar.setValue(self._pct)

    def _tick(self):
        if self._pct < 82:
            self._pct = min(82, self._pct + 3)
        elif self._pct < 97:
            self._pct = min(97, self._pct + 2)
        else:
            self._pct += 1
        self._update_status()
        if self._pct >= 100:
            self._timer.stop()
            self._status.setText(tr("upd_restarting"))
            QTimer.singleShot(420, self._finish)

    def _finish(self):
        self._finished = True
        self.ready.emit()
        self.accept()

    def closeEvent(self, event):
        if not self._finished:
            event.ignore()
            return
        super().closeEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._center_on_app)

    def _center_on_app(self):
        host = self._app
        try:
            if host is not None and host.isVisible():
                center = host.frameGeometry().center()
            else:
                center = QApplication.primaryScreen().availableGeometry().center()
            self.move(center.x() - self.width() // 2,
                      center.y() - self.height() // 2)
        except Exception:
            pass


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
        # 右下角不再画那三个小点：拖拽热区与斜向光标保留，视觉上不留痕迹
        return

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
def _widget_under_cursor():
    """取鼠标下方的控件——比 QApplication.widgetAt 可靠。

    QApplication.widgetAt 依赖平台的 topLevelAt：本应用自己的无边框 / 半透明窗口
    有时根本取不到（返回 None），那样下面这个光标兜底刷新就整条失效，表现就是
    "从上往下扫过按钮时手型光标不出现，只有反向才有"。这里直接沿着应用自己的
    窗口树用 childAt 找，不依赖平台。
    """
    pos = QCursor.pos()
    try:
        cands = []
        for tl in QApplication.topLevelWidgets():
            if not tl.isVisible() or tl.isMinimized():
                continue
            if not tl.frameGeometry().contains(pos):
                continue
            is_dialog = bool(tl.windowFlags() & Qt.WindowType.Dialog)
            cands.append((0 if is_dialog else 1,
                          tl.width() * tl.height(), tl))
        # 多个窗口重叠时（对话框盖在主窗口上），优先对话框、再挑面积最小的
        for _rank, _area, tl in sorted(cands, key=lambda x: (x[0], x[1])):
            child = tl.childAt(tl.mapFromGlobal(pos))
            w = child if child is not None else tl
            # childAt 可能返回按钮内部的装饰子控件，向上找最近的可点控件
            for _ in range(4):
                if isinstance(w, (QPushButton, QCheckBox, QComboBox)):
                    break
                parent = w.parentWidget()
                if parent is None:
                    break
                w = parent
            return w
    except Exception:
        pass
    return None


def _apply_hand_cursor(root):
    """给还没设光标的可点控件补上手型（按钮 / 开关 / 下拉）。

    之前只有部分按钮设了手型，开关和少数按钮没有，导致"同样是能点的东西，
    光标却不一样"。这里统一补齐（已禁用或不可见的跳过）。
    """
    try:
        widgets = list(root.findChildren(QWidget)) + [root]
    except Exception:
        return
    for w in widgets:
        if not isinstance(w, (QPushButton, QCheckBox, QComboBox)):
            continue
        if not w.isEnabled() or not w.isVisible():
            continue
        if w.cursor().shape() != Qt.CursorShape.PointingHandCursor:
            w.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))


def _refresh_cursor_under_mouse(widget=None):
    """鼠标下方控件的光标兜底刷新（设置 / 云同步窗口里的定时器调用）。

    以前这里会把"当前生效的光标"复制成子控件自己的光标。子控件一旦被复制上
    缩放箭头（比如贴过窗口边缘之后），父窗口再按位置重算也改不动它——表现就是
    打开设置窗口后光标被锁在缩放箭头上。现在只做两件安全的事：
      ① 缩放热区（自带缩放光标的细条）保持不动；
      ② 误钉在普通控件上的缩放光标清掉，让它继续继承父窗口按位置算出来的光标；
      ③ 自己设过光标的手型按钮（滚动时容易不刷新）重新应用一次。
    """
    try:
        w = widget if widget is not None else _widget_under_cursor()
        if w is None or isinstance(w, (_EdgeHandle, _CornerHandle, _ResizeGrip)):
            return
        # 桌面小组件靠"贴边=缩放箭头"表达可缩放，它自己设的缩放光标不能被这里清掉
        # （以前鼠标一经过小组件，光标刚变成缩放箭头就被这里 unsetCursor 弹回默认箭头）
        try:
            if isinstance(w.window(), DesktopClipboardWidget):
                return
        except Exception:
            pass
        shape = w.cursor().shape()
        if shape in (Qt.CursorShape.SizeHorCursor, Qt.CursorShape.SizeVerCursor,
                     Qt.CursorShape.SizeFDiagCursor,
                     Qt.CursorShape.SizeBDiagCursor,
                     Qt.CursorShape.SizeAllCursor):
            w.unsetCursor()
        elif (shape != Qt.CursorShape.ArrowCursor
              and w.testAttribute(Qt.WidgetAttribute.WA_SetCursor)):
            w.setCursor(w.cursor())
    except Exception:
        pass


class _TitleBar(QWidget):
    """Flat custom title bar with window control buttons."""
    HEIGHT = 36

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

    def _build(self):
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 0, 0)
        lay.setSpacing(3)
        self._light = _is_light_theme()
        hover_bg = f"background: {C['SURFACE3']};"
        close_hover = f"background: {C['DANGER']};"

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
        self._title_lbl.setStyleSheet(
            f"color: {C['TEXT']}; font-size: 12px; font-weight: 600;"
            " background: transparent;")
        lay.addWidget(self._title_lbl)
        lay.addStretch()
        # Window buttons (icon-based)
        # 直角 + 铺满标题栏高度：关闭按钮贴满窗口右上角，消除圆角露出的缝隙。
        btn_style_base = (
            "border: none; border-radius: 0; padding: 0; margin: 0; "
            "min-width: 36px; max-width: 36px; min-height: 36px; max-height: 36px; "
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
            btn_style_base + close_hover), self.update())[1]
        self._close_btn.leaveEvent = lambda e: (self._close_btn.setStyleSheet(
            btn_style_base + "background: transparent;"), self.update())[1]
        lay.addWidget(self._close_btn)

    def _toggle_max(self):
        if self._win._is_window_maximized():
            # 用记住的普通大小还原：即使 Qt 状态位没跟上，也能确定缩回去
            self._win._restore_normal_size()
            self._max_btn.setIcon(self._icon_max)
        else:
            self._win.showMaximized()
            self._max_btn.setIcon(self._icon_rest)
        self._max_btn.repaint()
        self.update()

    def update_max_btn(self):
        self._max_btn.setIcon(self._icon_rest
                              if self._win._is_window_maximized() else self._icon_max)
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
            if self._win._is_window_maximized():
                # Un-maximize on drag
                self._win._restore_normal_size()
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
        # Flat title bar: no continuous repaint.
        self._shimmer_timer.stop()

    def _tick_shimmer(self):
        self._shimmer_offset += 0.02
        if self._shimmer_offset > 1.0:
            self._shimmer_offset -= 1.0
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        # 有自定义背景时标题栏半透明，让壁纸透上来
        p.fillRect(self.rect(), qcolor(
            C['GLASS_HEADER'] if _GLASS_ACTIVE else C['SURFACE']))
        p.setPen(QPen(QColor(C['BORDER']), 1))
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
        # 右键按下期间不把「点击条目」当成左键复制（右键要弹菜单）
        self._right_press = False
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
        # 边缘缩放光标就不会触发；统一开启 mouseTracking + 事件过滤。
        self._card.setMouseTracking(True)
        self._track_child_cursor()

    def showEvent(self, event):
        super().showEvent(event)
        self._track_child_cursor()   # 内容变化后新建的子控件也要跟得上光标
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
        card_bg, txt, sec = C['PANEL_ALPHA'], C['TEXT'], C['TEXT_SEC']
        border = C['BORDER_LT']
        hover = ("rgba(0,0,0,14)" if _is_light_theme()
                 else "rgba(255,255,255,18)")
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
        if getattr(self, "_right_press", False):
            # 右键用于弹「关闭小组件」菜单，不当作左键复制
            self._right_press = False
            return
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

    def _show_context_menu(self, global_pos):
        """右键菜单：手动关闭小组件（想再显示在设置里重新打开）。"""
        self._right_press = False
        try:
            menu = _RoundMenu(self)
            # 小组件本身很小，菜单跟着用紧凑规格（字号 / 内边距都收一号）
            menu.setStyleSheet(f"""
                QMenu {{ background: {C['SURFACE2']}; color: {C['TEXT']};
                    border: 1px solid {C['BORDER_LT']}; border-radius: 8px;
                    padding: 3px; font-size: 11px; }}
                QMenu::item {{ padding: 4px 14px; border-radius: 5px; }}
                QMenu::item:selected {{ background: {C['ACCENT_DIM']};
                    color: {C['ACCENT']}; }}
            """)
            act_close = menu.addAction(tr("widget_close"))
            chosen = menu.exec(global_pos)
        except Exception:
            return
        if chosen is act_close:
            self._close_to_config()

    def contextMenuEvent(self, event):
        self._show_context_menu(event.globalPos())
        event.accept()

    # ---- drag / resize / persist geometry ----
    # 光标：子控件会继承父窗口的光标，而鼠标停在子控件上时父窗口收不到 move 事件，
    # 于是"贴过边缘"之后箭头会一直卡在缩放光标上（点一次设置、鼠标从边缘移开就会
    # 触发）。这里让子控件也把鼠标移动报给本窗口，光标准确跟随实际位置。
    _ZONE_CURSORS = {
        "corner": Qt.CursorShape.SizeFDiagCursor,
        "corner_tl": Qt.CursorShape.SizeFDiagCursor,
        "corner_l": Qt.CursorShape.SizeBDiagCursor,
        "corner_tr": Qt.CursorShape.SizeBDiagCursor,
        "hedge_l": Qt.CursorShape.SizeHorCursor,
        "hedge_r": Qt.CursorShape.SizeHorCursor,
        "vedge": Qt.CursorShape.SizeVerCursor,
        "tedge": Qt.CursorShape.SizeVerCursor,
    }

    def _track_child_cursor(self):
        """让所有子控件也汇报鼠标移动（并开启 hover 跟踪）。"""
        tracked = getattr(self, "_cursor_tracked", None)
        if tracked is None:
            tracked = self._cursor_tracked = set()
        for w in [self] + self.findChildren(QWidget):
            if w in tracked:
                continue
            tracked.add(w)
            w.setMouseTracking(True)
            w.installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.MouseButtonPress \
                and event.button() == Qt.MouseButton.RightButton:
            self._right_press = True
        elif event.type() == QEvent.Type.ContextMenu:
            # 子控件（列表 / 标签）上的右键也要冒到这里：小组件只提供「关闭」
            self._show_context_menu(event.globalPos())
            return True
        if event.type() in (QEvent.Type.MouseMove, QEvent.Type.HoverMove,
                            QEvent.Type.Enter, QEvent.Type.Leave):
            self._apply_zone_cursor(self.mapFromGlobal(QCursor.pos()))
        return super().eventFilter(obj, event)

    def _apply_zone_cursor(self, pos):
        """按鼠标在窗口里的位置切换光标：贴边=缩放箭头，中间=普通箭头。"""
        cur = self._ZONE_CURSORS.get(self._hit_zone(pos))
        if cur is None:
            self.unsetCursor()
        else:
            self.setCursor(QCursor(cur))

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
        self._apply_zone_cursor(self.mapFromGlobal(QCursor.pos()))
        self.setWindowOpacity(self.HOVER_OPACITY)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.unsetCursor()
        self.setWindowOpacity(self.IDLE_OPACITY)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            # 右键：上下文菜单由 _show_context_menu 处理
            self._right_press = True
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._right_press = False
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
        self._apply_zone_cursor(event.position().toPoint())

    def mouseReleaseEvent(self, event):
        changed = bool(self._press_active)
        self._resize_mode = None
        self._resize_origin = None
        self._resize_geo = None
        self._drag_pos = None
        self._press_pos = None
        self._press_zone = None
        self._press_active = False
        # 松手后按真实位置重算光标（还贴着边缘就继续显示缩放箭头，回到中间就是普通箭头）
        self._apply_zone_cursor(self.mapFromGlobal(QCursor.pos()))
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
        path.addRoundedRect(r, 10.0, 10.0)
        # 有壁纸时弹层也用半透明底色，避免出现一块死黑
        p.fillPath(path, qcolor(
            C['GLASS_CARD'] if _GLASS_ACTIVE else C['SURFACE']))
        p.setPen(QPen(QColor(C['BORDER_LT']), 1.0))
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
            QPushButton {{ background: {surface_bg('SURFACE2', glass_key='GLASS_BUTTON')};
                color: {C['TEXT_SEC']};
                border: 1px solid {C['BORDER']}; border-radius: 8px; padding: 7px 11px;
                font-size: 12px; text-align: left; }}
            QPushButton:hover {{ border-color: {C['BORDER_LT']}; color: {C['TEXT']};
                background: {surface_bg('SURFACE3', glass_key='GLASS_BUTTON')}; }}
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
        # 不要系统方角投影：它会在圆角面板外圈糊出一个直角灰框
        pop.setWindowFlag(Qt.WindowType.NoDropShadowWindowHint, True)
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
        # 四角裁圆 + 角外不绘制：避免露出方块底
        _round_window_corners(pop, 10)

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


class _AutoHideLabel(QLabel):
    """空文本时自动隐藏：避免状态栏右侧留下两个空标签的小痕迹。"""

    def setText(self, text):
        super().setText(text)
        try:
            self.setVisible(bool(str(text).strip()))
        except Exception:
            pass


class YouBoardApp(QMainWindow):

    def __init__(self, store, monitor=None):
        super().__init__()
        # 托盘程序：任何窗口被关掉都不该让进程自己结束（只有托盘退出 / 主窗口
        # closeEvent 里的显式 quit 才退出），否则收进托盘或关掉某个对话框后
        # 程序会悄悄消失，用户还得手动再打开一次
        _app_inst = QApplication.instance()
        if _app_inst is not None:
            _app_inst.setQuitOnLastWindowClosed(False)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.store = store
        self.monitor = monitor
        # 密库（3.3.1）：与剪贴板历史分开的加密存储，只由用户手动维护
        self.vault = VaultStore()
        # 默认落在第一个标签（3.1.0 起是「全部」），与 QTabWidget 的初始索引保持一致
        self._active_type = TAB_TYPES[0]
        self._tables = {}
        self._tab_layouts = []
        # 每个标签页里“按内容撑开”的那一列（横向滚动条靠它才能左右拖动看全文）
        self._flex_cols = {}
        # 「文件」标签里的细分筛选（all / video / image / design / audio / doc /
        # archive / app / code / font / other），只影响这个标签的显示
        self._file_kind = "all"
        self._file_kind_btns = {}
        # 标签 / 收藏筛选（3.2.7）：只看收藏 / 只看某个标签，作用于所有标签页
        self._fav_only = False
        self._tag_filter = None
        self._tag_chips = {}          # 标签页 → {标签小写: (原名, 按钮)}
        self._fav_chips = {}          # 标签页 → 收藏按钮
        self._filter_rows = {}        # 标签页 → (容器, 布局)
        self._filter_sig = {}         # 标签页 → 上次画筛选行时用的签名
        self._preview_hash = None
        # 需要按内容自适应宽度的小列（数量 / 格式 / 尺寸 / 大小）
        self._auto_width_cols = {"image": (4, 5, 6), "file": (4, 5, 6)}
        self._iid_to_hash = {t: {} for t in TAB_TYPES}
        self._search_edits = {}
        self._search_timers = {}
        self._count_labels = {}
        self._sort_orders = {t: "default" for t in TAB_TYPES}
        self._sort_combos = {}
        self._entry_index = {}
        self._pinned_hashes = set()
        self._preview_gen = 0
        self._cur_image_path = None
        self._cur_image_entry = None
        self._cur_text_entry = None
        self._cached_pil = None
        self._cached_path = None
        self._cached_qpixmap = None
        self._cached_qpixmap_path = None
        self._last_render_key = None
        self._status_timer = None
        self._dot_phase = 0
        self._last_self_copy = 0.0
        # 3.1.0：提示音事件队列（钩子线程 → 界面线程）
        self._paste_sound_q = queue.Queue()
        self._hist_ids = []
        self.restart_flag = False
        self._bg_movie = None
        self._bg_pixmap = None
        self._bg_source = None          # 背景原图（缩放时反复重铺用，避免变形）
        self._bg_resize_timer = None
        self._image_loader = None
        self._pending_image = None
        self._fade_anim = None
        self._max_before_hide = False
        self._last_geo = None      # 记住最后一次真实的窗口位置/大小
        self._last_max = False     # 记住最后一次是否最大化
        self._state_ready = False  # 启动稳定后才允许自动保存窗口状态
        self._resize_edge = 0
        self._resize_start_geo = None
        self._resize_start_pos = None

        self.setWindowTitle(tr("win_title"))
        self.resize(1180, 720)
        self.setMinimumSize(920, 540)
        # 无边框窗口：留 1px 窗体边框当描边，让窗口和桌面分得清
        self.setContentsMargins(1, 1, 1, 1)
        # Restore saved window geometry
        cfg = load_config()
        # 设置了背景图时启用半透明面板（让壁纸透出来）；没背景时保持原来的实心观感
        set_glass_active(bg_configured(cfg))
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
        self._restore_maximized = bool(cfg.get("win_maximized", False))
        if self._restore_maximized:
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
        self._track_edge_cursor()   # 边缘缩放光标：子控件也要汇报鼠标移动
        self._apply_background()
        QTimer.singleShot(150, self._initial_refresh)
        QTimer.singleShot(250, self._focus_search)
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_monitor)
        self._poll_timer.start(120)
        # 文件失效即时检测：源文件删除后当场置灰，无需手动刷新
        self._file_missing = {}
        self._file_status_worker = None
        self._file_check_timer = QTimer(self)
        self._file_check_timer.timeout.connect(self._check_files_changed)
        self._file_check_timer.start(2000)
        QTimer.singleShot(500, self._check_files_changed)
        # 图片/物化文件缓存回收：启动后执行一次，之后每 10 分钟检查
        self._cache_cleanup_worker = None
        self._cache_cleanup_pending = False
        self._cache_cleanup_timer = QTimer(self)
        self._cache_cleanup_timer.timeout.connect(self._schedule_cache_cleanup)
        self._cache_cleanup_timer.start(10 * 60 * 1000)
        self._retention_deadline_timer = QTimer(self)
        self._retention_deadline_timer.setSingleShot(True)
        self._retention_deadline_timer.timeout.connect(
            self._on_retention_deadline)
        QTimer.singleShot(3000, self._schedule_cache_cleanup)
        QTimer.singleShot(1000, self._schedule_retention_deadline)
        self._animate_dot()
        # 桌面小组件（可选功能，默认开启；延迟创建不影响启动速度）
        self._desk_widget = None
        QTimer.singleShot(600, self._apply_desktop_widget)
        # 浏览器扩展桥（只监听 127.0.0.1，默认关闭；设置里打开）
        self._bridge = None
        self._bridge_error = ""
        QTimer.singleShot(900, self._apply_bridge)
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
        central.setObjectName("rootSurface")
        self.setCentralWidget(central)
        self._bg_label = QLabel(central)
        self._bg_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # 注意：不能用 setScaledContents（那是"不管比例强行拉伸"，会把背景拉变形），
        # 缩放时改由 _scale_bg(smooth=False) 按比例重铺，见 resizeEvent
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
        _panel_bg = surface_bg()
        # 有背景时分隔条自身不画底色：多层半透明叠加会把壁纸压黑
        _split_bg = "transparent" if is_glass_active() else _panel_bg
        self._splitter.setStyleSheet(
            f"QSplitter {{ background: {_split_bg}; }}"
            f"QSplitter::handle {{ background: {_split_bg}; }}")
        root.addWidget(self._splitter, 1)

        self._tabs = QTabWidget()
        self._tabs.setAutoFillBackground(True)
        _tabs_pal = self._tabs.palette()
        if is_glass_active():
            _tabs_pal.setColor(QPalette.ColorRole.Window, qcolor(C['GLASS_PANE']))
        else:
            _tabs_pal.setColor(QPalette.ColorRole.Window, QColor(C['SURFACE']))
        self._tabs.setPalette(_tabs_pal)
        self._tabs.setStyleSheet(
            f"QTabWidget {{ background: {_panel_bg}; }}"
            f"QTabBar {{ background: {_panel_bg}; }}")
        self._tabs.currentChanged.connect(self._on_tab_changed)
        self._splitter.addWidget(self._tabs)

        right_split = QSplitter(Qt.Orientation.Vertical)
        right_split.setHandleWidth(6)
        right_split.setStyleSheet(
            f"QSplitter {{ background: {_split_bg}; }}"
            f"QSplitter::handle {{ background: {_split_bg}; }}")
        self._splitter.addWidget(right_split)
        self._build_preview_panel(right_split)
        self._build_history_panel(right_split)
        right_split.setSizes([400, 200])
        self._splitter.setSizes([700, 380])

        self._tabs.blockSignals(True)
        self._tabs.setIconSize(QSize(18, 18))
        for etype in TAB_TYPES:
            tab_w = QWidget()
            self._tabs.addTab(tab_w, f"  {self._type_label(etype)}  0  ")
            _icon_file = TAB_ICON_FILES.get(etype)
            self._tabs.setTabIcon(
                self._tabs.count() - 1,
                QIcon(_res_icon(_icon_file)) if _icon_file else _all_tab_icon())
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
                background: {surface_bg(glass_key='GLASS_HEADER')};
                border: none;
                border-bottom: 1px solid {C['BORDER']};
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

        vault_btn = QPushButton()
        vault_btn.setIcon(_lock_icon(20, C['TEXT']))
        vault_btn.setIconSize(QSize(20, 20))
        vault_btn.setFixedSize(30, 26)
        vault_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        vault_btn.setToolTip(tr("vault_btn"))
        vault_btn.setStyleSheet(
            "QPushButton { border: none; background: transparent; border-radius: 6px; }"
            "QPushButton:hover { background: rgba(128,128,128,0.18); }"
            "QPushButton:pressed { background: rgba(128,128,128,0.30); }")
        vault_btn.clicked.connect(self._open_vault)
        hl.addWidget(vault_btn)
        self._vault_btn = vault_btn

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
            self._bg_source = self._bg_pixmap      # 原图（用于反复重铺，避免变形）
            self._scale_bg()

    def _on_bg_frame(self):
        if self._bg_movie:
            self._bg_pixmap = self._bg_movie.currentPixmap()
            self._scale_bg()

    def _scale_bg(self, smooth=True):
        """把背景图按"保持比例、铺满窗口（超出部分裁掉）"绘制。

        smooth=True 用高质量平滑缩放（默认，静止时用）；
        smooth=False 用快速缩放（拖动窗口过程中用，保证不卡、也不露底）。
        两种方式都保持宽高比，绝不会把背景拉伸变形。
        """
        is_gif = getattr(self, "_bg_is_gif", False)
        pm = self._bg_source if not is_gif else self._bg_pixmap
        if pm is None or pm.isNull():
            pm = self._bg_pixmap
        return self._scale_bg_with(pm, smooth)

    def _scale_bg_with(self, pm, smooth=True):
        is_gif = getattr(self, "_bg_is_gif", False)
        path = getattr(self, "_bg_image_path", "")
        # 静态图：缓存比窗口小（窗口放大）或缓存丢失时，从磁盘重新加载
        if (not is_gif and path and os.path.exists(path)
                and (pm is None or pm.isNull()
                     or self.width() > pm.width() or self.height() > pm.height())):
            pm = QPixmap(path)
            self._bg_source = pm
        if pm and not pm.isNull():
            scaled = pm.scaled(
                self.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation if smooth
                else Qt.TransformationMode.FastTransformation)
            self._bg_label.setPixmap(scaled)
            self._bg_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._bg_label.setGeometry(self.rect())
            if not is_gif:
                # 只保留缩放后的窗口尺寸副本，释放原图大图内存
                self._bg_pixmap = scaled

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._record_window_state()
        self._schedule_state_save()
        # 窗口大小变化时同步标题栏的最大化/还原图标
        # （被系统直接最大化时不会发 WindowStateChange，只能靠这里兜底）
        if hasattr(self, '_title_bar'):
            self._title_bar.update_max_btn()
        if hasattr(self, '_bg_label'):
            self._bg_label.setGeometry(self.rect())
            if self._bg_pixmap and not self._bg_pixmap.isNull():
                # 立刻按比例"快速重铺"一次：比例不变形、不会露底，而且几乎不耗时
                try:
                    self._scale_bg(smooth=False)
                except Exception:
                    pass
                if self._bg_resize_timer:
                    self._bg_resize_timer.stop()
                self._bg_resize_timer = QTimer()
                self._bg_resize_timer.setSingleShot(True)
                self._bg_resize_timer.timeout.connect(self._scale_bg)
                self._bg_resize_timer.start(60)   # 停手后再做一次高质量平滑缩放
        # 预览图不在拖动过程中重绘：改成"缩放停下来之后再重绘一次"。
        # 之前每次 resize 事件都清缓存键并做一次平滑缩放，拖动时明显卡顿。
        if (hasattr(self, '_cached_qpixmap') and self._cached_qpixmap
                and not self._cached_qpixmap.isNull()):
            t = getattr(self, '_preview_resize_timer', None)
            if t is None:
                t = QTimer(self)
                t.setSingleShot(True)
                t.timeout.connect(self._on_preview_resize_settled)
                self._preview_resize_timer = t
            t.start(90)
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
            # 已经显示/在最上层时不再重复 show/raise，避免每次都触发重绘
            for _h in (self._edge_left, self._edge_right,
                       self._edge_top, self._edge_bottom):
                if not _h.isVisible():
                    _h.show()
                    _h.raise_()
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
            # 最大化/还原后记录并保存状态，异常退出也不会丢
            self._record_window_state()
            self._schedule_state_save()
            # 状态切换后几何要等一拍才最终确定，延迟再记一次，避免记到过渡值
            QTimer.singleShot(180, self._record_window_state)
            QTimer.singleShot(240, self._schedule_state_save)

    def moveEvent(self, event):
        super().moveEvent(event)
        self._record_window_state()
        self._schedule_state_save()

    def showEvent(self, event):
        super().showEvent(event)
        self._resume_motion()
        self._track_edge_cursor()          # 子控件也要汇报鼠标移动（光标才跟着走）
        _apply_hand_cursor(self)           # 可点控件统一手型光标
        QTimer.singleShot(250, self._check_files_changed)

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

    def _track_edge_cursor(self):
        """让主窗口的所有子控件也汇报鼠标移动。

        子控件会继承窗口光标，而鼠标停在子控件上时窗口收不到 mouseMove，
        于是"贴着边缘拖过一次"之后，鼠标回到窗口中间仍然显示缩放箭头。
        """
        tracked = getattr(self, "_edge_cursor_tracked", None)
        if tracked is None:
            tracked = self._edge_cursor_tracked = set()
        for w in [self] + self.findChildren(QWidget):
            if w in tracked:
                continue
            tracked.add(w)
            w.setMouseTracking(True)
            w.installEventFilter(self)

    def eventFilter(self, obj, event):
        et = event.type()
        # 记录"最后一次用户操作"：空闲久了再回收工作集，不影响正在用的手感
        if et in (QEvent.Type.MouseButtonPress, QEvent.Type.KeyPress,
                  QEvent.Type.Wheel, QEvent.Type.MouseMove,
                  QEvent.Type.TouchBegin):
            self._last_input = time.time()
        if et in (QEvent.Type.MouseMove, QEvent.Type.HoverMove,
                            QEvent.Type.Enter, QEvent.Type.Leave):
            self._apply_edge_cursor(self.mapFromGlobal(QCursor.pos()))
        return super().eventFilter(obj, event)

    def _apply_edge_cursor(self, pos):
        """按鼠标在窗口里的位置切换光标：贴边=缩放箭头，其余=普通箭头。"""
        self.setCursor(self._EDGE_CURSORS.get(self._edge_at(pos),
                                              Qt.CursorShape.ArrowCursor))

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
        # 松手后按当前位置重算一次：否则贴边拖过之后，鼠标回到窗口中间仍然是
        # 缩放箭头（主窗口没有收到新的 mouseMove 前不会再更新光标）。
        self.setCursor(self._EDGE_CURSORS.get(
            self._edge_at(event.position().toPoint()),
            Qt.CursorShape.ArrowCursor))
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event):
        self.unsetCursor()
        super().leaveEvent(event)

    def enterEvent(self, event):
        # 鼠标重新进入窗口时按当前位置重算光标：避免上一次贴边留下的缩放箭头
        # 在不动鼠标的情况下一直显示
        self._apply_edge_cursor(self.mapFromGlobal(QCursor.pos()))
        super().enterEvent(event)

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
        return {"all": tr("type_all"), "text": tr("type_text"),
                "image": tr("type_image"), "file": tr("type_file"),
                "url": tr("type_url")}.get(etype, str(etype))

    @staticmethod
    def _file_kind_label(key):
        """文件细分的显示名（视频 / 设计源文件 / 压缩包 …）。"""
        return tr("fk_" + key)

    def _set_file_kind(self, key):
        self._file_kind = key
        self._refresh_tab("file")

    def _paint_file_kind_chips(self, counts):
        """刷新文件细分按钮：空分类隐藏（当前选中的始终留着），并显示各自数量。"""
        if not self._file_kind_btns:
            return
        active = self._file_kind
        for key, chip in self._file_kind_btns.items():
            n = counts.get(key, 0)
            if key != "all" and n == 0 and key != active:
                chip.setVisible(False)
                continue
            chip.setVisible(True)
            label = self._file_kind_label(key)
            chip.setText(f"{label} {n}" if n else label)
            if key == active:
                chip.setStyleSheet(
                    f"QPushButton {{ background: {C['ACCENT_DIM']};"
                    f" color: {C['ACCENT']}; border: 1px solid {C['ACCENT']};"
                    f" border-radius: 11px; padding: 3px 10px; font-size: 11px;"
                    f" font-weight: 600; }}")
            else:
                chip.setStyleSheet(
                    f"QPushButton {{ background: {C['SURFACE2']};"
                    f" color: {C['TEXT_SEC']}; border: 1px solid {C['BORDER']};"
                    f" border-radius: 11px; padding: 3px 10px; font-size: 11px; }}"
                    f"QPushButton:hover {{ background: {C['SURFACE3']};"
                    f" color: {C['TEXT']}; }}")

    # ---- 标签 / 收藏筛选（3.2.7） ----
    def _build_filter_row(self, lay, etype):
        """标签 / 收藏筛选行：贴着列表的一排小胶囊，有标签或收藏时才显示。"""
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        # 同「文件」细分那排：行尾留白显式给 stretch，否则宽窗口下胶囊会被摊开
        row.addStretch(1)
        holder.setVisible(False)
        lay.addWidget(holder)
        self._filter_rows[etype] = (holder, row)

    def _style_filter_chip(self, chip, label, count, active, force_show=False):
        """筛选胶囊画法：与「文件」细分那排完全同一套样式。"""
        if count <= 0 and not force_show:
            chip.setVisible(False)
            return
        chip.setVisible(True)
        chip.setText(f"{label} {count}" if count else label)
        if active:
            chip.setStyleSheet(
                f"QPushButton {{ background: {C['ACCENT_DIM']};"
                f" color: {C['ACCENT']}; border: 1px solid {C['ACCENT']};"
                f" border-radius: 11px; padding: 3px 10px; font-size: 11px;"
                f" font-weight: 600; }}")
        else:
            chip.setStyleSheet(
                f"QPushButton {{ background: {C['SURFACE2']};"
                f" color: {C['TEXT_SEC']}; border: 1px solid {C['BORDER']};"
                f" border-radius: 11px; padding: 3px 10px; font-size: 11px; }}"
                f"QPushButton:hover {{ background: {C['SURFACE3']};"
                f" color: {C['TEXT']}; }}")

    def _refresh_filters(self, etype, entries):
        """重画标签 / 收藏筛选行，并返回筛选后的记录列表。"""
        fav_n = sum(1 for e in entries if e.get("fav"))
        counts = {}
        for e in entries:
            for tag in entry_tags(e):
                counts[tag] = counts.get(tag, 0) + 1
        self._paint_tag_filter(etype, counts, fav_n)
        if not self._fav_only and not self._tag_filter:
            return entries
        want = (self._tag_filter or "").lower()
        out = []
        for e in entries:
            if self._fav_only and not e.get("fav"):
                continue
            if want and want not in [t.lower() for t in entry_tags(e)]:
                continue
            out.append(e)
        return out

    def _paint_tag_filter(self, etype, counts, fav_n):
        holder = self._filter_rows.get(etype)
        if holder is None:
            return
        row_w, row = holder
        sig = (tuple(sorted(k.lower() for k in counts)),
               fav_n > 0, self._fav_only, (self._tag_filter or "").lower())
        if self._filter_sig.get(etype) != sig:
            # 只有标签集合 / 筛选状态变了才重建按钮，否则只刷新数量与高亮，
            # 免得每次捕获新内容都重建控件闪一下
            self._filter_sig[etype] = sig
            while row.count():
                item = row.takeAt(0)
                w = item.widget()
                if w is not None:
                    w.deleteLater()
            self._tag_chips[etype] = {}
            fav_chip = QPushButton(tr("filter_fav"))
            fav_chip.setFlat(True)
            fav_chip.setIcon(_star_icon(14))
            fav_chip.setIconSize(QSize(12, 12))
            fav_chip.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            fav_chip.clicked.connect(self._toggle_fav_filter)
            row.addWidget(fav_chip)
            self._fav_chips[etype] = fav_chip
            for tag in sorted(counts, key=lambda t: (-counts[t], t.lower())):
                chip = QPushButton()
                chip.setFlat(True)
                chip.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
                chip.clicked.connect(
                    lambda _, t=tag: self._toggle_tag_filter(t))
                row.addWidget(chip)
                self._tag_chips[etype][tag.lower()] = (tag, chip)
            row.addStretch(1)
        active = (self._tag_filter or "").lower()
        fav_chip = self._fav_chips.get(etype)
        if fav_chip is not None:
            self._style_filter_chip(fav_chip, tr("filter_fav"), fav_n,
                                    self._fav_only, force_show=self._fav_only)
        for key, (tag, chip) in self._tag_chips.get(etype, {}).items():
            self._style_filter_chip(chip, "#" + tag, counts.get(tag, 0),
                                    key == active, force_show=(key == active))
        row_w.setVisible(bool(counts) or fav_n > 0 or self._fav_only
                         or bool(self._tag_filter))

    def _toggle_fav_filter(self):
        """只看收藏 / 取消：作用在当前标签页上。"""
        self._fav_only = not self._fav_only
        self._refresh_tab(self._active_type)
        self._update_preview()
        if self._fav_only:
            n = self.store.fav_count(
                None if self._active_type == "all" else self._active_type)
            self._set_status(tr("st_fav_filter", n=n))
        else:
            self._set_status(tr("st_fav_filter_off"))

    def _toggle_tag_filter(self, tag):
        """点标签胶囊：只看该标签；再点一次取消。"""
        tag = normalize_tag(tag)
        if not tag:
            return
        if (self._tag_filter or "").lower() == tag.lower():
            self._tag_filter = None
            self._refresh_tab(self._active_type)
            self._update_preview()
            self._set_status(tr("st_tag_filter_off"))
            return
        self._tag_filter = tag
        self._refresh_tab(self._active_type)
        self._update_preview()
        self._set_status(tr("st_tag_filter", tag=tag))

    def _clear_tag_filter(self):
        if not self._tag_filter and not self._fav_only:
            return
        self._tag_filter = None
        self._fav_only = False
        self._refresh_tab(self._active_type)
        self._update_preview()

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

        sort_ids = (["default", "oldest"] if etype in ("all", "text", "url") else
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
        # 收藏 / 取消收藏（多选时整批处理，和「置顶」的多选行为一致）
        fav_btn = QPushButton(tr("btn_fav"))
        fav_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        fav_btn.setIcon(_star_icon(14))
        fav_btn.setIconSize(QSize(13, 13))
        fav_btn.clicked.connect(self._toggle_fav_selected)
        act_row.addWidget(fav_btn)
        for label, slot in [(tr("btn_pin"), self._pin_selected),
                            (tr("btn_unpin"), self._unpin_selected),
                            (tr("btn_delete"), self._delete_selected)]:
            btn = QPushButton(label)
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn.clicked.connect(slot)
            act_row.addWidget(btn)
        act_row.addStretch()
        if etype in ("all", "image", "file", "url"):
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

        if etype == "file":
            # 文件细分：视频 / 图片 / 设计源文件 / 音频 / 文档 / 压缩包 / 程序 /
            # 代码 / 字体 / 其他（只在「文件」标签里，不影响「全部」）
            kind_row = QHBoxLayout()
            kind_row.setSpacing(6)
            self._file_kind = "all"
            self._file_kind_btns = {}
            for key in ("all",) + FILE_KINDS:
                chip = QPushButton(self._file_kind_label(key))
                chip.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
                chip.setFlat(True)
                chip.clicked.connect(lambda _, k=key: self._set_file_kind(k))
                kind_row.addWidget(chip)
                self._file_kind_btns[key] = chip
            # 行尾留白必须显式给 stretch：只写 addStretch()（stretch=0）时，
            # 窗口一拉宽，多出来的宽度会被平均摊到各个胶囊之间，
            # 看上去就"分类排得太开"了
            kind_row.addStretch(1)
            lay.addLayout(kind_row)

        # 标签 / 收藏筛选行：贴着列表，有标签或收藏时才出现
        self._build_filter_row(lay, etype)

        table = _Table()
        table._owner = self          # 让列表内的 Ctrl+C 能复制完整原文（见 _Table.keyPressEvent）
        # 内容列会按内容撑开，横向滚动条用来左右拖动看完整内容
        table.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        # 逐像素滚动：滚动更顺滑，不再一格一格地跳
        table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        # 点选单元格后不要那圈细焦点框（选中高亮保留）
        table.setItemDelegate(_NoFocusDelegate(table))
        table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        table.customContextMenuRequested.connect(
            lambda pos, t=etype: self._on_right_click(t, pos))
        table.doubleClicked.connect(lambda _: self._open_selected())
        table.itemSelectionChanged.connect(
            lambda t=etype: self._on_selection_changed(t))

        if etype == "all":
            # 全部标签：混合展示四类记录；类型以「[文本] 内容」前缀显示，
            # 这样「内容预览」列和别的分类一样从同一位置开始
            table.setColumnCount(4)
            table.setHorizontalHeaderLabels(
                ["#", tr("col_time"), "", tr("col_preview")])
            table.setColumnWidth(0, 58)
            table.setColumnWidth(1, 186)
            table.setColumnWidth(2, 30)
            table.horizontalHeader().setSectionResizeMode(
                3, QHeaderView.ResizeMode.Interactive)
            self._flex_cols[etype] = 3
        elif etype == "text":
            table.setColumnCount(4)
            table.setHorizontalHeaderLabels(["#", tr("col_time"), "", tr("col_preview")])
            table.setColumnWidth(0, 58)
            table.setColumnWidth(1, 186)
            table.setColumnWidth(2, 30)
            table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
            self._flex_cols[etype] = 3
        elif etype == "url":
            table.setColumnCount(4)
            table.setHorizontalHeaderLabels(["#", tr("col_time"), "", tr("col_url")])
            table.setColumnWidth(0, 58)
            table.setColumnWidth(1, 186)
            table.setColumnWidth(2, 30)
            table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
            self._flex_cols[etype] = 3
        elif etype == "image":
            table.setColumnCount(7)
            table.setHorizontalHeaderLabels(
                ["#", tr("col_time"), "", tr("col_filename"), tr("col_format"),
                 tr("col_dims"), tr("col_size")])
            table.setColumnWidth(0, 58)
            table.setColumnWidth(1, 186)
            table.setColumnWidth(2, 30)
            table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
            self._flex_cols[etype] = 3
            # 格式 / 尺寸 / 大小：按实际最长内容留够宽度（"AI, PSD" / "2560x1440" / "2109.5 MB"）
            table.setColumnWidth(4, 104)
            table.setColumnWidth(5, 132)
            table.setColumnWidth(6, 132)
        else:
            # 文件分类：内容列（文件列表）紧跟时间列，其后才是数量/格式/大小
            table.setColumnCount(7)
            table.setHorizontalHeaderLabels(
                ["#", tr("col_time"), "", tr("col_files"), tr("col_count"),
                 tr("col_format"), tr("col_size")])
            table.setColumnWidth(0, 58)
            table.setColumnWidth(1, 186)
            table.setColumnWidth(2, 30)
            table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
            table.setColumnWidth(4, 46)
            # 格式 / 大小：留够宽度，避免 "AI, PSD" / "2109.5 MB" 被截断成 "…"
            table.setColumnWidth(5, 108)
            table.setColumnWidth(6, 132)
            self._flex_cols[etype] = 3

        # 内容列的标题靠左（该列会被内容撑得很宽，居中会跑到很右边去）
        _flex = self._flex_cols.get(etype)
        if _flex is not None:
            _head = table.horizontalHeaderItem(_flex)
            if _head is not None:
                _head.setTextAlignment(
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        # 时间列标题与时间值都居中，保证标题正对着下面时间的中心
        _time_head = table.horizontalHeaderItem(1)
        if _time_head is not None:
            _time_head.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(table, 1)
        self._tables[etype] = table
        if etype in TAB_TYPES:
            # 内容列统一由该委托绘制：起点严格对齐本列表头文字，
            # 文本预览还会把换行渲染成内联 huiche 图标
            if not hasattr(self, "_text_preview_delegate"):
                self._text_preview_delegate = _InlineImageDelegate(table)
            table.setItemDelegateForColumn(self._flex_cols[etype],
                                           self._text_preview_delegate)

    def _build_preview_panel(self, parent_split):
        pf = QFrame()
        pf.setStyleSheet(
            f"background: {glass_transparent()}; border: none;")
        pf.setFrameShape(QFrame.Shape.NoFrame)
        pl = QVBoxLayout(pf)
        pl.setContentsMargins(8, 6, 8, 6)
        pl.setSpacing(4)
        title = QLabel(tr("panel_preview"))
        title.setStyleSheet(f"color: {C['ACCENT']}; font-weight: bold; font-size: 12px;")
        pl.addWidget(title)

        # 标题留在边框外；只给真正的预览内容区域绘制边界。
        preview_box = QFrame()
        preview_box.setStyleSheet(
            f"background: {surface_bg(glass_key='GLASS_CARD')};"
            f" border: 1px solid {C['BORDER_LT']};"
            " border-radius: 10px;")
        box_lay = QVBoxLayout(preview_box)
        box_lay.setContentsMargins(4, 4, 4, 4)
        box_lay.setSpacing(0)
        # 预览顶部：收藏按钮 + 标签 + 「编辑标签…」（选中记录时才有内容）
        self._preview_meta = QFrame()
        self._preview_meta.setStyleSheet("background: transparent; border: none;")
        meta_lay = QHBoxLayout(self._preview_meta)
        meta_lay.setContentsMargins(6, 2, 6, 4)
        meta_lay.setSpacing(8)
        self._preview_fav_btn = QPushButton(tr("btn_fav"))
        self._preview_fav_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._preview_fav_btn.clicked.connect(self._toggle_fav_preview)
        meta_lay.addWidget(self._preview_fav_btn)
        self._preview_tags_lbl = QLabel(tr("tags_none"))
        self._preview_tags_lbl.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        meta_lay.addWidget(self._preview_tags_lbl, 1)
        self._preview_tags_btn = QPushButton(tr("btn_edit_tags"))
        self._preview_tags_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._preview_tags_btn.clicked.connect(self._edit_tags_selected)
        meta_lay.addWidget(self._preview_tags_btn)
        self._preview_meta.setVisible(False)
        box_lay.addWidget(self._preview_meta)
        self._preview_scroll = QScrollArea()
        self._preview_scroll.setWidgetResizable(True)
        if is_glass_active():
            # 有壁纸时必须显式透明：否则滚动控件会把父面板的半透明底色再画一遍，
            # 叠起来壁纸被压暗（预览区会明显比历史面板黑）
            self._preview_scroll.setStyleSheet(
                f"background: {glass_transparent()}; border: none;")
            self._preview_scroll.viewport().setStyleSheet(
                f"background: {glass_transparent()};")
        self._preview_inner = QWidget()
        self._preview_inner.setStyleSheet(
            f"background: {glass_transparent()};")
        self._preview_layout = QVBoxLayout(self._preview_inner)
        self._preview_layout.setContentsMargins(4, 4, 4, 4)
        self._preview_layout.setSpacing(4)
        self._preview_scroll.setWidget(self._preview_inner)
        box_lay.addWidget(self._preview_scroll, 1)
        pl.addWidget(preview_box, 1)
        self._show_preview_placeholder()
        parent_split.addWidget(pf)

    def _build_history_panel(self, parent_split):
        hf = QFrame()
        hf.setObjectName("histPanel")
        # 选择器必须限定到面板本身：不带选择器的样式表会按类名匹配到子控件，
        # QLabel 是 QFrame 的子类，会被套上一圈多余的方框（“历史快照”那圈就是它）
        hf.setStyleSheet(
            f"QFrame#histPanel {{ background: {surface_bg(glass_key='GLASS_CARD')};"
            f" border: 1px solid {C['BORDER_LT']};"
            f" border-radius: 10px; }}")
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
        # 快照列表自己保留圆角面板（原来那圈背景/描边就是它的）
        self._hist_list.setStyleSheet(
            f"QListWidget {{ background: {surface_bg(glass_key='GLASS_CARD')};"
            f" border: 1px solid {C['BORDER_LT']}; border-radius: 10px; }}")
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
        bar.setStyleSheet(
            f"background: {surface_bg(glass_key='GLASS_HEADER')};"
            f" border: none;")     # 去掉提示条上方那根分隔横杠
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(12, 0, 12, 0)
        self._hint_lbl = QLabel()
        self._hint_lbl.setStyleSheet(f"color: {C['TEXT_MUTED']}; font-size: 11px; background: transparent;")
        bl.addWidget(self._hint_lbl)
        bl.addStretch()
        self._sel_lbl = _AutoHideLabel()
        self._sel_lbl.setStyleSheet(f"color: {C['ACCENT']}; font-size: 12px; font-weight: bold; background: transparent;")
        self._sel_lbl.setVisible(False)
        bl.addWidget(self._sel_lbl)
        self._status_lbl = _AutoHideLabel()
        self._status_lbl.setStyleSheet(f"color: {C['TEXT_SEC']}; font-size: 12px; font-weight: bold; background: transparent;")
        self._status_lbl.setVisible(False)
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
        _add(cfg.get("hk_fav", _ACTION_HOTKEY_DEFAULTS["hk_fav"]),
             self._toggle_fav_selected)
        _add(cfg.get("hk_ai", _ACTION_HOTKEY_DEFAULTS["hk_ai"]),
             lambda: self._run_ai("summarize"))
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
        for etype in TAB_TYPES:
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
        for etype in TAB_TYPES:
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
        if etype == "all":
            # 全部标签：跨分类检索（搜索留空时按时间汇总四类）
            entries = (self.store.search(kw, None) if kw
                       else self.store.get_all())
        else:
            entries = (self.store.search(kw, etype) if kw
                       else self.store.get_by_type(etype))
        entries = self._apply_sort(etype, entries)
        if etype == "file":
            # 文件细分筛选：先统计各细分数量给按钮用，再按当前选中项过滤
            counts = {"all": len(entries)}
            kinds = {}
            for e in entries:
                k = file_kind_of_paths(self.store._norm_paths(e))
                kinds[id(e)] = k
                counts[k] = counts.get(k, 0) + 1
            self._paint_file_kind_chips(counts)
            if self._file_kind != "all":
                entries = [e for e in entries
                           if kinds.get(id(e)) == self._file_kind]
        # 标签 / 收藏筛选（在所有标签页都生效）
        entries = self._refresh_filters(etype, entries)
        total_all = len(entries)
        shown = entries[:DISPLAY_LIMIT]
        table.setRowCount(len(shown))
        iid_map = {}
        pin_color = QColor(C['PIN_BG'])
        for i, entry in enumerate(shown):
            row_type = entry.get("type") or etype
            if row_type not in DATA_TYPES:
                row_type = "text"
            ts = entry.get("timestamp", "")
            try:
                time_str = datetime.fromisoformat(ts).strftime(TIME_FORMAT)
            except ValueError:
                time_str = ts[:19] if len(ts) >= 19 else ts
            is_pin = entry["hash"] in self._pinned_hashes
            is_fav = entry_is_fav(entry)
            missing = (row_type == "file" and
                       self._file_missing.get(entry.get("hash", ""), False))
            status = "\U0001f4cc" if is_pin else ""
            if row_type == "text":
                content = entry.get("content", "")
                preview = content[:120].replace("\n", " ⏎ ").replace("\t", "  ")
                preview_html = _text_preview_html(content, 120)
                if len(content) > 120:
                    preview += "…"
                vals = [str(i + 1), time_str, status, preview]
            elif row_type == "url":
                vals = [str(i + 1), time_str, status, entry.get("content", "")]
            elif row_type == "image":
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
                if missing:
                    fp += f"  {tr('file_missing')}"
                vals = [str(i + 1), time_str, status, fp,
                        str(entry.get("file_count", len(paths))),
                        _extract_extensions(paths),
                        fmt_size(total_sz) if total_sz > 0 else "?"]
            if etype == "all":
                # 混合列表：类型用圆角标签画在内容前面，内容列起绘位置不变。
                # 文件 / 图片行把 格式、大小（图片还有尺寸）一并带上，这样
                # "文件列表里的内容"在「全部」里也看得到。
                badge = self._type_label(row_type)
                if row_type == "text":
                    html_flex = _text_preview_html(
                        entry.get("content", "") or "", 120)
                else:
                    detail = str(vals[3])
                    if row_type == "image":
                        extra = [str(vals[5]), str(vals[4]), str(vals[6])]
                    elif row_type == "file":
                        extra = [str(vals[5]), str(vals[6])]
                        _n = str(vals[4])
                        if _n and _n != "1":
                            extra.append(f"{tr('col_count')} {_n}")
                    else:
                        extra = []
                    detail = "  ·  ".join(
                        [p for p in [detail] + extra if p and p != "?"] or [detail])
                    vals[3] = detail
                    html_flex = _html_escape(detail)
            elif etype == "text":
                html_flex = preview_html
                badge = None
            elif etype == "file":
                # 文件行：在文件名前画一个细分标签（视频 / 压缩包 / 设计源文件 …）
                html_flex = None
                badge = self._file_kind_label(file_kind_of_paths(paths))
            else:
                html_flex = None
                badge = None
            _flex_col = self._flex_cols.get(etype)
            for col, val in enumerate(vals):
                item = QTableWidgetItem(val)
                if (_flex_col is not None and col == _flex_col
                        and html_flex is not None):
                    item.setData(_InlineImageDelegate.HTML_ROLE, html_flex)
                if (_flex_col is not None and col == _flex_col
                        and etype in ("all", "file")):
                    item.setData(_InlineImageDelegate.BADGE_ROLE, badge)
                if (_flex_col is not None and col == _flex_col and is_pin):
                    # 置顶的记录额外画一个主题色小胶囊，任何分类里都能一眼看出
                    item.setData(_InlineImageDelegate.PIN_ROLE, tr("btn_pin"))
                if (_flex_col is not None and col == _flex_col and is_fav):
                    # 已收藏：内容前加一颗主题色星
                    item.setData(_InlineImageDelegate.FAV_ROLE, True)
                # 序号 / 时间 / 状态居中；数量·格式·尺寸·大小 这几个小列也跟着表头居中，
                # 否则表头居中、数值左对齐，看上去就"没对上"
                if col in (0, 1, 2) or (etype in ("image", "file") and col >= 4):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if is_pin:
                    item.setBackground(pin_color)
                if missing:
                    item.setForeground(QColor(C['TEXT_MUTED']))
                    if _flex_col is not None and col == _flex_col:
                        item.setIcon(self._missing_icon())
                table.setItem(i, col, item)
            iid_map[i] = entry["hash"]
        self._iid_to_hash[etype] = iid_map
        # 数量 / 格式 / 尺寸 / 大小 这几个小列按实际内容自适应（带上下限），
        # 避免出现 "2109.5 MB"、"AI, PSD"、"1200x2238" 被截断成 "…"
        for _col in self._auto_width_cols.get(etype, ()):
            if _col >= table.columnCount():
                continue
            try:
                table.resizeColumnToContents(_col)
                _w = max(46, min(table.columnWidth(_col) + 8, 200))
                table.setColumnWidth(_col, _w)
            except Exception:
                pass
        # 内容列按内容撑开（不窄于剩余宽度）：这样横向滚动条才有可拖动的范围，
        # 长内容可以左右拖动看全
        flex_col = getattr(self, "_flex_cols", {}).get(etype)
        if flex_col is not None and flex_col < table.columnCount():
            try:
                hint = table.sizeHintForColumn(flex_col)
                others = sum(table.columnWidth(c)
                             for c in range(table.columnCount())
                             if c != flex_col)
                target = max(hint, table.viewport().width() - others)
                if abs(table.columnWidth(flex_col) - target) > 2:
                    table.setColumnWidth(flex_col, target)
            except Exception:
                pass
        pin_n = (self.store.pinned_count(None) if etype == "all"
                 else self.store.pinned_count(etype))
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
        self._schedule_cache_cleanup()

    def _missing_icon(self):
        if not hasattr(self, "_jinggao_icon"):
            self._jinggao_icon = QIcon(_res_icon("jinggao.ico"))
        return self._jinggao_icon

    def _check_files_changed(self):
        """后台检测文件失效状态，源文件被删除时当场刷新（无需手动 F5）。"""
        if not self.isVisible() or self.isMinimized():
            return
        worker = getattr(self, "_file_status_worker", None)
        if worker is not None and worker.isRunning():
            return
        try:
            entries = self.store.get_by_type("file")
        except Exception:
            return
        worker = _FileStatusWorker(entries, self)
        self._file_status_worker = worker
        worker.done.connect(self._on_file_status_ready)
        worker.finished.connect(
            lambda current=worker: self._on_file_status_worker_finished(current))
        worker.start()

    def _on_file_status_ready(self, missing):
        if missing == getattr(self, "_file_missing", None):
            return
        self._file_missing = missing or {}
        if self.isVisible() and not self.isMinimized():
            if self._active_type == "file":
                self._refresh_tab("file")
            self._update_desk_widget()

    def _on_file_status_worker_finished(self, worker):
        if worker is getattr(self, "_file_status_worker", None):
            self._file_status_worker = None
        worker.deleteLater()

    def _schedule_cache_cleanup(self):
        """在后台回收不再被历史或快照引用的图片与文件缓存。"""
        worker = getattr(self, "_cache_cleanup_worker", None)
        if worker is not None and worker.isRunning():
            self._cache_cleanup_pending = True
            return
        worker = _CacheCleanupWorker(self.store, self)
        self._cache_cleanup_worker = worker
        worker.done.connect(self._on_cache_cleanup_done)
        worker.finished.connect(
            lambda current=worker: self._on_cache_cleanup_finished(current))
        worker.start()

    def _on_cache_cleanup_done(self, removed_entries, removed_files, freed):
        # 保留策略删除历史后静默刷新列表；缓存文件回收不改变界面。
        if removed_entries and self.isVisible() and not self.isMinimized():
            for etype in TAB_TYPES:
                self._refresh_tab(etype)
            self._refresh_history_list()
            self._update_desk_widget()

    def _on_cache_cleanup_finished(self, worker):
        if worker is getattr(self, "_cache_cleanup_worker", None):
            self._cache_cleanup_worker = None
        worker.deleteLater()
        if getattr(self, "_cache_cleanup_pending", False):
            self._cache_cleanup_pending = False
            QTimer.singleShot(300, self._schedule_cache_cleanup)

    def _apply_retention_policy(self):
        """保留策略变化后立即执行一次，并安排固定到期时间。"""
        self._schedule_cache_cleanup()
        self._schedule_retention_deadline()

    def _schedule_retention_deadline(self):
        """为“指定时间清空”安排一次性触发，长周期自动分段续排。"""
        timer = getattr(self, "_retention_deadline_timer", None)
        if timer is None:
            return
        policy = load_config().get("history_retention") or {}
        if policy.get("mode") != "expire":
            timer.stop()
            return
        expire_at = ClipboardStore._parse_time(policy.get("expire_at", ""))
        if expire_at is None:
            timer.stop()
            return
        delay_ms = int(max(0.0, (expire_at - datetime.now()).total_seconds())
                       * 1000)
        timer.start(min(delay_ms, 24 * 60 * 60 * 1000))

    def _on_retention_deadline(self):
        self._schedule_cache_cleanup()
        self._schedule_retention_deadline()

    def _update_tab_badge(self, etype):
        n = self.store.count(None) if etype == "all" else self.store.count(etype)
        idx = TAB_TYPES.index(etype)
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
        if not _confirm_card(
                self, tr("dlg_confirm_restore"),
                tr("msg_restore_confirm", ts=ts, desc=snap.get("desc", "?")),
                ok_text=tr("btn_confirm_restore")):
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
        if not _confirm_card(
                self, tr("dlg_confirm_clear"),
                tr("msg_clear_history", n=len(snaps)),
                ok_text=tr("btn_confirm_clear")):
            return
        self.store.clear_snapshots()
        self._refresh_history_list()
        self._set_status(tr("st_history_cleared"), "ok")
        self._schedule_cache_cleanup()

    # ------------------------------------------------------------------
    # Tab switching / shortcuts / status bar
    # ------------------------------------------------------------------
    def _on_tab_changed(self, idx):
        if 0 <= idx < len(TAB_TYPES):
            self._active_type = TAB_TYPES[idx]
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
        """底部提示按当前快捷键配置生成（改了快捷键后要跟着变）。"""
        try:
            cfg = load_config()
        except Exception:
            cfg = {}
        copy_hk = _hotkey_display(cfg.get(
            "hk_copy", _ACTION_HOTKEY_DEFAULTS["hk_copy"]))
        pin_hk = _hotkey_display(cfg.get(
            "hk_pin", _ACTION_HOTKEY_DEFAULTS["hk_pin"]))
        del_hk = _hotkey_display(cfg.get(
            "hk_delete", _ACTION_HOTKEY_DEFAULTS["hk_delete"]))
        hints = {"text": tr("hint_text"), "image": tr("hint_image"),
                 "file": tr("hint_file"), "url": tr("hint_url"),
                 "all": tr("hint_all")}
        tpl = hints.get(self._active_type, "")
        try:
            self._hint_lbl.setText(
                tpl.format(copy=copy_hk, pin=pin_hk, delete=del_hk))
        except Exception:
            self._hint_lbl.setText(tpl)

    # ------------------------------------------------------------------
    # Preview panel
    # ------------------------------------------------------------------
    def _clear_preview(self):
        self._pending_image = None
        self._cancel_image_loader()

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
        self._refresh_preview_meta(None)
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
        self._refresh_preview_meta(entry)
        if etype == "text":
            self._preview_text(entry)
        elif etype == "image":
            self._preview_image(entry)
        elif etype == "url":
            self._preview_url(entry)
        else:
            self._preview_files(entry)

    def _refresh_preview_meta(self, entry):
        """预览顶部的收藏 / 标签条：没选中记录时整条隐藏。"""
        bar = getattr(self, "_preview_meta", None)
        if bar is None:
            return
        self._preview_hash = entry.get("hash") if entry else None
        if not entry:
            bar.setVisible(False)
            return
        bar.setVisible(True)
        fav = entry_is_fav(entry)
        self._preview_fav_btn.setText(tr("btn_faved") if fav else tr("btn_fav"))
        # 实心星 = 已收藏，空心星 = 未收藏（矢量画，不依赖字体字形）
        self._preview_fav_btn.setIcon(
            _star_icon(14, C['ACCENT'] if fav else C['TEXT_SEC'], fav))
        self._preview_fav_btn.setIconSize(QSize(13, 13))
        if fav:
            self._preview_fav_btn.setStyleSheet(
                f"QPushButton {{ background: {C['ACCENT_DIM']};"
                f" color: {C['ACCENT']}; border: 1px solid {C['ACCENT']};"
                f" border-radius: 11px; padding: 3px 10px; font-size: 11px;"
                f" font-weight: 700; }}")
        else:
            self._preview_fav_btn.setStyleSheet(
                f"QPushButton {{ background: {C['SURFACE2']};"
                f" color: {C['TEXT_SEC']}; border: 1px solid {C['BORDER']};"
                f" border-radius: 11px; padding: 3px 10px; font-size: 11px; }}"
                f"QPushButton:hover {{ background: {C['SURFACE3']};"
                f" color: {C['TEXT']}; }}")
        tags = entry_tags(entry)
        text = "   ".join("#" + t for t in tags) if tags else tr("tags_none")
        if len(text) > 90:
            text = text[:90] + "…"
        self._preview_tags_lbl.setText(text)
        self._preview_tags_lbl.setStyleSheet(
            f"color: {C['ACCENT'] if tags else C['TEXT_MUTED']};"
            f" font-size: 12px; background: transparent;")

    def _preview_text(self, entry):
        self._preview_gen += 1
        self._cur_image_path = None
        self._cur_text_entry = entry
        self._clear_preview()
        content = entry.get("content", "")
        # 大内容外置：正文存在 content/ 里，预览只读头部，避免把整份大文本读进界面
        full_len = entry.get("content_size") or len(content)
        is_external = bool(entry.get("content_ref"))
        if is_external:
            head = read_external_head(entry["content_ref"], PREVIEW_TEXT_LIMIT)
            if len(head) > len(content):
                content = head
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
            shown = content[:PREVIEW_TEXT_LIMIT]
            txt.setPlainText(shown + (tr("preview_truncated")
                                     if full_len > len(shown) else ""))
            self._preview_layout.addWidget(txt, 1)
        info_row = QHBoxLayout()
        n_lines = content.count("\n") + 1
        chip1 = QLabel(tr("chip_chars", n=f"{full_len:,}"))
        chip1.setStyleSheet(f"background: {C['ACCENT_DIM']}; color: {C['ACCENT']}; border-radius: 4px; padding: 2px 8px; font-size: 11px;")
        info_row.addWidget(chip1)
        chip2 = QLabel(tr("chip_lines", n=f"{'≈' if is_external else ''}{n_lines:,}"))
        chip2.setStyleSheet(f"background: {C['SURFACE3']}; color: {C['TEXT_SEC']}; border-radius: 4px; padding: 2px 8px; font-size: 11px;")
        info_row.addWidget(chip2)
        if is_external:
            chip3 = QLabel(tr("chip_external"))
            chip3.setStyleSheet(f"background: {C['SURFACE3']}; color: {C['TEXT_MUTED']}; border-radius: 4px; padding: 2px 8px; font-size: 11px;")
            info_row.addWidget(chip3)
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
        # 切换记录时先丢弃上一张已解码的图：
        # 否则当前记录图片文件缺失（或还在后台解码）时，
        # 标签重排触发的重绘会把上一张图当成这一条记录显示。
        self._cached_qpixmap = None
        self._cached_qpixmap_path = None
        self._last_render_key = None
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
                self._queue_image_load(img_path, gen)
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

    def _cancel_image_loader(self):
        """取消仍在解码的旧图片，避免快速切换多图时线程堆积。"""
        loader = getattr(self, "_image_loader", None)
        if loader is not None and loader.isRunning():
            loader.cancel()

    def _queue_image_load(self, path, gen):
        """最多保留一个图片解码任务，始终优先最新选择。"""
        self._pending_image = (path, gen)
        loader = getattr(self, "_image_loader", None)
        if loader is not None and loader.isRunning():
            loader.cancel()
            return
        self._start_pending_image_load()

    def _start_pending_image_load(self):
        pending = getattr(self, "_pending_image", None)
        self._pending_image = None
        if not pending:
            return
        path, gen = pending
        if gen != self._preview_gen:
            return
        loader = ImageLoader(path, gen)
        self._image_loader = loader
        loader.loaded.connect(self._on_image_loaded)
        loader.finished.connect(
            lambda current=loader: self._on_image_loader_finished(current))
        loader.start()

    def _on_image_loader_finished(self, loader):
        if loader is getattr(self, "_image_loader", None):
            self._image_loader = None
        loader.deleteLater()
        self._start_pending_image_load()

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
            self._cached_qpixmap_path = path
        except Exception:
            self._cached_qpixmap = None
            self._cached_qpixmap_path = None
        self._render_preview_image()
        # Delayed re-render in case label size wasn't final yet
        QTimer.singleShot(100, self._render_preview_image)

    def _on_preview_resize_settled(self):
        """缩放结束（短暂停顿）后再重绘预览图，避免拖动时每帧做平滑缩放。"""
        try:
            self._last_render_key = None
            self._render_preview_image()
        except Exception:
            pass

    def _render_preview_image(self):
        if not hasattr(self, '_preview_img_lbl'):
            return
        pm = getattr(self, '_cached_qpixmap', None)
        if pm is None or pm.isNull():
            return
        # 只画“当前这条记录”的图；文件缺失或尚未解码完成时保持占位提示，
        # 绝不能把上一张已缓存的图重新画到这个标签上。
        if (not self._cur_image_path
                or getattr(self, '_cached_qpixmap_path', None) != self._cur_image_path):
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
            _info_card(self, tr("dlg_info"),
                       tr("msg_file_not_found", path=path), kind="warning")

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
            # 大内容外置：复制时读回完整正文
            set_clipboard_text(entry_full_text(entry))
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
                body = entry_full_text(entry)
                set_clipboard_text(body)
                self._set_status(tr("st_copied_chars", n=f"{len(body):,}"), "ok")
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
            self._play_sound("copy")
        except Exception as ex:
            _info_card(self, tr("dlg_error"),
                       tr("msg_copy_failed", err=ex), kind="warning")

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
            _info_card(self, tr("dlg_error"),
                       tr("msg_open_failed", err=ex), kind="warning")

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

    def _all_selected_fav(self):
        """选中的记录是不是都已经收藏了（决定菜单里显示"收藏"还是"取消收藏"）。"""
        hashes = self._get_selected_hashes()
        return bool(hashes) and all(self.store.is_fav(h) for h in hashes)

    def _toggle_fav_selected(self):
        """收藏 / 取消收藏（多选整批处理，与「置顶」的多选行为一致）。"""
        hashes = self._get_selected_hashes()
        if not hashes:
            return
        all_fav = all(self.store.is_fav(h) for h in hashes)
        n = self.store.set_fav_many(hashes, not all_fav)
        self._after_mutate(tr("st_fav_unset", n=n) if all_fav
                           else tr("st_fav_set", n=n))
        self.lightbar.surge(52.0, 0.9)

    def _toggle_fav_preview(self):
        """预览面板上的收藏按钮：只作用于当前预览的那一条。"""
        h = self._preview_hash
        if not h:
            return
        flag = self.store.toggle_fav(h)
        if flag is None:
            return
        self._after_mutate(tr("st_fav_set", n=1) if flag
                           else tr("st_fav_unset", n=1))

    def _edit_tags_selected(self):
        """编辑标签：选一条是完整编辑，选多条是"批量添加"。"""
        hashes = self._get_selected_hashes()
        if not hashes:
            self._set_status(tr("st_tags_none"), "warn")
            return
        first = self._entry_index.get(hashes[0])
        if first is None:
            return
        known = self.store.all_tags()
        if len(hashes) == 1:
            dlg = _TagsDialog(self, self, entry_tags(first), known,
                              mode="edit", count=1)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            if self.store.set_tags(hashes[0], dlg.selected_tags()):
                self._after_mutate(tr("st_tags_saved", n=1))
            return
        # 多选：把选中记录已有的标签并起来当"已有标签"（批量添加不会重复加）
        merged = []
        for h in hashes:
            merged.extend(entry_tags(self._entry_index.get(h) or {}))
        dlg = _TagsDialog(self, self, merged, known,
                          mode="add", count=len(hashes))
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        picked = dlg.selected_tags()
        if not picked:
            return
        n = self.store.add_tags(hashes, picked)
        self._after_mutate(tr("st_tags_saved", n=n))

    def _add_selected_to_vault(self):
        """把选中的记录（文本 / 图片 / 文件 / 网址）**移进**密库。

        入口：列表右键 →「加入密库…」。名称是可选的：默认取这条记录的标签，
        用户清空后就按内容显示。图片会把文件复制进密库目录，文件记路径；
        存进密库成功后，**把这条记录从剪贴板历史里删掉**——否则加进密库的
        密钥在历史里还留着一份（会被手机传输/云同步带出去），等于白加。
        """
        entry = self._get_selected_entry()
        if not entry:
            self._set_status(tr("st_nothing_to_copy"), "warn")
            return
        # 密库设了主密码又没解锁时，先就地解锁；取消就别往下走（绝不能写进锁着的密库）
        if self.vault.is_locked():
            unlock_dlg = _VaultUnlockDialog(self, self, self.vault)
            if unlock_dlg.exec() != QDialog.DialogCode.Accepted:
                return
        etype = entry.get("type", "text")
        tags = entry_tags(entry)
        # 记住这条记录原来的时间 / 标签 / 收藏 / 置顶：移出密库时按原样归位
        prefill = {"name": tags[0] if tags else "",
                   "ts": str(entry.get("timestamp") or ""),
                   "tags": list(tags),
                   "fav": bool(entry.get("fav")),
                   "pinned": bool(entry.get("hash") in self._pinned_hashes)}
        if etype == "image":
            src = self._image_full_path(entry)
            if not (src and os.path.exists(src)):
                self._set_status(tr("vault_no_file"), "warn")
                return
            prefill.update({"type": "image", "image_src": src})
        elif etype == "file":
            paths = [p for p in entry.get("file_paths", []) if p]
            if not paths:
                self._set_status(tr("vault_no_file"), "warn")
                return
            prefill.update({"type": "file", "paths": paths})
        else:
            content = (entry_full_text(entry) if etype == "text"
                       else str(entry.get("content", "") or ""))
            if not content.strip():
                return
            prefill.update({"type": "url" if etype == "url" else "text",
                            "content": content})
        dlg = _VaultEntryDialog(self, self, prefill)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        if _vault_add_from_values(self.vault, dlg.values()) is None:
            return
        # 移进密库 = 从剪贴板历史里挪走这条（不是删掉：记录已带着原时间存进密库，
        # 之后在密库里右键「移出密库」就能按当初的时间原样放回历史）
        moved = False
        try:
            h = str(entry.get("hash") or "")
            if h:
                self.store.delete_many([h])
                moved = True
        except Exception:
            moved = False
        if moved:
            self._after_mutate(tr("st_vault_moved"))
        else:
            self._set_status(tr("vault_saved"), "ok")

    def _delete_selected(self):
        hashes = self._get_selected_hashes()
        if not hashes:
            return
        if not _confirm_card(
                self, tr("dlg_confirm_delete"),
                tr("msg_delete_confirm", n=len(hashes)),
                ok_text=tr("btn_confirm_delete")):
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
        self._schedule_cache_cleanup()

    # ---- Clear operations ----
    def _clear_type(self):
        etype, label = self._active_type, self._type_label(self._active_type)
        count = self.store.count(etype)
        if count == 0:
            self._set_status(tr("st_no_type_records", t=label))
            return
        if not _confirm_card(
                self, tr("dlg_confirm_clear"),
                tr("msg_clear_type", t=label, n=count),
                ok_text=tr("btn_confirm_clear")):
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
        if not _confirm_card(
                self, tr("dlg_confirm_remove"),
                tr("msg_clear_type_unpinned", t=label, n=unpinned),
                ok_text=tr("btn_confirm_clear")):
            return
        self.store.save_snapshot(tr("snap_clear_type_unpinned", t=label, n=unpinned))
        self.store.clear_type_unpinned(etype)
        self._after_mutate(tr("st_cleared_unpinned_type", t=label))

    def _clear_unpinned(self):
        unpinned = self.store.count() - self.store.pinned_count()
        if unpinned == 0:
            self._set_status(tr("st_no_unpinned"))
            return
        if not _confirm_card(
                self, tr("dlg_confirm_remove"),
                tr("msg_clear_unpinned", n=unpinned),
                ok_text=tr("btn_confirm_clear")):
            return
        self.store.save_snapshot(tr("snap_clear_unpinned", n=unpinned))
        self.store.clear_unpinned()
        self._refresh_all()
        self._set_status(tr("st_cleared_unpinned"), "ok")
        self._schedule_cache_cleanup()

    def _clear_all(self):
        total = self.store.count()
        if total == 0:
            self._set_status(tr("st_nothing_to_clear"))
            return
        if not _confirm_card(
                self, tr("dlg_confirm_clear"),
                tr("msg_clear_all", n=total),
                ok_text=tr("btn_confirm_clear")):
            return
        self.store.save_snapshot(tr("snap_clear_all", n=total))
        self.store.clear()
        self._refresh_all()
        self._set_status(tr("st_cleared_all"), "ok")
        self._schedule_cache_cleanup()

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
                    f.write(entry_full_text(entry))
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
    # ---- AI 就地处理（只对文本 / 网址记录） ----
    @staticmethod
    def _ai_actions_for(etype):
        """不同分类下能用的 AI 动作。"""
        if etype == "image":
            return AI_IMAGE_ACTIONS
        if etype == "file":
            return AI_FILE_ACTIONS
        return ("summarize", "translate", "rewrite", "extract", "custom")

    def _ai_menu_for(self, menu, entry):
        """把「AI 处理 ▸」子菜单加进右键菜单；不支持的类型返回 None。"""
        etype = (entry or {}).get("type")
        if etype not in ("text", "url", "image", "file"):
            return None
        sub = _RoundMenu(menu)
        sub.setTitle(tr("ai_menu"))
        for act in self._ai_actions_for(etype):
            if act == "custom":
                continue
            sub.addAction(tr("ai_act_" + act),
                          lambda a=act: self._run_ai(a))
        sub.addSeparator()
        sub.addAction(tr("ai_act_custom"), self._run_ai_custom)
        sub.addAction(tr("ai_menu_chat"), self._run_ai_chat)
        menu.addMenu(sub)
        return sub

    def _ai_target_entry(self):
        """当前选中的记录；四类记录都支持（图片走视觉模型、文件走清单）。"""
        entry = self._get_selected_entry()
        if not entry:
            return None
        if entry.get("type") not in ("text", "url", "image", "file"):
            self._set_status(tr("ai_only_supported"), "warn")
            return None
        return entry

    def _run_ai(self, action, custom_prompt=""):
        entry = self._ai_target_entry()
        if entry is None:
            return
        dlg = _AIDialog(self, self, entry, action,
                        custom_prompt=custom_prompt)
        dlg.exec()

    def _run_ai_custom(self):
        entry = self._ai_target_entry()
        if entry is None:
            return
        ask = _AIPromptDialog(self, self, entry)
        if ask.exec() != QDialog.DialogCode.Accepted:
            return
        prompt = ask.prompt_text()
        if prompt:
            self._run_ai("custom", prompt)

    def _run_ai_chat(self):
        """和 AI 自由对话：把选中的这条记录作为上下文，之后问什么都行。"""
        entry = self._ai_target_entry()
        if entry is None:
            return
        _AIDialog(self, self, entry, "summarize", chat=True).exec()

    def _ai_result_copy(self, text):
        """把 AI 结果复制到剪贴板（必须标记 self-copy，否则会被再收录一条）。"""
        text = text or ""
        if not text.strip():
            return
        self._last_self_copy = time.time()
        self.store.mark_self_copy()
        try:
            set_clipboard_text(text)
        except Exception as ex:
            _info_card(self, tr("dlg_error"),
                       tr("msg_copy_failed", err=ex), kind="warning")
            return
        self._set_status(tr("ai_copied", n=f"{len(text):,}"), "ok")
        self._play_sound("copy")

    def _ai_result_save(self, text):
        text = text or ""
        if not text.strip():
            return
        self.store.add_text(text)
        self._refresh_all()
        self._update_desk_widget()
        self._set_status(tr("ai_saved_new", n=f"{len(text):,}"), "ok")

    def _ai_result_replace(self, entry_hash, text):
        text = text or ""
        if not text.strip() or not entry_hash:
            return
        if not self.store.replace_text(entry_hash, text):
            return
        self._refresh_all()
        self._update_desk_widget()
        self._set_status(tr("ai_replaced"), "ok")

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
        if etype == "all":
            # 全部标签：右键菜单按记录自身的类型来给
            etype = entry.get("type") or "text"
        n = len(table.selectionModel().selectedRows())
        menu = _RoundMenu(self)
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
        # 标签 / 收藏：同样放在分隔线下方，和置顶 / 删除归为一组操作
        menu.addAction(tr("m_fav_off") if self._all_selected_fav()
                       else tr("m_fav_on"), self._toggle_fav_selected)
        menu.addAction(tr("m_edit_tags"), self._edit_tags_selected)
        # 密库：四类记录都能手动存进去（图片复制文件、文件记路径）
        menu.addAction(tr("m_vault_add"), self._add_selected_to_vault)
        # AI 处理子菜单（只对文本 / 网址记录出现）
        self._ai_menu_for(menu, entry)
        menu.addAction(tr("m_delete_n", n=n) if n > 1 else tr("m_delete"), self._delete_selected)
        menu.exec(table.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------------
    # Manage menu (header)
    # ------------------------------------------------------------------
    def _show_manage_menu(self):
        menu = _RoundMenu(self)
        menu.addAction(tr("m_refresh"), self._refresh_all)
        menu.addSeparator()
        if self._active_type != "all":
            menu.addAction(tr("m_clear_type", t=self._type_label(self._active_type)),
                           self._clear_type)
            menu.addAction(tr("m_clear_type_unpinned",
                              t=self._type_label(self._active_type)),
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
        for etype in TAB_TYPES:
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
        # 静默挂后台时，在别的程序里 Ctrl+C 也能听到复制提示音。
        # 只判断"是不是本程序自己写进剪贴板的"，外部复制每次都出声。
        try:
            own_copy = self.store.is_self_copy()
        except Exception:
            own_copy = False
        if not own_copy:
            self._play_sound("copy")

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
        # 关掉设置后立刻按当前位置重算一次光标：模态窗口期间本窗口收不到鼠标事件，
        # 之前贴边留下的缩放箭头会一直粘着（用户描述为"光标被锁定"）。
        try:
            self._apply_edge_cursor(self.mapFromGlobal(QCursor.pos()))
        except Exception:
            pass
        # 设置窗口关闭后静默刷新：保证期间复制的新内容立即显示（不弹状态提示）
        try:
            for etype in TAB_TYPES:
                self._refresh_tab(etype)
            self._refresh_history_list()
            self._update_desk_widget()
        except Exception:
            pass

    def _open_phone_transfer(self):
        dlg = PhoneTransferDialog(self)
        dlg.exec()

    def _open_vault(self):
        """密库窗口：用户主动存放的私密数据（独立加密文件，不进历史）。"""
        dlg = VaultDialog(self)
        dlg.exec()

    def _check_update_leftover(self):
        """更新后的收尾：能正常启动说明新版没问题 → 删掉 .bak；
        上次替换校验失败自动回滚过 → 如实告诉用户一次。"""
        try:
            exe = (os.path.abspath(sys.executable)
                   if getattr(sys, "frozen", False)
                   else os.path.abspath(sys.argv[0]))
            exe_dir = os.path.dirname(exe)
            flag = exe + ".update_failed"
            bak = exe + ".bak"
            ok_mark = exe + ".update_ok"
            # 换包脚本还在跑（它成功或回滚后都会自删）＝这次更新的结果还没定下来：
            # 这时候绝不能删备份，否则脚本回滚时没有旧版本可用，用户的主程序会消失。
            # 脚本卡死超过 10 分钟也按结束处理，免得备份永远清不掉。
            bat_path = os.path.join(exe_dir, "_update.bat")
            busy = False
            try:
                busy = (time.time() - os.path.getmtime(bat_path)) < 600
            except OSError:
                busy = False
            if os.path.exists(flag):
                reason = ""
                try:
                    with open(flag, "r", encoding="utf-8",
                              errors="replace") as f:
                        reason = f.read().strip()
                except OSError:
                    pass
                try:
                    os.remove(flag)
                except OSError:
                    pass
                _info_card(self, tr("upd_rollback_title"),
                           tr("upd_rollback_detail", err=reason or "—"),
                           kind="warning")
                return
            if busy and os.path.exists(bat_path):
                # 给脚本一个"新版确实起来了"的凭据：它看到标记才算更新成功，
                # 才敢删掉备份（标记写不出来时脚本会退回用进程名判断）
                try:
                    with open(ok_mark, "w", encoding="utf-8") as f:
                        f.write(str(APP_VERSION))
                except OSError:
                    pass
                return
            for junk in (bak, ok_mark, exe + ".failed"):
                try:
                    if os.path.exists(junk):
                        os.remove(junk)
                except OSError:
                    pass
        except Exception:
            pass

    def apply_settings(self, lang, autostart, theme="dark", bg_changed=False,
                       force_restart=False):
        if autostart != get_autostart():
            if set_autostart(autostart):
                self._set_status(tr("st_autostart_on") if autostart else tr("st_autostart_off"), "ok")
            else:
                self._set_status(tr("st_autostart_failed"), "err")
        cfg = load_config()
        need_restart = bool(bg_changed or force_restart)
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
        # 底部提示条里的快捷键说明也要跟着变
        self._update_hint()
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

    # ------------------------------------------------------------------
    # 浏览器扩展桥（只监听 127.0.0.1）
    # ------------------------------------------------------------------
    def _apply_bridge(self):
        """按配置启动 / 停止本机桥；端口被占会自动换一个空闲端口。"""
        cfg = load_config()
        enabled, port, token = ensure_bridge_config(cfg)
        if token != str(cfg.get("bridge_token") or ""):
            cfg["bridge_token"] = token
            save_config(cfg)
        if not enabled:
            if self._bridge is not None:
                self._bridge.stop()
                self._bridge = None
            self._bridge_error = ""
            return
        if self._bridge is not None and self._bridge.running:
            self._bridge_error = ""
            return
        try:
            bridge = BridgeServer(self.store, port=port, token=token,
                                  on_copy=self._on_bridge_copy)
            actual = bridge.start()
        except Exception as ex:
            self._bridge = None
            # 以前这里是静默失败：设置里只显示"未运行"，用户不知道哪出了问题
            self._bridge_error = str(getattr(ex, "last_error", "") or ex)
            return
        self._bridge = bridge
        self._bridge_error = ""
        if actual and actual != int(cfg.get("bridge_port") or 0):
            # 端口被占用时把真实端口写回配置，设置里显示的就是能用的那个
            cfg["bridge_port"] = int(actual)
            save_config(cfg)

    def _stop_bridge(self):
        if self._bridge is not None:
            try:
                self._bridge.stop()
            except Exception:
                pass
            self._bridge = None

    def _on_bridge_copy(self, entry):
        """扩展让桌面端复制某条记录：刷新小组件与状态栏。"""
        try:
            self._update_desk_widget(entry)
            self._set_status(tr("bridge_copied"), "ok")
        except Exception:
            pass

    def bridge_status(self):
        if self._bridge is None or not self._bridge.running:
            return {"running": False, "port": 0, "last_seen_text": "",
                    "error": getattr(self, "_bridge_error", "")}
        st = self._bridge.status()
        st["error"] = ""
        return st

    def set_bridge_enabled(self, enabled):
        """开关本机桥：立刻写配置并启动 / 停止，返回最新状态（供设置弹层用）。"""
        try:
            cfg = load_config()
            _e, port, token = ensure_bridge_config(cfg)
            cfg["bridge_enabled"] = bool(enabled)
            cfg["bridge_port"] = port
            cfg["bridge_token"] = token
            save_config(cfg)
        except Exception as ex:
            self._bridge_error = str(ex)
            return self.bridge_status()
        if enabled:
            self._apply_bridge()
        else:
            self._stop_bridge()
            self._bridge_error = ""
        return self.bridge_status()

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
        maxed = self._is_window_maximized()
        if getattr(self, "_flush", None) == maxed:
            return
        self._flush = maxed
        # 1px 外框只在普通窗口下保留（最大化时贴着屏幕边，留着反而多余）
        if maxed:
            self.setContentsMargins(0, 0, 0, 0)
        else:
            self.setContentsMargins(1, 1, 1, 1)
        for lay in getattr(self, "_tab_layouts", []):
            # 最大化时也留一点左边距：内容贴着屏幕边缘看着太挤
            lay.setContentsMargins(8, 8, 8, 8)
        try:
            self.setStyleSheet(build_qss(load_config().get("theme", "dark"), flush=maxed))
        except Exception:
            pass

    @staticmethod
    def _close_to_tray_enabled():
        """设置里是否开了「点 ✕ 收进托盘」。"""
        try:
            return bool(load_config().get("close_to_tray", False))
        except Exception:
            return False

    def _trim_memory(self):
        """把可回收的页还给系统（任务管理器里的"内存"就是这个数）。

        页面只是移到系统的 standby 列表，需要时会立刻映射回来——所以后台静默 /
        长时间空闲时回收，既不卡操作，又能把常驻占用压到几 MB。
        """
        try:
            gc.collect()
        except Exception:
            pass
        if not IS_WIN:
            return
        try:
            # 必须声明原型：不声明时 ctypes 会把句柄当 32 位 int（伪句柄 -1 被截断），
            # 参数也会按 int 转换从而抛 OverflowError——之前就是这样被静默吞掉的。
            k32 = ctypes.windll.kernel32
            k32.GetCurrentProcess.restype = ctypes.c_void_p
            k32.SetProcessWorkingSetSize.argtypes = [ctypes.c_void_p,
                                                     ctypes.c_size_t,
                                                     ctypes.c_size_t]
            k32.SetProcessWorkingSetSize.restype = ctypes.c_int
            handle = k32.GetCurrentProcess()
            # (-1, -1) = 让系统按需把工作集尽量收回去
            k32.SetProcessWorkingSetSize(handle, ctypes.c_size_t(-1),
                                         ctypes.c_size_t(-1))
            try:
                psapi = ctypes.windll.psapi
                psapi.EmptyWorkingSet.argtypes = [ctypes.c_void_p]
                psapi.EmptyWorkingSet.restype = ctypes.c_int
                psapi.EmptyWorkingSet(handle)
            except Exception:
                pass
        except Exception:
            pass

    def _memory_tick(self):
        """定期回收工作集（30 秒一次，运行中也回收）。

        实测：启动后专用工作集约 130MB，回收一次就掉到 6~8MB，而且不会很快涨回去。
        回收只是把页移到系统 standby 列表，用到时软缺页回来，代价极小。
        """
        try:
            self._trim_memory()
        except Exception:
            pass

    def _hide_to_tray(self):
        """收进托盘：保存几何 / 落盘，隐藏窗口；桌面小组件照常留着。"""
        self._save_window_geometry()
        try:
            self.store.flush()
        except Exception:
            pass
        self.hide()
        # 收进托盘后（后台静默状态）把工作集还给系统：任务管理器里的占用能降到几 MB，
        # 页面只是移到 standby 列表，重新打开窗口时会立即映射回来，不会变慢。
        QTimer.singleShot(2500, self._trim_memory)
        tray = getattr(self, "_tray", None)
        if tray is not None:
            try:
                if not getattr(self, "_tray_hint_shown", False):
                    self._tray_hint_shown = True
                    tray.showMessage(APP_NAME, tr("tray_still_running"),
                                     QSystemTrayIcon.MessageIcon.Information,
                                     3000)
            except Exception:
                pass
        self._set_status(tr("win_hidden_tray"), "ok")

    def closeEvent(self, event):
        """X 按钮：默认直接退出；开启「点 ✕ 收进托盘」后只隐藏窗口。"""
        if (not getattr(self, "_quitting", False)
                and not getattr(self, "_restarting", False)
                and self._close_to_tray_enabled()):
            self._hide_to_tray()
            event.ignore()
            return
        self._pending_image = None
        self._cancel_image_loader()
        self._wait_aux_workers()
        self._save_window_geometry()
        self._end_session()  # 临时会话：退出即清空本次记录
        self._unregister_hotkey()
        try:
            self._stop_winv_takeover()
            self._remove_paste_sound_hook()
        except Exception:
            pass
        self._stop_sounds()          # 停掉异步提示音，别让它占住打包临时目录
        self._stop_bridge()          # 关掉本机桥（浏览器扩展那条通道）
        try:
            self.store.flush()      # 防抖写盘：退出前把未落盘的历史写完
        except Exception:
            pass
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
        # 关掉主窗口就是要结束事件循环（上面关掉了"最后一个窗口自动退出"，
        # 这里显式退；重启时 main() 的循环会据 restart_flag 再开一个新窗口）
        if not getattr(self, "_restarting", False):
            self._quitting = True
        QApplication.quit()

    def _real_quit(self):
        self._quitting = True          # 真退出：别再被"收进托盘"拦下来
        self._stop_bridge()
        self._pending_image = None
        self._cancel_image_loader()
        self._wait_aux_workers()
        self._save_window_geometry()
        self._end_session()  # 临时会话：退出即清空本次记录
        if hasattr(self, '_unregister_hotkey'):
            self._unregister_hotkey()
        try:
            self._stop_winv_takeover()
            self._remove_paste_sound_hook()
        except Exception:
            pass
        self._stop_sounds()          # 停掉异步提示音，别让它占住打包临时目录
        try:
            self.store.flush()
        except Exception:
            pass
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

    def _stop_sounds(self):
        """退出前停掉还在异步播放的提示音。

        winsound 的 SND_ASYNC 播放期间会一直持有 wav 文件句柄；onefile 打包时
        那个 wav 位于 PyInstaller 的临时目录里，句柄不释放就会导致退出时
        "Failed to remove temporary directory: ...\\_MEIxxxx" 警告。
        """
        try:
            if IS_WIN:
                import winsound
                winsound.PlaySound(None, winsound.SND_PURGE)
        except Exception:
            pass

    def _wait_aux_workers(self):
        """等待短时后台任务收尾，避免退出时销毁运行中的 QThread。"""
        for attr in ("_file_status_worker", "_cache_cleanup_worker"):
            worker = getattr(self, attr, None)
            if worker is not None and worker.isRunning():
                worker.wait(1200)

    def _save_window_geometry(self):
        """退出/重启前保存主窗口位置、大小与最大化状态，保证下次启动原样恢复。"""
        try:
            self._record_window_state()
            cfg = load_config()
            maxed = bool(getattr(self, "_last_max", self._is_window_maximized()))
            geo = getattr(self, "_last_geo", None)
            if geo is not None and self._rect_is_maximized(geo):
                # 整屏大小的矩形只可能来自最大化（或状态切换那一瞬间）。
                # 若把它当作“普通大小”写进配置，下次启动会被判成脏数据并退回默认
                # 大小，用户刚调好的尺寸就白调了——所以这里不覆盖已有的值。
                geo = None
                maxed = True
            if geo is None:
                if not cfg.get("win_geometry"):
                    base = (self.normalGeometry() if self._is_window_maximized()
                            else self.geometry())
                    cfg["win_geometry"] = [base.x(), base.y(),
                                           base.width(), base.height()]
            else:
                cfg["win_geometry"] = [geo.x(), geo.y(), geo.width(), geo.height()]
            cfg["win_maximized"] = maxed
            save_config(cfg)
        except Exception:
            pass

    @staticmethod
    def _rect_is_maximized(geo):
        """几何是否等于（或几乎等于）所在屏幕的可用区。

        最大化、以及最大化/还原切换的瞬间都会返回整屏矩形。
        """
        try:
            screen = QApplication.screenAt(geo.center())
            if screen is None:
                screen = QApplication.primaryScreen()
            avail = screen.availableGeometry()
            return (abs(geo.width() - avail.width()) <= 2
                    and abs(geo.height() - avail.height()) <= 2)
        except Exception:
            return False

    def _is_window_maximized(self):
        """窗口是否处于最大化。

        无边框窗口（自绘标题栏 + 手动补的 WS_THICKFRAME）有时会被系统直接最大化，
        Qt 的状态位没跟上：窗口看着已经铺满屏幕，isMaximized() 却是 False，
        于是标题栏图标不切换、退出时也记不住最大化。这里补上几何判断兜底。
        """
        try:
            return bool(self.isMaximized()
                        or self._rect_is_maximized(self.geometry()))
        except Exception:
            return False

    def _restore_normal_size(self):
        """还原成普通窗口：优先用记住的普通大小，保证一定缩得回去。"""
        try:
            if self.isMaximized():
                self.showNormal()
        except Exception:
            pass
        geo = getattr(self, "_last_geo", None)
        try:
            if geo is not None and not self._rect_is_maximized(geo):
                if self._rect_is_maximized(self.geometry()):
                    self.setGeometry(geo)
                return
        except Exception:
            pass
        try:
            self.showNormal()
        except Exception:
            pass

    def _record_window_state(self):
        """记录当前窗口状态。

        窗口隐藏后代 isMaximized()/normalGeometry() 会失真（会被记成普通窗口和默认
        大小），所以隐藏时先把真实状态记下来，退出时直接用记住的值。
        另外：无边框窗口在“最大化/还原”切换的一瞬间，状态位已经变了而几何还是
        整屏矩形；这种值一旦被当成“普通大小”记下来，用户上次调好的大小就丢了。
        """
        try:
            if self.isVisible() and not self.isMinimized():
                if self._is_window_maximized():
                    self._last_max = True
                    # 最大化时不要用 normalGeometry() 覆盖已记录的普通大小
                    if self._last_geo is None:
                        self._last_geo = self.normalGeometry()
                else:
                    self._last_geo = self.geometry()
                    self._last_max = False
        except Exception:
            pass

    def _schedule_state_save(self):
        """窗口大小/位置变化后延迟保存一次，避免异常退出（断电、结束进程）丢状态。"""
        if not getattr(self, "_state_ready", False):
            return
        timer = getattr(self, "_state_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self._save_window_geometry)
            self._state_timer = timer
        timer.start(900)

    def _init_tray(self):
        """创建并显示系统托盘图标（幂等）。启动早期调用，保证图标及时出现。"""
        if getattr(self, "_tray", None) is not None:
            return
        self._tray = QSystemTrayIcon(_build_tray_icon(), self)
        tray_menu = _RoundMenu()
        show_act = QAction(tr("tray_show"), self)
        show_act.triggered.connect(self._tray_show)
        import_act = QAction(tr("tray_import"), self)
        import_act.triggered.connect(self._open_import_dialog)
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
        tray_menu.addAction(import_act)
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
        # 3.1.0：Win+V 接管（按配置启用，直接开关主窗口）
        self._apply_winv_takeover()
        self._apply_paste_sound_hook()
        # Fade-in animation
        self._fade_in()
        if getattr(self, "_restore_maximized", False):
            self.showMaximized()
        else:
            self.show()
        # 开机自启动时任务栏偶发显示默认占位图标：onefile 程序启动阶段
        # 解压/初始化较慢，任务栏可能在图标就绪前就抓取了占位图标；
        # 这里在窗口显示后（立即 + 延迟）重新注册 AppUserModelID 并重设图标，
        # 让 shell 有机会重新解析为正确的 YouBoard 图标。
        QTimer.singleShot(0, self._refresh_taskbar_icon)
        QTimer.singleShot(1500, self._refresh_taskbar_icon)
        # 启动稳定后才开启"窗口大小自动保存"，避免启动阶段的默认尺寸覆盖已存尺寸
        QTimer.singleShot(3000, self._enable_state_autosave)
        # 更新后的收尾（删备份 / 提示上次自动回滚）
        QTimer.singleShot(1200, self._check_update_leftover)
        # 换包脚本要等新版"活着的凭据"才敢删备份，所以再来两拍收尾：
        # 脚本正常结束（自删）后把备份清掉；脚本被强行结束也照样清理
        QTimer.singleShot(20000, self._check_update_leftover)
        QTimer.singleShot(60000, self._check_update_leftover)
        # 内存占用：启动稳定后回收一次；之后每分钟检查——后台静默 / 长时间空闲时
        # 再把工作集还给系统（任务管理器里显示的就是这个数）
        # 启动阶段先按 6 / 12 / 20 秒各回收一次（加载完那 100 多 MB 很快就降下来），
        # 之后交给 30 秒的常规定时器
        for _delay in (6000, 12000, 20000):
            QTimer.singleShot(_delay, self._trim_memory)
        self._last_input = time.time()
        _app_inst2 = QApplication.instance()
        if _app_inst2 is not None:
            _app_inst2.installEventFilter(self)
        self._mem_timer = QTimer(self)
        self._mem_timer.timeout.connect(self._memory_tick)
        self._mem_timer.start(30000)
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
        # 原生样式位修改后再恢复一次最大化，避免被 Windows 重置为普通窗口。
        if getattr(self, "_restore_maximized", False):
            QTimer.singleShot(0, self.showMaximized)

    def _enable_state_autosave(self):
        """启动稳定后开启窗口状态自动保存（大小/位置/最大化）。"""
        self._record_window_state()
        self._state_ready = True

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
            # 记住隐藏前的最大化状态：重新呼出时按原样恢复，不再被强制还原
            self._max_before_hide = self._is_window_maximized()
            self._record_window_state()
            self.hide()
        else:
            self._show_preserving_state()

    def _tray_show(self):
        self._show_preserving_state()

    # ---- 3.1.0：快速面板 / 提示音 / Win+V 接管 / 数据移植 ----

    def _play_sound(self, kind="copy"):
        """按设置播放提示音（默认关闭；任何异常都不影响主流程）。"""
        try:
            cfg = load_config()
            if kind == "copy" and not cfg.get("snd_copy", False):
                return
            if kind == "paste" and not cfg.get("snd_paste", False):
                return
            source = cfg.get("snd_%s_src" % kind, SOUND_SRC_SYSTEM)
            if source not in (SOUND_SRC_BUILTIN, SOUND_SRC_CUSTOM):
                # 旧配置里的「系统默认」统一按 YouBoard 音效处理
                source = SOUND_SRC_BUILTIN
            custom = cfg.get("snd_%s_file" % kind, "") or ""
            if not custom:
                custom = cfg.get("snd_custom", "") or ""
            play_notify_sound(kind, source, custom)
        except Exception:
            pass

    def _open_import_dialog(self):
        try:
            ImportDialog(self).exec()
        except Exception:
            pass

    def _apply_paste_sound_hook(self):
        """粘贴提示音需要知道「在任意程序里按了 Ctrl+V」，这里挂一个全局监听。

        优先用底层键盘钩子（ctypes）：它不会被 Win+V 接管的 suppress 钩子压掉；
        失败时才回退到 keyboard 库。只在开启粘贴提示音时挂载，关闭立即卸载，
        两种方式都不拦截按键。
        """
        try:
            want = bool(load_config().get("snd_paste", False))
        except Exception:
            want = False
        hook = getattr(self, "_paste_kbd_hook", None)
        name = getattr(self, "_paste_hook_name", None)
        if want and hook is None and name is None:
            if IS_WIN:
                try:
                    h = _CtrlVHook(self._on_global_paste)
                    h.start()
                    time.sleep(0.08)
                    if getattr(h, "ok", False):
                        self._paste_kbd_hook = h
                    else:
                        h.stop()
                except Exception:
                    self._paste_kbd_hook = None
            if getattr(self, "_paste_kbd_hook", None) is None and HAS_KEYBOARD:
                try:
                    self._paste_hook_name = _keyboard_lib.add_hotkey(
                        "ctrl+v", self._on_global_paste)
                except Exception:
                    self._paste_hook_name = None
        elif not want and (hook is not None or name is not None):
            self._remove_paste_sound_hook()
        if want:
            timer = getattr(self, "_paste_sound_timer", None)
            if timer is None:
                try:
                    self._paste_sound_timer = QTimer(self)
                    self._paste_sound_timer.timeout.connect(
                        self._poll_paste_sounds)
                    self._paste_sound_timer.start(80)
                except Exception:
                    self._paste_sound_timer = None

    def _remove_paste_sound_hook(self):
        hook = getattr(self, "_paste_kbd_hook", None)
        if hook is not None:
            try:
                hook.stop()
            except Exception:
                pass
            self._paste_kbd_hook = None
        name = getattr(self, "_paste_hook_name", None)
        if name is not None and HAS_KEYBOARD:
            try:
                _keyboard_lib.remove_hotkey(name)
            except Exception:
                pass
        self._paste_hook_name = None
        timer = getattr(self, "_paste_sound_timer", None)
        if timer is not None:
            try:
                timer.stop()
            except Exception:
                pass
            self._paste_sound_timer = None

    def _on_global_paste(self):
        """全局 Ctrl+V 回调（在钩子线程里）→ 交给界面线程播提示音。"""
        try:
            self._paste_sound_q.put_nowait(1)
        except Exception:
            pass

    def _poll_paste_sounds(self):
        """界面线程定时消费粘贴提示音事件（钩子线程只负责投递）。"""
        queued = 0
        while True:
            try:
                self._paste_sound_q.get_nowait()
            except Exception:
                break
            queued += 1
        if not queued:
            return
        try:
            if time.time() < getattr(self, "_mute_paste_sound_until", 0.0):
                return
        except Exception:
            pass
        self._play_sound("paste")

    def _apply_winv_takeover(self):
        """按配置启用 / 停用 Win+V 接管（默认关闭，需用户主动开启）。"""
        try:
            want = bool(load_config().get("takeover_winv", False))
        except Exception:
            want = False
        hook = getattr(self, "_winv_hook", None)
        if want and hook is None:
            if HAS_KEYBOARD:
                try:
                    # Win+V 直接开关整个工具窗口（与 Alt+Q 同一套显隐逻辑）
                    self._winv_name = _keyboard_lib.add_hotkey(
                        "windows+v", self._on_hotkey_threadsafe,
                        suppress=True)
                    self._winv_hook = "keyboard"
                    return
                except Exception:
                    pass
            hook = _WinVHook(self._on_hotkey_threadsafe)
            hook.start()
            time.sleep(0.12)
            if hook.ok:
                self._winv_hook = hook
            else:
                self._winv_hook = None
        elif not want and hook is not None:
            self._stop_winv_takeover()

    def _stop_winv_takeover(self):
        hook = getattr(self, "_winv_hook", None)
        if hook is None:
            return
        if hook == "keyboard" and HAS_KEYBOARD:
            try:
                _keyboard_lib.remove_hotkey(
                    getattr(self, "_winv_name", None))
            except Exception:
                pass
            self._winv_name = None
        else:
            try:
                hook.stop()
            except Exception:
                pass
        self._winv_hook = None

    def _show_preserving_state(self):
        """显示窗口时保持它原来的大小状态（最大化就还是最大化）。"""
        if self._is_window_maximized() or getattr(self, "_max_before_hide", False):
            self.showMaximized()
        else:
            self.showNormal()
        self.activateWindow()
        self.raise_()

    def _tray_quit(self):
        self._quitting = True
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
    "hk_fav": "ctrl+d",
    "hk_ai": "ctrl+i",
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


def _event_hotkey(event):
    """把一次按键事件转成 'ctrl+enter' 这类规范字符串（与 _canon_hotkey 一致）。"""
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
    key = event.key()
    if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
        parts.append("enter")
    elif key == Qt.Key.Key_Delete:
        parts.append("delete")
    elif key == Qt.Key.Key_Space:
        parts.append("space")
    elif key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
        parts.append("tab")
    elif key == Qt.Key.Key_Escape:
        parts.append("esc")
    elif key == Qt.Key.Key_Backspace:
        parts.append("backspace")
    elif Qt.Key.Key_F1 <= key <= Qt.Key.Key_F12:
        parts.append("f%d" % (key - Qt.Key.Key_F1 + 1))
    elif Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
        parts.append(chr(key - Qt.Key.Key_A + ord("a")))
    elif Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
        parts.append(str(key - Qt.Key.Key_0))
    else:
        try:
            name = QKeySequence(key).toString().lower()
        except Exception:
            name = ""
        parts.append(name if name else "?")
    return "+".join(parts)


_VK_NAMED = {
    "enter": 0x0D, "space": 0x20, "tab": 0x09, "esc": 0x1B,
    "escape": 0x1B, "backspace": 0x08, "delete": 0x2E, "del": 0x2E,
    "home": 0x24, "end": 0x23, "up": 0x26, "down": 0x28,
    "left": 0x25, "right": 0x27,
}


def _spec_to_vk(hk):
    """把 'ctrl+enter' 这类字符串转成 (需要的修饰键集合, 主键 vk)；失败返回 None。"""
    mods = set()
    vk = 0
    for p in _canon_hotkey(hk).split("+"):
        if p in ("ctrl", "control"):
            mods.add("ctrl")
        elif p == "alt":
            mods.add("alt")
        elif p == "shift":
            mods.add("shift")
        elif p in ("win", "super"):
            mods.add("win")
        elif p in _VK_NAMED:
            vk = _VK_NAMED[p]
        elif len(p) == 1 and p.isalpha():
            vk = ord(p.upper())
        elif len(p) == 1 and p.isdigit():
            vk = ord(p)
        elif p.startswith("f") and p[1:].isdigit():
            n = int(p[1:])
            if 1 <= n <= 12:
                vk = 0x70 + n - 1
    if not vk:
        return None
    return mods, vk


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


class _DialogHeader(QWidget):
    """Flat draggable header used by secondary dialogs."""

    HEIGHT = 40

    def __init__(self, dialog, title):
        super().__init__(dialog)
        self._dialog = dialog
        self._drag_pos = None
        self.setObjectName("dialogHeader")
        self.setFixedHeight(self.HEIGHT)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 8, 0)
        lay.setSpacing(8)
        if LOGO_ICO and os.path.exists(LOGO_ICO):
            icon = QLabel()
            icon.setFixedSize(20, 20)
            icon.setPixmap(QIcon(LOGO_ICO).pixmap(18, 18))
            lay.addWidget(icon)
        label = QLabel(title)
        label.setObjectName("dialogTitle")
        lay.addWidget(label)
        lay.addStretch()
        self._close_btn = QPushButton("✕")
        self._close_btn.setObjectName("dialogClose")
        self._close_btn.setFixedSize(30, 30)
        self._close_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._close_btn.clicked.connect(dialog.close)
        lay.addWidget(self._close_btn)
        self.setStyleSheet(f"""
            QWidget#dialogHeader {{ background: {C['SURFACE']};
                border-bottom: 1px solid {C['BORDER']}; }}
            QLabel {{ background: transparent; color: {C['TEXT']}; }}
            QLabel#dialogTitle {{ font-size: 12px; font-weight: 600; }}
            QPushButton#dialogClose {{ background: transparent; color: {C['TEXT_SEC']};
                border: none; border-radius: 6px; padding: 0; font-size: 13px; }}
            QPushButton#dialogClose:hover {{ background: {C['DANGER']}; color: #ffffff; }}
        """)

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position().toPoint()
        if self._close_btn.geometry().contains(pos):
            event.ignore()
            return
        handle = self._dialog.windowHandle()
        if handle is not None and handle.startSystemMove():
            event.accept()
            return
        self._drag_pos = (event.globalPosition().toPoint() -
                          self._dialog.frameGeometry().topLeft())
        event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self._dialog.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None


def _make_frameless_dialog(dialog, title):
    """Apply the shared flat dialog chrome and return its custom header."""
    dialog.setWindowTitle(title)
    dialog.setWindowFlags(
        Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
    return _DialogHeader(dialog, title)


# ===========================================================================
# Settings Dialog
# ===========================================================================
class _HotkeyDialog(QDialog):
    """独立的快捷键设置界面：全局显示/隐藏 + 各类动作快捷键。"""

    def __init__(self, parent, values):
        super().__init__(parent)
        header = _make_frameless_dialog(self, tr("set_hotkeys_title"))
        # 窗口级模态：只挡住宿主窗口，桌面小组件仍然可用
        self.setModal(True)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumWidth(520 if LANG == "en" else 440)
        self._values = dict(values)
        self._rows = {}
        self.setStyleSheet(f"""
            QDialog {{ background-color: {C['DIALOG_BG']};
                border: 1px solid {C['BORDER_LT']}; }}
            QLabel {{ background: transparent; color: {C['TEXT']}; }}
            QPushButton {{ background: {C['SURFACE2']}; color: {C['TEXT_SEC']};
                border: 1px solid {C['BORDER']}; border-radius: 6px;
                padding: 6px 14px; font-size: 12px; }}
            QPushButton:hover {{ background: {C['SURFACE3']}; color: {C['TEXT']}; }}
            QPushButton[cssClass="accent"] {{ background: {C['ACCENT']};
                color: #fff; border: none; font-weight: bold; }}
            QPushButton[cssClass="accent"]:hover {{ background: {C['ACCENT_HV']}; }}
        """)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(header)
        content = QWidget()
        lay = QVBoxLayout(content)
        lay.setContentsMargins(20, 16, 20, 16)
        outer.addWidget(content, 1)
        self._add_row(lay, "hotkey", tr("set_hotkey_title"))
        lay.addWidget(self._make_sep())
        for key in ("hk_copy", "hk_delete", "hk_pin", "hk_fav", "hk_ai",
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
        dialog_header = _make_frameless_dialog(dlg, tr("hk_dialog_title"))
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setModal(True)
        dlg.setMinimumWidth(340)
        outer = QVBoxLayout(dlg)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(dialog_header)
        body = QWidget()
        l = QVBoxLayout(body)
        l.setContentsMargins(20, 16, 20, 16)
        outer.addWidget(body, 1)
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
        header = _make_frameless_dialog(self, tr("phone_title"))
        self.setFixedSize(440 if LANG == "en" else 420, 760)
        if LOGO_ICO and os.path.exists(LOGO_ICO):
            self.setWindowIcon(QIcon(LOGO_ICO))
        self.setStyleSheet(f"""
            QDialog {{ background-color: {C['DIALOG_BG']};
                border: 1px solid {C['BORDER_LT']}; }}
            QLabel {{ background: transparent; color: {C['TEXT']}; }}
            QLabel#muted {{ color: {C['TEXT_MUTED']}; font-size: 11px; }}
            QLabel#url {{ color: {C['ACCENT']}; font-size: 11px; font-family: Consolas; }}
            QComboBox {{ background: {C['INPUT_BG']}; color: {C['TEXT']};
                border: 1px solid {C['BORDER']}; border-radius: 8px; padding: 6px 9px; font-size: 12px; }}
            QPushButton {{ background: {C['SURFACE2']}; color: {C['TEXT_SEC']};
                border: 1px solid transparent; border-radius: 8px; padding: 7px 14px; font-size: 12px; }}
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
                on_receive_image=self._queue_image,
                on_receive_file=self._queue_file,
                port=pick_free_port(base_port))
            app._phone_server = self._server
        self._server_was_running = self._server.running
        self._server.start()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(header)
        body = QWidget()
        root = QVBoxLayout(body)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(10)
        outer.addWidget(body, 1)

        title = QLabel(tr("phone_title"))
        title.setStyleSheet(f"color: {C['TEXT']}; font-size: 16px; font-weight: bold;")
        root.addWidget(title)

        self._qr_lbl = QLabel(tr("phone_generating"))
        self._qr_lbl.setFixedSize(300, 300)
        self._qr_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qr_lbl.setStyleSheet(
            "background: #ffffff; border-radius: 12px; border: none; "
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
        # 普通权限运行时 netsh 无法改防火墙，且部分安全策略会弹出启动错误；
        # 只在明确具备管理员权限时尝试，其他情况交给 Windows 首次监听提示。
        try:
            if not ctypes.windll.shell32.IsUserAnAdmin():
                return
        except Exception:
            return
        netsh = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                             "System32", "netsh.exe")
        if not os.path.exists(netsh):
            return

        def _work():
            try:
                exe = os.path.abspath(sys.executable)
                rule = "YouBoard Phone Transfer"
                out = subprocess.run(
                    [netsh, "advfirewall", "firewall", "show", "rule",
                     "name=" + rule],
                    capture_output=True, text=True, timeout=6,
                    creationflags=0x08000000)
                if rule in (out.stdout or ""):
                    return
                subprocess.run(
                    [netsh, "advfirewall", "firewall", "add", "rule",
                     "name=" + rule, "dir=in", "action=allow",
                     "program=" + exe, "enable=yes",
                     "profile=any"],
                    capture_output=True, text=True, timeout=6,
                    creationflags=0x08000000)
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

    def _queue_image(self, pil_image, name=""):
        """手机上传的图片（在服务线程里回调，丢进队列由界面线程入库）。"""
        self._inbox.put(("image", pil_image, name))

    def _queue_file(self, path, name=""):
        self._inbox.put(("file", path, name))

    def _drain_inbox(self):
        while True:
            try:
                item = self._inbox.get_nowait()
            except queue.Empty:
                break
            if isinstance(item, tuple) and item:
                kind = item[0]
                if kind == "image" and len(item) >= 2:
                    self._handle_phone_image(item[1], item[2] if len(item) > 2 else "")
                    continue
                if kind == "file" and len(item) >= 2:
                    self._handle_phone_file(item[1], item[2] if len(item) > 2 else "")
                    continue
            self._handle_phone_text(item)

    def _handle_phone_image(self, pil_image, name=""):
        """手机上传的图片进「图片」分类，并写入系统剪贴板。"""
        try:
            from PIL import Image as PILImage  # noqa: F401
        except Exception:
            pass
        try:
            h = self.app.store._image_hash(pil_image)
            self.app.store.mark_self_copy()
            self.app.store.add_image(pil_image, h, source_name=name or None)
            try:
                set_clipboard_image(pil_image)
            except Exception:
                pass
            self.app._refresh_all()
            self.app._update_desk_widget()
            self.app._set_status(tr("phone_image_received"), "ok")
            tray = getattr(self.app, "_tray", None)
            if tray is not None:
                try:
                    tray.showMessage("YouBoard", tr("phone_image_received"),
                                     QSystemTrayIcon.MessageIcon.Information, 3000)
                except Exception:
                    pass
        except Exception:
            pass

    def _handle_phone_file(self, path, name=""):
        """手机上传的普通文件：落到缓存目录后进「文件」分类。"""
        try:
            if not path or not os.path.exists(path):
                return
            self.app.store.mark_self_copy()
            self.app.store.add_files([path], self.app.store._files_hash([path]))
            self.app._refresh_all()
            self.app._update_desk_widget()
            self.app._set_status(tr("phone_file_received"), "ok")
            tray = getattr(self.app, "_tray", None)
            if tray is not None:
                try:
                    tray.showMessage("YouBoard", tr("phone_file_received"),
                                     QSystemTrayIcon.MessageIcon.Information, 3000)
                except Exception:
                    pass
        except Exception:
            pass

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

    def showEvent(self, event):
        super().showEvent(event)
        _apply_hand_cursor(self)          # 可点控件统一手型光标

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


# ===========================================================================
# 3.1.0 新增：提示音 / Win+V 接管 / 快速呼出面板 / 其它安装数据移植
# ===========================================================================

# 提示音来源：系统默认 / YouBoard 自带音效 / 用户自定义文件
SOUND_SRC_SYSTEM = "system"
SOUND_SRC_BUILTIN = "builtin"
SOUND_SRC_CUSTOM = "custom"
SOUND_BUILTIN_FILES = {"copy": "youboard_copy.wav",
                       "paste": "youboard_paste.wav"}


def builtin_sound_path(kind):
    """YouBoard 自带音效文件路径（打包后从 _MEIPASS/res 读取）。"""
    name = SOUND_BUILTIN_FILES.get(kind, SOUND_BUILTIN_FILES["copy"])
    path = _res_icon(name)
    return path if path and os.path.exists(path) else ""


def sound_source_label(kind, source, custom_path=""):
    """音效来源的显示名。"""
    if source == SOUND_SRC_CUSTOM and custom_path and os.path.exists(custom_path):
        return tr("set_sound_custom", name=os.path.basename(custom_path))
    return tr("set_sound_builtin")


def play_notify_sound(kind="copy", source=SOUND_SRC_BUILTIN, custom_path=""):
    """播放复制 / 粘贴提示音（只用 YouBoard 音效或用户自定义文件）。

    提示音属于锦上添花，任何失败都静默忽略，也不回退到系统提示音。
    """
    try:
        path = ""
        if source == SOUND_SRC_CUSTOM and custom_path and os.path.exists(custom_path):
            path = custom_path
        if not path:
            path = builtin_sound_path(kind)
        if not path:
            return False
        if IS_WIN:
            import winsound
            try:
                # 先停掉上一次可能还在播的提示音，保证连续复制 / 粘贴每次都重新响
                winsound.PlaySound(None, winsound.SND_PURGE)
            except Exception:
                pass
            winsound.PlaySound(path,
                               winsound.SND_FILENAME | winsound.SND_ASYNC)
            return True
        if IS_MAC:
            subprocess.Popen(["afplay", path],
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            return True
        return False
    except Exception as ex:
        # 播放失败不再默默吞掉：写进 error log，便于排查"听不到提示音"
        try:
            log_path = os.path.join(os.path.dirname(os.path.abspath(
                sys.executable if getattr(sys, "frozen", False) else __file__)),
                "youboard_error.log")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write("[%s] sound %s failed: %r\n"
                        % (time.strftime("%Y-%m-%d %H:%M:%S"), kind, ex))
        except Exception:
            pass
        return False


def clipboard_history_enabled():
    """读取 Windows 剪贴板历史开关状态：True / False / None（未知）。"""
    if not IS_WIN:
        return None
    try:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Clipboard") as k:
                value, _ = winreg.QueryValueEx(k, "EnableClipboardHistory")
                return bool(int(value))
        except FileNotFoundError:
            # 键不存在代表系统默认（开启）
            return True
    except Exception:
        return None


def disable_windows_clipboard_history():
    """关掉系统剪贴板历史（用户点按钮才会执行），返回是否成功。"""
    if not IS_WIN:
        return False
    try:
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Clipboard", 0,
                                winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, "EnableClipboardHistory", 0,
                              winreg.REG_DWORD, 0)
            try:
                winreg.SetValueEx(k, "EnableCloudClipboard", 0,
                                  winreg.REG_DWORD, 0)
            except OSError:
                pass
        return True
    except Exception:
        return False


def foreground_window():
    """记录当前前台窗口句柄（用于粘贴回原窗口）。"""
    if not IS_WIN:
        return 0
    try:
        return int(ctypes.windll.user32.GetForegroundWindow())
    except Exception:
        return 0


def _send_paste_shortcut():
    """发送一次粘贴快捷键（Windows: Ctrl+V，macOS: Cmd+V）。"""
    if IS_WIN:
        user32 = ctypes.windll.user32
        vk_control, vk_v, key_up = 0x11, 0x56, 0x0002
        user32.keybd_event(vk_control, 0, 0, 0)
        user32.keybd_event(vk_v, 0, 0, 0)
        user32.keybd_event(vk_v, 0, key_up, 0)
        user32.keybd_event(vk_control, 0, key_up, 0)
        return True
    if IS_MAC:
        script = ('tell application "System Events" to keystroke "v" '
                  'using command down')
        subprocess.Popen(["osascript", "-e", script],
                         stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        return True
    return False


def paste_into_window(hwnd):
    """把焦点交还给之前的窗口并粘贴；返回是否已发出粘贴动作。"""
    try:
        if IS_WIN and hwnd:
            user32 = ctypes.windll.user32
            try:
                user32.ShowWindow(hwnd, 9)      # SW_RESTORE
            except Exception:
                pass
            try:
                user32.SetForegroundWindow(hwnd)
            except Exception:
                pass
            time.sleep(0.09)
            return _send_paste_shortcut()
        if IS_MAC:
            return _send_paste_shortcut()
    except Exception:
        return False
    return False


class _WinVHook(threading.Thread):
    """底层键盘钩子：拦截 Win+V 并抑制系统剪贴板历史（ctypes 实现，无额外依赖）。"""

    WH_KEYBOARD_LL = 13
    WM_KEYDOWN = 0x0100
    WM_SYSKEYDOWN = 0x0104
    WM_KEYUP = 0x0101
    WM_SYSKEYUP = 0x0105
    VK_V = 0x56
    VK_LWIN = 0x5B
    VK_RWIN = 0x5C

    def __init__(self, callback):
        super().__init__(daemon=True, name="YouBoardWinVHook")
        self.callback = callback
        self.hook = None
        self.ok = False
        self._win_down = False
        self._suppress_up = False
        self._proc_ref = None
        self._stop = threading.Event()

    def run(self):
        if not IS_WIN:
            return
        from ctypes import wintypes

        class _KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [("vkCode", wintypes.DWORD),
                        ("scanCode", wintypes.DWORD),
                        ("flags", wintypes.DWORD),
                        ("time", wintypes.DWORD),
                        ("dwExtraInfo", ctypes.c_void_p)]

        user32 = ctypes.windll.user32
        HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_int,
                                      wintypes.WPARAM, wintypes.LPARAM)
        try:
            user32.GetMessageW.argtypes = [
                ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                wintypes.UINT, wintypes.UINT]
            user32.GetMessageW.restype = ctypes.c_int
            user32.CallNextHookEx.argtypes = [
                ctypes.c_void_p, ctypes.c_int,
                wintypes.WPARAM, wintypes.LPARAM]
            user32.CallNextHookEx.restype = ctypes.c_ssize_t
        except Exception:
            pass

        def _proc(code, wparam, lparam):
            if code == 0:
                try:
                    kb = ctypes.cast(lparam,
                                     ctypes.POINTER(_KBDLLHOOKSTRUCT)).contents
                    vk = int(kb.vkCode)
                    if vk in (self.VK_LWIN, self.VK_RWIN):
                        self._win_down = wparam in (self.WM_KEYDOWN,
                                                    self.WM_SYSKEYDOWN)
                    elif vk == self.VK_V and self._win_down:
                        if wparam in (self.WM_KEYDOWN, self.WM_SYSKEYDOWN):
                            self._suppress_up = True
                            try:
                                self.callback()
                            except Exception:
                                pass
                            return 1
                        if wparam in (self.WM_KEYUP, self.WM_SYSKEYUP):
                            self._suppress_up = False
                            return 1
                    elif vk == self.VK_V and self._suppress_up:
                        return 1
                except Exception:
                    pass
            return int(user32.CallNextHookEx(None, code, wparam, lparam))

        self._proc_ref = HOOKPROC(_proc)   # 保持引用，避免回调被回收
        try:
            self.hook = user32.SetWindowsHookExW(self.WH_KEYBOARD_LL,
                                                  self._proc_ref, None, 0)
        except Exception:
            self.hook = None
        if not self.hook:
            return
        self.ok = True
        msg = wintypes.MSG()
        while not self._stop.is_set():
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret in (0, -1):
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        try:
            user32.UnhookWindowsHookEx(self.hook)
        except Exception:
            pass
        self.hook = None
        self.ok = False

    def stop(self):
        self._stop.set()
        if IS_WIN:
            try:
                ctypes.windll.user32.PostThreadMessageW(self.ident, 0x0012, 0, 0)
            except Exception:
                pass


class _LLKeyboardHook(threading.Thread):
    """底层键盘钩子的公共骨架：只观察按键，不拦截（子类可自行选择抑制）。"""

    WH_KEYBOARD_LL = 13
    WM_KEYDOWN = 0x0100
    WM_SYSKEYDOWN = 0x0104
    WM_KEYUP = 0x0101
    WM_SYSKEYUP = 0x0105
    LLKHF_INJECTED = 0x10
    VK_V = 0x56

    def __init__(self, name="YouBoardKbdHook"):
        super().__init__(daemon=True, name=name)
        self.hook = None
        self.ok = False
        self._proc_ref = None
        self._stop = threading.Event()
        self._down = set()
        if IS_WIN:
            # 显式声明原型，避免 ctypes 把 MSG 指针当成别的类型报错
            try:
                from ctypes import wintypes as _wt
                _u32 = ctypes.windll.user32
                _u32.GetMessageW.argtypes = [
                    ctypes.POINTER(_wt.MSG), _wt.HWND,
                    _wt.UINT, _wt.UINT]
                _u32.GetMessageW.restype = ctypes.c_int
                _u32.CallNextHookEx.argtypes = [
                    ctypes.c_void_p, ctypes.c_int, _wt.WPARAM, _wt.LPARAM]
                _u32.CallNextHookEx.restype = ctypes.c_ssize_t
            except Exception:
                pass

    def handle(self, vk, is_down, injected):
        """子类实现：返回 True 表示已处理（按键会被抑制）。"""
        return False

    def mods_down(self):
        """当前按住的修饰键（由钩子自己维护，不依赖系统查询）。"""
        d = self._down
        return {
            "ctrl": any(v in d for v in (0x11, 0xA2, 0xA3)),
            "alt": any(v in d for v in (0x12, 0xA4, 0xA5)),
            "shift": any(v in d for v in (0x10, 0xA0, 0xA1)),
            "win": any(v in d for v in (0x5B, 0x5C)),
        }

    def run(self):
        if not IS_WIN:
            return
        from ctypes import wintypes

        class _KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [("vkCode", wintypes.DWORD),
                        ("scanCode", wintypes.DWORD),
                        ("flags", wintypes.DWORD),
                        ("time", wintypes.DWORD),
                        ("dwExtraInfo", ctypes.c_void_p)]

        user32 = ctypes.windll.user32
        HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_int,
                                      wintypes.WPARAM, wintypes.LPARAM)

        def _proc(code, wparam, lparam):
            if code == 0:
                try:
                    kb = ctypes.cast(lparam,
                                     ctypes.POINTER(_KBDLLHOOKSTRUCT)).contents
                    vk = int(kb.vkCode)
                    injected = bool(int(kb.flags) & self.LLKHF_INJECTED)
                    if wparam in (self.WM_KEYDOWN, self.WM_SYSKEYDOWN):
                        self._down.add(vk)
                        if self.handle(vk, True, injected):
                            return 1
                    elif wparam in (self.WM_KEYUP, self.WM_SYSKEYUP):
                        self._down.discard(vk)
                        if self.handle(vk, False, injected):
                            return 1
                except Exception:
                    pass
            return int(user32.CallNextHookEx(None, code, wparam, lparam))

        self._proc_ref = HOOKPROC(_proc)   # 保持引用，避免回调被回收
        try:
            self.hook = user32.SetWindowsHookExW(self.WH_KEYBOARD_LL,
                                                  self._proc_ref, None, 0)
        except Exception:
            self.hook = None
        if not self.hook:
            return
        self.ok = True
        msg = wintypes.MSG()
        while not self._stop.is_set():
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret in (0, -1):
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        try:
            user32.UnhookWindowsHookEx(self.hook)
        except Exception:
            pass
        self.hook = None
        self.ok = False

    def stop(self):
        self._stop.set()
        if IS_WIN:
            try:
                ctypes.windll.user32.PostThreadMessageW(self.ident, 0x0012, 0, 0)
            except Exception:
                pass


class _CtrlVHook(_LLKeyboardHook):
    """全局监听 Ctrl+V（不拦截按键），用于粘贴提示音。

    自己发出的模拟 Ctrl+V（粘贴回原窗口）带 injected 标记，直接跳过，
    不会重复响；真实键盘按下则会回调。
    """

    def __init__(self, callback):
        super().__init__("YouBoardCtrlVHook")
        self.callback = callback

    def handle(self, vk, is_down, injected):
        if not is_down or injected or vk != self.VK_V:
            return False
        if self.mods_down()["ctrl"]:
            try:
                self.callback()
            except Exception:
                pass
        return False


class _ImportScanWorker(QThread):
    """后台扫描其它 YouBoard 安装，避免阻塞界面。"""

    done = pyqtSignal(list)

    def run(self):
        try:
            found = find_installations()
        except Exception:
            found = []
        self.done.emit(found)


class ImportDialog(QDialog):
    """自动识别本机其它 YouBoard 安装，由用户选择后把数据合并进来。"""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        header = _make_frameless_dialog(self, tr("port_title"))
        self.resize(620 if LANG == "en" else 540, 420)
        self.setMinimumSize(460, 320)
        if LOGO_ICO and os.path.exists(LOGO_ICO):
            self.setWindowIcon(QIcon(LOGO_ICO))
        self.setStyleSheet(f"""
            QDialog {{ background-color: {C['DIALOG_BG']};
                border: 2px solid {C['DIALOG_EDGE']}; }}
            QLabel {{ background: transparent; color: {C['TEXT']}; }}
            QPushButton {{ background: {C['SURFACE2']}; color: {C['TEXT_SEC']};
                border: 1px solid {C['BORDER']}; border-radius: 6px;
                padding: 6px 14px; font-size: 12px; }}
            QPushButton:hover {{ background: {C['SURFACE3']}; color: {C['TEXT']}; }}
            QPushButton[cssClass="accent"] {{ background: {C['ACCENT']};
                color: {_on_accent_color()}; border: none; font-weight: bold; }}
        """)
        self._installs = []
        self._worker = None
        self._closed = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(header)
        _content = QWidget()
        root.addWidget(_content, 1)
        content_lay = QVBoxLayout(_content)
        content_lay.setContentsMargins(14, 10, 14, 12)
        content_lay.setSpacing(8)

        self._status = QLabel(tr("port_scanning"))
        self._status.setStyleSheet(f"color: {C['TEXT_SEC']}; font-size: 12px;")
        content_lay.addWidget(self._status)

        self._list = QListWidget()
        self._list.setStyleSheet(
            f"QListWidget {{ background: {C['SURFACE2']};"
            f" border: 1px solid {C['BORDER']}; border-radius: 8px;"
            f" color: {C['TEXT']}; outline: none; }}"
            f"QListWidget::item {{ padding: 8px; }}"
            f"QListWidget::item:selected {{ background: {C['ACCENT_DIM']};"
            f" color: {C['TEXT']}; }}")
        self._list.itemDoubleClicked.connect(lambda _: self._do_import())
        content_lay.addWidget(self._list, 1)

        btns = QHBoxLayout()
        browse = QPushButton(tr("port_browse"))
        browse.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        browse.clicked.connect(self._browse)
        btns.addWidget(browse)
        rescan = QPushButton(tr("port_rescan"))
        rescan.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        rescan.clicked.connect(lambda: self._scan())
        btns.addWidget(rescan)
        btns.addStretch()
        cancel = QPushButton(tr("btn_cancel"))
        cancel.clicked.connect(self.reject)
        btns.addWidget(cancel)
        self._import_btn = QPushButton(tr("port_import"))
        self._import_btn.setProperty("cssClass", "accent")
        self._import_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._import_btn.clicked.connect(self._do_import)
        btns.addWidget(self._import_btn)
        content_lay.addLayout(btns)

        QTimer.singleShot(0, self._scan)

    # ---- 扫描 ----

    def _scan(self, extra=None):
        if self._closed:
            return
        self._status.setText(tr("port_scanning"))
        self._list.clear()
        self._installs = []
        self._worker = _ImportScanWorker(self)
        self._worker.done.connect(self._on_scanned)
        self._worker.start()

    def _on_scanned(self, found):
        self._worker = None
        if self._closed:
            return
        self._installs = list(found or [])
        self._fill()

    def _fill(self):
        self._list.clear()
        for info in self._installs:
            path = info.get("path", "")
            if info.get("readable"):
                ts = info.get("modified") or 0
                try:
                    when = (datetime.fromtimestamp(ts).strftime(TIME_FORMAT)
                            if ts else "—")
                except (OSError, OverflowError, ValueError):
                    when = "—"
                text = (f"{path}\n{tr('port_col_count')}: {info.get('total', 0)}"
                        f"   ·   {tr('port_col_time')}: {when}")
            else:
                text = f"{path}\n{tr('port_unreadable')}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, info)
            self._list.addItem(item)
        if self._installs:
            self._list.setCurrentRow(0)
            self._status.setText(tr("port_found", n=len(self._installs)))
        else:
            self._status.setText(tr("port_none"))

    def _browse(self):
        folder = QFileDialog.getExistingDirectory(self, tr("port_browse"))
        if not folder:
            return
        from youboard_core import inspect_installation as _inspect
        info = _inspect(folder)
        if not info:
            _info_card(self, tr("port_title"), tr("port_failed"),
                       kind="warning")
            return
        self._installs.append(info)
        self._fill()
        self._list.setCurrentRow(self._list.count() - 1)

    # ---- 导入 ----

    def _do_import(self):
        item = self._list.currentItem()
        if item is None:
            return
        info = item.data(Qt.ItemDataRole.UserRole) or {}
        folder = info.get("path", "")
        if not folder:
            return
        cats = read_foreign_history(folder)
        if cats is None:
            _info_card(self, tr("port_title"), tr("port_failed"),
                       kind="warning")
            return
        if not _confirm_card(
                self, tr("port_title"),
                tr("port_confirm", path=folder, n=info.get("total", 0)),
                ok_text=tr("btn_ok")):
            return
        before = self.app.store.count()
        try:
            # 先搬运图片与正文文件，再合并条目，避免记录指向不存在的资源
            copy_installation_assets(folder, cats)
            self.app.store.merge_history(cats)
        except Exception:
            _info_card(self, tr("port_title"), tr("port_failed"),
                       kind="warning")
            return
        added = self.app.store.count() - before
        try:
            self.app._refresh_all()
            self.app._update_desk_widget()
            self.app._rebuild_index()
        except Exception:
            pass
        _info_card(self, tr("port_title"),
                   tr("port_done", n=added) if added else tr("port_nothing"),
                   kind="latest" if added else "warning")
        # 导入完成后确保主窗口看得见：即使用户是从托盘打开的导入窗口、
        # 或者中途把窗口收进了托盘，也自动把工具显示出来，不用手动再开一次
        try:
            self.app.show()
            if self.app.isMinimized():
                self.app.showNormal()
            self.app.raise_()
            self.app.activateWindow()
        except Exception:
            pass

    def closeEvent(self, event):
        """关闭窗口时收好后台扫描线程，避免程序退出时线程仍在运行。"""
        self._closed = True
        worker = getattr(self, "_worker", None)
        if worker is not None and worker.isRunning():
            worker.wait(8000)
        self._worker = None
        event.accept()

    def reject(self):
        self._closed = True
        worker = getattr(self, "_worker", None)
        if worker is not None and worker.isRunning():
            worker.wait(8000)
        self._worker = None
        super().reject()


class _SkinTile(QFrame):
    """皮肤卡片：上面一小块配色预览，下面是皮肤名；选中时描边高亮。"""

    clicked = pyqtSignal(str)

    def __init__(self, skin_id, title, parent=None):
        super().__init__(parent)
        self._skin_id = skin_id
        self._selected = False
        self.setObjectName("skinTile")
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(7, 7, 7, 5)
        lay.setSpacing(4)
        preview = QLabel()
        preview.setObjectName("skinPreview")
        preview.setPixmap(_skin_preview_pixmap(skin_id))
        preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview = preview
        lay.addWidget(preview, 0, Qt.AlignmentFlag.AlignHCenter)
        self._name = QLabel(title)
        self._name.setObjectName("skinName")
        self._name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._name)
        self.set_selected(False)

    def skin_id(self):
        return self._skin_id

    def refresh_preview(self):
        """自定义皮肤改了颜色后，重新画一遍预览小图。"""
        try:
            self._preview.setPixmap(_skin_preview_pixmap(self._skin_id))
        except Exception:
            pass

    def set_selected(self, flag):
        self._selected = bool(flag)
        if self._selected:
            self.setStyleSheet(
                f"QFrame#skinTile {{ background: {C['ACCENT_DIM']}; "
                f"border: 2px solid {C['ACCENT']}; border-radius: 10px; }}")
            self._name.setStyleSheet(
                f"background: transparent; color: {C['TEXT']}; "
                f"font-size: 11px; font-weight: 600;")
        else:
            self.setStyleSheet(
                f"QFrame#skinTile {{ background: {C['SURFACE2']}; "
                f"border: 2px solid {C['BORDER']}; border-radius: 10px; }}"
                f"QFrame#skinTile:hover {{ border-color: {C['BORDER_LT']}; }}")
            self._name.setStyleSheet(
                f"background: transparent; color: {C['TEXT_SEC']}; "
                f"font-size: 11px;")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._skin_id)
        super().mousePressEvent(event)


class SettingsDialog(QDialog):

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        header = _make_frameless_dialog(self, tr("settings_title"))
        # 窗口级模态：设置窗口只挡住宿主窗口，桌面小组件照样能点、能复制
        # （应用级模态会禁用整个程序的所有窗口，包括小组件）
        self.setWindowModality(Qt.WindowModality.WindowModal)
        # 尺寸收敛到"正常窗口"大小：不超屏幕可用区，也能缩到更小
        _avail = QApplication.primaryScreen().availableGeometry()
        _w = 600 if LANG == "en" else 480
        _h = 660
        self.resize(min(_w, max(360, _avail.width() - 120)),
                    min(_h, max(360, _avail.height() - 120)))
        self.setMinimumSize(420, 420)
        if LOGO_ICO and os.path.exists(LOGO_ICO):
            self.setWindowIcon(QIcon(LOGO_ICO))

        cfg = load_config()
        self._lang_sel = LANG if LANG in STRINGS else "zh"
        self._theme_sel = cfg.get("theme", "dark")
        self._bg_path = cfg.get("bg_image", "")
        self._retention_policy = (cfg.get("history_retention")
                                  or {"mode": "forever"})

        root = QVBoxLayout(self)
        # 预留 2px：保证面板外沿的描边不会被内部控件盖住（左侧曾经整条消失）
        root.setContentsMargins(2, 2, 2, 2)
        root.setSpacing(0)
        root.addWidget(header)
        self.setStyleSheet(f"""
            QDialog {{ background-color: {C['DIALOG_BG']};
                border: 2px solid {C['DIALOG_EDGE']}; }}
            QScrollArea {{ background: transparent; border: none; }}
            QScrollArea > QWidget > QWidget {{ background: {C['DIALOG_BG']}; }}
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
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0; width: 0; background: none; border: none; }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
                background: transparent; border: none; }}
            QScrollBar:horizontal {{ background: transparent; height: 8px; }}
            QScrollBar::handle:horizontal {{ background: {C['BORDER_LT']};
                border-radius: 4px; min-width: 30px; }}
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
                height: 0; width: 0; background: none; border: none; }}
            QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
                background: transparent; border: none; }}
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
        self._lay.setContentsMargins(14, 9, 14, 9)
        self._lay.setSpacing(7)
        # 已排布的模块数：用来给"模块之间"额外留白，做出 组间 > 组内 的分组感
        self._card_n = 0
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
        # 点 ✕ 的行为（放在最前面）：默认直接退出，开启后收进托盘
        close_row = QHBoxLayout()
        ct_lbl = QLabel(tr("set_close_tray"))
        # 跟其它条目同一字号、不加粗，别抢模块标题的视觉重心
        ct_lbl.setStyleSheet(f"color: {C['TEXT']}; font-weight: 500;")
        self._close_tray_lbl = ct_lbl
        close_row.addWidget(ct_lbl, 1)
        self._close_tray_state = QLabel("")
        close_row.addWidget(self._close_tray_state)
        self._close_tray_cb = QCheckBox()
        self._close_tray_cb.setChecked(
            bool(cfg.get("close_to_tray", False)))
        self._close_tray_cb.toggled.connect(self._sync_close_tray_ui)
        close_row.addWidget(self._close_tray_cb)
        self._lay.addLayout(close_row)
        self._sync_close_tray_ui()
        self._add_sep()
        auto_row = QHBoxLayout()
        t1 = QLabel(tr("set_autostart"))
        t1.setStyleSheet(f"color: {C['TEXT']}; font-weight: 500;")
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
        hk_title.setStyleSheet(f"color: {C['TEXT']}; font-weight: 500;")
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
        wg_title.setStyleSheet(f"color: {C['TEXT']}; font-weight: 500;")
        widget_row.addWidget(wg_title, 1)
        self._widget_cb = QCheckBox()
        self._widget_cb.setChecked(bool(cfg.get("desktop_widget", True)))
        widget_row.addWidget(self._widget_cb)
        self._lay.addLayout(widget_row)
        self._add_sep()

        # Temporary session row (merged from privacy mode)
        session_row = QHBoxLayout()
        ss_title = QLabel(tr("set_session_title"))
        ss_title.setStyleSheet(f"color: {C['TEXT']}; font-weight: 500;")
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

        # History retention card
        self._card(tr("set_retention"))
        ret_row = QHBoxLayout()
        self._retention_lbl = QLabel(_retention_summary(self._retention_policy))
        self._retention_lbl.setStyleSheet(
            f"color: {C['TEXT_SEC']}; font-size: 11px;")
        self._retention_lbl.setWordWrap(True)
        ret_row.addWidget(self._retention_lbl, 1)
        ret_btn = QPushButton(tr("set_retention_open"))
        ret_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        ret_btn.clicked.connect(self._open_retention)
        ret_row.addWidget(ret_btn)
        self._lay.addLayout(ret_row)

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

        # 皮肤：和暗色 / 亮色同级，只能选一种（三列配色预览卡片，紧凑清晰）
        skin_title = QLabel(tr("set_skin"))
        skin_title.setStyleSheet(
            f"color: {C['TEXT_SEC']}; font-size: 11px; font-weight: bold; "
            f"letter-spacing: 1px; padding-top: 10px;")
        self._lay.addWidget(skin_title)
        self._skin_tiles = {}
        skin_grid = QGridLayout()
        skin_grid.setContentsMargins(0, 0, 0, 0)
        skin_grid.setHorizontalSpacing(10)
        skin_grid.setVerticalSpacing(8)
        for col in range(3):
            skin_grid.setColumnStretch(col, 1)
        skin_items = [(spec["id"], tr("skin_" + spec["id"]))
                      for spec in _SKIN_SPECS]
        skin_items.append((CUSTOM_SKIN_ID, tr("skin_custom")))
        for idx, (sid, label) in enumerate(skin_items):
            tile = _SkinTile(sid, label)
            tile.clicked.connect(self._pick_theme)
            skin_grid.addWidget(tile, idx // 3, idx % 3)
            self._skin_tiles[sid] = tile
        self._lay.addLayout(skin_grid)
        self._custom_skin_btn = QPushButton(tr("set_skin_custom_edit"))
        self._custom_skin_btn.setCursor(
            QCursor(Qt.CursorShape.PointingHandCursor))
        self._custom_skin_btn.clicked.connect(self._edit_custom_skin)
        self._lay.addWidget(self._custom_skin_btn, 0,
                            Qt.AlignmentFlag.AlignLeft)
        self._paint_theme()

        # Background card
        self._card(tr("set_bg"))
        bg_row = QHBoxLayout()
        self._bg_lbl = QLabel(self._bg_display_name())
        self._bg_lbl.setStyleSheet(f"color: {C['TEXT_SEC']}; font-size: 11px;")
        self._bg_lbl.setWordWrap(True)
        bg_row.addWidget(self._bg_lbl, 1)
        self._lay.addLayout(bg_row)

        bg_btn_row = QHBoxLayout()
        sel_btn = QPushButton(tr("set_bg_select"))
        sel_btn.clicked.connect(self._select_bg)
        bg_btn_row.addWidget(sel_btn)
        clr_btn = QPushButton(tr("set_bg_clear"))
        clr_btn.clicked.connect(self._clear_bg)
        bg_btn_row.addWidget(clr_btn)
        wall_btn = QPushButton(tr("set_bg_wallpaper"))
        wall_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        wall_btn.clicked.connect(self._use_wallpaper)
        bg_btn_row.addWidget(wall_btn)
        bg_btn_row.addStretch()
        self._lay.addLayout(bg_btn_row)

        # 历史壁纸：横向缩略图，点击可再次使用
        hist_lbl = QLabel(tr("set_bg_history"))
        hist_lbl.setStyleSheet(f"color: {C['TEXT_MUTED']}; font-size: 11px; font-weight: bold; "
                               f"letter-spacing: 1px; padding-top: 6px;")
        self._lay.addWidget(hist_lbl)
        self._bg_history = QListWidget()
        self._bg_history.setViewMode(QListView.ViewMode.IconMode)
        self._bg_history.setFlow(QListView.Flow.LeftToRight)
        self._bg_history.setWrapping(False)
        self._bg_history.setFixedHeight(62)
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

        # 提示音 card（默认关闭）
        self._card(tr("set_sound"))
        # 复制 / 粘贴各一行：开关 + 音效来源（YouBoard 音效 / 自定义）
        self._snd_cbs = {}
        self._snd_btns = {}
        self._snd_src = {
            "copy": cfg.get("snd_copy_src", SOUND_SRC_BUILTIN),
            "paste": cfg.get("snd_paste_src", SOUND_SRC_BUILTIN),
        }
        self._snd_file = {
            "copy": cfg.get("snd_copy_file", cfg.get("snd_custom", "")) or "",
            "paste": cfg.get("snd_paste_file", "") or "",
        }
        for kind in ("copy", "paste"):
            row = QHBoxLayout()
            lbl = QLabel(tr("set_sound_" + kind))
            lbl.setStyleSheet(f"color: {C['TEXT']}; font-weight: 500;")
            row.addWidget(lbl, 1)
            pick = QPushButton(self._snd_button_text(kind))
            pick.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            pick.clicked.connect(lambda _, k=kind: self._pick_sound_source(k))
            row.addWidget(pick)
            cb = QCheckBox()
            cb.setChecked(bool(cfg.get("snd_" + kind, False)))
            row.addWidget(cb)
            self._lay.addLayout(row)
            self._snd_cbs[kind] = cb
            self._snd_btns[kind] = pick

        # AI 服务 card（详细配置在卡片弹层里，设置页保持紧凑）
        self._card(tr("set_ai"))
        self._ai_values = load_ai_settings(cfg)
        ai_row = QHBoxLayout()
        self._ai_lbl = QLabel(self._ai_summary())
        self._ai_lbl.setStyleSheet(f"color: {C['TEXT_SEC']}; font-size: 11px;")
        self._ai_lbl.setWordWrap(True)
        ai_row.addWidget(self._ai_lbl, 1)
        ai_open = QPushButton(tr("set_ai_open"))
        ai_open.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        ai_open.clicked.connect(self._open_ai)
        ai_row.addWidget(ai_open)
        self._lay.addLayout(ai_row)

        # 浏览器扩展 card（详细配置在独立卡片弹层里，设置页只留一行）
        self._card(tr("set_bridge"))
        br_row = QHBoxLayout()
        self._bridge_lbl = QLabel(self._bridge_summary())
        self._bridge_lbl.setStyleSheet(
            f"color: {C['TEXT_SEC']}; font-size: 11px;")
        self._bridge_lbl.setWordWrap(True)
        br_row.addWidget(self._bridge_lbl, 1)
        br_open = QPushButton(tr("set_ai_open"))
        br_open.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        br_open.clicked.connect(self._open_bridge)
        br_row.addWidget(br_open)
        self._lay.addLayout(br_row)

        # Win+V 接管 card（默认关闭，带风险提示）
        self._card(tr("set_winv"))
        winv_row = QHBoxLayout()
        winv_lbl = QLabel(tr("set_winv_takeover"))
        winv_lbl.setStyleSheet(f"color: {C['TEXT']}; font-weight: 500;")
        winv_row.addWidget(winv_lbl, 1)
        winv_row.addStretch()
        self._winv_onoff = QLabel("")
        winv_row.addWidget(self._winv_onoff)
        self._winv_cb = QCheckBox()
        self._winv_cb.setChecked(bool(cfg.get("takeover_winv", False)))
        self._winv_cb.stateChanged.connect(self._on_winv_toggled)
        winv_row.addWidget(self._winv_cb)
        self._lay.addLayout(winv_row)
        self._winv_state = QLabel(self._winv_state_text())
        self._winv_state.setStyleSheet(
            f"color: {C['TEXT_MUTED']}; font-size: 11px;")
        self._winv_state.setWordWrap(True)
        self._lay.addWidget(self._winv_state)
        self._winv_disable = QPushButton(tr("set_winv_disable"))
        self._winv_disable.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._winv_disable.clicked.connect(self._disable_winv_history)
        self._lay.addWidget(self._winv_disable, 0,
                            Qt.AlignmentFlag.AlignLeft)
        self._sync_winv_ui()

        # 数据移植 card
        self._card(tr("set_port"))
        port_desc = QLabel(tr("set_port_desc"))
        port_desc.setStyleSheet(f"color: {C['TEXT_SEC']}; font-size: 11px;")
        port_desc.setWordWrap(True)
        self._lay.addWidget(port_desc)
        port_open = QPushButton(tr("set_port_open"))
        port_open.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        port_open.clicked.connect(lambda: ImportDialog(self.app).exec())
        self._lay.addWidget(port_open, 0, Qt.AlignmentFlag.AlignLeft)

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

        # 设置窗口是无边框窗口，补回四边和四角的拖拽缩放热区。
        self._resize_edges = [
            _EdgeHandle(self, edge) for edge in ("left", "right", "top", "bottom")]
        self._resize_corners = [
            _CornerHandle(self, corner) for corner in ("tl", "tr", "bl", "br")]
        QTimer.singleShot(0, self._place_resize_handles)

        # 设置窗口打开期间兜底刷新：即使主窗口轮询受阻，复制的新内容也会实时显示
        self._live_refresh_timer = QTimer(self)
        self._live_refresh_timer.timeout.connect(self._live_refresh)
        self._live_refresh_timer.start(800)
        # 兜底光标刷新：修复 Qt 滚动区按钮手型光标在部分进入方向下不生效的问题
        self._cursor_timer = QTimer(self)
        self._cursor_timer.timeout.connect(self._force_cursor_refresh)
        self._cursor_timer.start(120)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place_resize_handles()

    def _place_resize_handles(self):
        w, h = self.width(), self.height()
        t = _EdgeHandle.THICKNESS
        if getattr(self, "_resize_edges", None):
            left, right, top, bottom = self._resize_edges
            left.setGeometry(0, t, t, max(0, h - 2 * t))
            right.setGeometry(max(0, w - t), t, t, max(0, h - 2 * t))
            top.setGeometry(t, 0, max(0, w - 2 * t), t)
            bottom.setGeometry(t, max(0, h - t), max(0, w - 2 * t), t)
            for handle in self._resize_edges:
                handle.show()
                handle.raise_()
        if getattr(self, "_resize_corners", None):
            s = _CornerHandle.SIZE
            positions = {
                "tl": (0, 0),
                "tr": (max(0, w - s), 0),
                "bl": (0, max(0, h - s)),
                "br": (max(0, w - s), max(0, h - s)),
            }
            for handle in self._resize_corners:
                x, y = positions[handle._corner]
                handle.setGeometry(x, y, s, s)
                handle.show()
                handle.raise_()

    def _refresh_button_cursors(self):
        """滚动区按钮手型光标刷新：修复 Qt 滚动区在鼠标静止时
        滚动内容不更新子控件光标的问题。

        以前这里只处理"已经是手型"的按钮，从没设过光标的按钮（例如「打开同步窗口」）
        会一直是箭头光标，表现就是"只有从下往上扫过才变手型"。现在统一补齐。
        """
        _apply_hand_cursor(self)

    def _live_cursor_refresh(self, widget=None):
        """定时按鼠标位置补一次手型光标。

        Qt 只在该子控件收到进入事件时才更新光标；鼠标不动、内容滚过来、
        或者从别处移进来时，鼠标下面的按钮常常仍是箭头（用户看到的就是
        "从上往下扫没有手型，只有从下往上才有"）。这里直接按位置算，
        并用 override 光标强制生效——实测只有这样系统光标才会立刻跟着变。
        """
        if widget is not None:
            w = widget
        else:
            try:
                w = _widget_under_cursor()
            except Exception:
                w = None
        want_hand = (isinstance(w, (QPushButton, QCheckBox, QComboBox))
                     and w.isEnabled())
        if want_hand:
            if not getattr(self, "_cursor_override_on", False):
                QApplication.setOverrideCursor(
                    QCursor(Qt.CursorShape.PointingHandCursor))
                self._cursor_override_on = True
        else:
            self._clear_cursor_override()

    def _clear_cursor_override(self):
        """只撤掉自己设的那个手型覆盖光标，别动别人设的。"""
        if getattr(self, "_cursor_override_on", False):
            self._cursor_override_on = False
            try:
                QApplication.restoreOverrideCursor()
            except Exception:
                pass

    def _start_cursor_timer(self):
        timer = getattr(self, "_cursor_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.timeout.connect(self._live_cursor_refresh)
            self._cursor_timer = timer
        timer.start(150)
    def _force_cursor_refresh(self):
        """兜底光标刷新（只刷新按钮自己的手型光标，不再把缩放箭头钉到子控件上）。"""
        _apply_hand_cursor(self)
        _refresh_cursor_under_mouse()

    def showEvent(self, event):
        super().showEvent(event)
        self._refresh_button_cursors()
        self._start_cursor_timer()

    def hideEvent(self, event):
        timer = getattr(self, "_cursor_timer", None)
        if timer is not None:
            timer.stop()
        self._clear_cursor_override()
        super().hideEvent(event)

    def _live_refresh(self):
        try:
            self.app._poll_monitor()
        except Exception:
            pass

    def _card(self, title):
        # 模块之间比模块内部多留 8px：分组靠留白就能一眼看出来
        if self._card_n:
            self._lay.addSpacing(8)
        self._card_n += 1
        box = QFrame()
        box.setObjectName("settingsSection")
        box.setStyleSheet(
            f"QFrame#settingsSection {{ background: {C['SURFACE3']};"
            f" border: 1px solid {C['BORDER_LT']}; border-radius: 8px; }}")
        row = QHBoxLayout(box)
        row.setContentsMargins(10, 4, 10, 4)
        row.setSpacing(8)
        accent = QFrame()
        accent.setFixedSize(3, 16)
        accent.setStyleSheet(
            f"background: {C['ACCENT']}; border: none; border-radius: 1px;")
        row.addWidget(accent)
        lbl = QLabel(title)
        # 模块标题是这一层最高一级：比条目更大、用主文字色（以前 11px 次级色，像注脚）
        lbl.setStyleSheet(
            f"color: {C['TEXT']}; font-size: 13px; font-weight: 700;"
            " letter-spacing: 0.5px;")
        row.addWidget(lbl)
        row.addStretch()
        self._lay.addWidget(box)

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
                btn.setStyleSheet(f"background: {C['ACCENT']}; color: {_on_accent_color()}; font-weight: bold; "
                                  f"border-radius: 6px; padding: 7px 16px;")
            else:
                btn.setStyleSheet(f"background: {C['SURFACE3']}; color: {C['TEXT_SEC']}; "
                                  f"border-radius: 6px; padding: 7px 16px;")
        for sid, tile in getattr(self, "_skin_tiles", {}).items():
            tile.set_selected(sid == self._theme_sel)

    def _edit_custom_skin(self):
        """自定义皮肤颜色：底色 / 面板 / 文字 / 强调色。"""
        cfg = load_config()
        current = {}
        for key, default in _CUSTOM_SKIN_DEFAULTS.items():
            value = str(cfg.get(key, default) or default)
            current[key] = value if QColor(value).isValid() else default
        dlg = _CustomSkinDialog(self, self.app, current)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        cfg = load_config()
        for key, value in dlg.colors.items():
            cfg[key] = value
        save_config(cfg)
        self._theme_sel = CUSTOM_SKIN_ID
        self._custom_skin_dirty = True
        tile = getattr(self, "_skin_tiles", {}).get(CUSTOM_SKIN_ID)
        if tile is not None:
            tile.refresh_preview()
        self._paint_theme()

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
        menu = _RoundMenu(self)
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
        for key in ("hk_copy", "hk_delete", "hk_pin", "hk_fav", "hk_ai",
                    "hk_next_tab", "hk_prev_tab"):
            vals[key] = _canon_hotkey(
                cfg.get(key, _ACTION_HOTKEY_DEFAULTS[key]))
        return vals

    def _open_hotkeys(self):
        """打开独立的动作快捷键设置界面。"""
        dlg = _HotkeyDialog(self, self._hotkey_values)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._hotkey_values = dlg.values()

    def _open_retention(self):
        """打开紧凑的历史保留策略卡片并立即保存策略。"""
        dlg = _RetentionDialog(self, self.app, self._retention_policy)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self._retention_policy = dlg.policy()
        cfg = load_config()
        cfg["history_retention"] = self._retention_policy
        save_config(cfg)
        self._retention_lbl.setText(
            _retention_summary(self._retention_policy))
        self.app._apply_retention_policy()

    # ---- AI 服务（自带 Key；只发送选中的那一条记录） ----
    def _bridge_conn_text(self):
        """当前应该给扩展粘贴的那一行连接信息。"""
        cfg = load_config()
        _enabled, port, token = ensure_bridge_config(cfg)
        st = self.app.bridge_status() if self.app is not None else {}
        if st.get("port"):
            port = st["port"]
        return conn_line(port or DEFAULT_BRIDGE_PORT, token), token

    def _bridge_summary(self):
        """设置页那一行摘要：没开就写未启用，开了就写地址。"""
        cfg = load_config()
        enabled, port, _token = ensure_bridge_config(cfg)
        if not enabled:
            return tr("set_bridge_off")
        st = self.app.bridge_status() if self.app is not None else {}
        return tr("set_bridge_status_on", port=st.get("port") or port)

    def _open_bridge(self):
        """打开浏览器扩展的卡片弹层（与「AI 服务」同一套风格）。"""
        dlg = _BridgeDialog(self, self.app)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._bridge_lbl.setText(self._bridge_summary())

    def _sync_close_tray_ui(self, *_args):
        """把「关闭窗口行为」的当前取值显示在开关左边。"""
        on = self._close_tray_cb.isChecked()
        self._close_tray_state.setText(
            tr("set_close_tray_on") if on else tr("set_close_tray_off"))
        self._close_tray_state.setStyleSheet(
            f"color: {C['ACCENT'] if on else C['TEXT_SEC']};"
            f" font-size: 11px;")

    def _ai_summary(self):
        """设置页里那行摘要：当前服务商 / 模型 / Key 状态。"""
        values = self._ai_values or {}
        model = model_display_name(values)
        if not model:
            return tr("set_ai_unset")
        key_state = (tr("set_ai_key_saved") if values.get("api_key_saved")
                     else tr("set_ai_key_missing"))
        return tr("set_ai_summary",
                  provider=provider_label(values.get("provider"), LANG),
                  model=model, state=key_state)

    def _open_ai(self):
        """打开 AI 服务卡片弹层（与「历史保留策略」同一套风格）。"""
        dlg = _AISettingsDialog(self, self.app, self._ai_values)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._ai_values = dlg.values()
            self._ai_lbl.setText(self._ai_summary())

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
            cfg["snd_copy"] = self._snd_cbs["copy"].isChecked()
            cfg["snd_paste"] = self._snd_cbs["paste"].isChecked()
            cfg["snd_copy_src"] = self._snd_src.get("copy", SOUND_SRC_SYSTEM)
            cfg["snd_paste_src"] = self._snd_src.get("paste", SOUND_SRC_SYSTEM)
            cfg["snd_copy_file"] = self._snd_file.get("copy", "") or ""
            cfg["snd_paste_file"] = self._snd_file.get("paste", "") or ""
            cfg["takeover_winv"] = self._winv_cb.isChecked()
            cfg["temporary_session"] = self._session_cb.isChecked()
            cfg["close_to_tray"] = self._close_tray_cb.isChecked()
            for k, v in self._hotkey_values.items():
                if k != "hotkey":
                    cfg[k] = v
            save_config(cfg)
            self.app.set_temporary_session(self._session_cb.isChecked())
            self.accept()
            self.app.apply_settings(self._lang_sel, self._auto_cb.isChecked(),
                                    self._theme_sel, bg_changed,
                                    force_restart=bool(getattr(
                                        self, "_custom_skin_dirty", False)))
            try:
                self.app._apply_winv_takeover()
                self.app._apply_paste_sound_hook()
            except Exception:
                pass
        except Exception:
            import traceback as _tb
            _tb.print_exc()
            try:
                self.accept()
            except Exception:
                pass

    # ---- 3.1.0：提示音 / Win+V 相关控件 ----

    def _snd_button_text(self, kind):
        return sound_source_label(kind, self._snd_src.get(kind),
                                  self._snd_file.get(kind, ""))

    def _refresh_snd_button(self, kind):
        btn = self._snd_btns.get(kind)
        if btn is not None:
            btn.setText(self._snd_button_text(kind))

    def _pick_sound_source(self, kind):
        """选择该音效的来源：YouBoard 音效 / 自定义文件（不再提供系统默认音效）。"""
        menu = _RoundMenu(self)
        act_builtin = menu.addAction(tr("set_sound_builtin"))
        act_custom = menu.addAction(tr("set_sound_pick"))
        for act, src in ((act_builtin, SOUND_SRC_BUILTIN),
                         (act_custom, SOUND_SRC_CUSTOM)):
            if self._snd_src.get(kind) == src:
                act.setCheckable(True)
                act.setChecked(True)
        picked = menu.exec(QCursor.pos())
        if picked is act_builtin:
            self._snd_src[kind] = SOUND_SRC_BUILTIN
        elif picked is act_custom:
            path, _ = QFileDialog.getOpenFileName(
                self, tr("set_sound_pick"), "",
                "WAV (*.wav);;Audio (*.wav *.mp3 *.aiff *.ogg);;All (*.*)")
            if not path:
                return
            self._snd_file[kind] = path
            self._snd_src[kind] = SOUND_SRC_CUSTOM
        else:
            return
        self._refresh_snd_button(kind)
        # 选完立即试听一次（不影响开关状态）
        try:
            play_notify_sound(kind, self._snd_src.get(kind),
                              self._snd_file.get(kind, ""))
        except Exception:
            pass

    def _winv_state_text(self):
        state = clipboard_history_enabled()
        if state is True:
            return tr("set_winv_state_on")
        if state is False:
            return tr("set_winv_state_off")
        return tr("set_winv_state_unknown")

    def _on_winv_toggled(self, _state=None):
        # 勾选接管时，顺手刷新一次系统剪贴板历史状态提示
        self._winv_state.setText(self._winv_state_text())
        self._sync_winv_ui()

    def _sync_winv_ui(self):
        """刷新 Win+V 卡片的开关状态文字与按钮可用性（避免"东一个西一个"）。"""
        try:
            on = self._winv_cb.isChecked()
            self._winv_onoff.setText(
                tr("set_winv_on") if on else tr("set_winv_off"))
            self._winv_onoff.setStyleSheet(
                f"color: {C['SUCCESS'] if on else C['TEXT_MUTED']};"
                f" font-size: 11px; font-weight: bold;")
        except Exception:
            pass
        try:
            already_off = clipboard_history_enabled() is False
            self._winv_disable.setEnabled(not already_off)
            self._winv_disable.setText(
                tr("set_winv_already_off") if already_off
                else tr("set_winv_disable"))
        except Exception:
            pass

    def _disable_winv_history(self):
        ok = disable_windows_clipboard_history()
        self._winv_state.setText(self._winv_state_text())
        self._sync_winv_ui()
        _info_card(self, tr("set_winv"),
                   tr("set_winv_done") if ok else tr("set_winv_failed"),
                   kind="latest" if ok else "warning")

    def _use_wallpaper(self):
        # 只读取系统注册表已保存的壁纸文件；不再对壁纸层做 GDI/PrintWindow 强抓取。
        # 那条 _capture_wallpaper() 在硬件加速/壁纸引擎下会把窗口强制重绘，
        # 导致标题栏被"搅坏"（关闭按钮红块左上出现梯形缺口），故此处直接规避。
        p = _get_wallpaper()
        if not p:
            _info_card(self, tr("set_bg"), tr("set_bg_wall_err"),
                       kind="warning")
            return
        self._bg_path = p
        self._bg_lbl.setText(_short_display_name(os.path.basename(p)))

    def _check_update(self):
        """Check GitHub Releases and auto-update by downloading + replacing EXE."""
        import urllib.request
        import json as _json

        html_url = "https://github.com/cloudxys/YouBoard/releases"
        tag = ""
        name = ""
        body = ""
        dl_url = None
        asset_size = 0
        asset_sha256 = ""
        got_info = False

        # 1) 首选 GitHub API：能同时拿到版本号与更新说明正文
        try:
            url = "https://api.github.com/repos/cloudxys/YouBoard/releases/latest"
            req = urllib.request.Request(url, headers={"User-Agent": "YouBoard"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = _json.loads(resp.read().decode())
            tag = data.get("tag_name", "").lstrip("v")
            name = data.get("name", tag)
            body = data.get("body", "") or ""
            html_url = data.get("html_url", html_url)
            for a in data.get("assets", []):
                if a.get("name") == "YouBoard.exe":
                    dl_url = a.get("browser_download_url")
                    # 发布资源自带的体积与摘要：下载后拿它核对，防止装到半截文件
                    try:
                        asset_size = int(a.get("size") or 0)
                    except (TypeError, ValueError):
                        asset_size = 0
                    digest = str(a.get("digest") or "")
                    if digest.startswith("sha256:"):
                        asset_sha256 = digest.split(":", 1)[1].strip()
                    break
            got_info = bool(tag)
        except Exception:
            got_info = False

        # 2) API 被限流 / 被网络拦时的兜底：改用 Releases 页面的跳转拿最新版本号。
        #    未认证的 API 每小时只有 60 次额度（同一出口 IP 共享），外国用户在公司 /
        #    学校 / VPN 网络下很容易撞上限流，这一步保证他们仍能检查到更新。
        if not got_info:
            try:
                req = urllib.request.Request(
                    "https://github.com/cloudxys/YouBoard/releases/latest",
                    headers={"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; "
                                            "Win64; x64) YouBoard-Updater")})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    final_url = resp.geturl() or ""
                m = re.search(r"/tag/v?([0-9]+(?:\.[0-9]+)+)", final_url)
                if m:
                    tag = m.group(1)
                    name = "v" + tag
                    html_url = ("https://github.com/cloudxys/YouBoard/releases/"
                                "tag/v" + tag)
                    # 直接用官方直链：外国网络走 GitHub CDN 即可，国内仍会自动
                    # 在下载环节按速度切换到加速镜像
                    dl_url = ("https://github.com/cloudxys/YouBoard/releases/"
                              "download/v" + tag + "/YouBoard.exe")
                    got_info = True
            except Exception:
                got_info = False

        if not got_info:
            _UpdateStatusDialog(
                self, self.app, APP_VERSION,
                title=tr("upd_title"),
                detail=tr("upd_network_err"),
                kind="warning").exec()
            return

        # Only update if remote version is actually newer
        def _ver_tuple(v):
            try:
                return tuple(int(x) for x in v.split(".")[:3])
            except (ValueError, AttributeError):
                return (0,)
        if not tag or _ver_tuple(tag) <= _ver_tuple(APP_VERSION):
            _UpdateStatusDialog(self, self.app, APP_VERSION).exec()
            return

        if not dl_url:
            import webbrowser
            webbrowser.open(html_url)
            return
        if IS_MAC:
            # macOS 版暂不支持应用内替换可执行文件：直接打开 Releases 页面
            import webbrowser
            webbrowser.open(html_url)
            return
        # Download new EXE with the custom update card.
        self._do_update(dl_url, tag, name, body,
                        expected_size=asset_size,
                        expected_sha256=asset_sha256)

    def _do_update(self, dl_url, new_version, release_name, release_body,
                   expected_size=0, expected_sha256=""):
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
            self._dl_current = current_exe
            sections = _release_notes_sections(release_body)
            dlg = _UpdateDialog(
                self, self.app, APP_VERSION, new_version, release_name,
                sections, urls, tmp_exe,
                expected_size=expected_size,
                expected_sha256=expected_sha256)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                self._finish_update(dlg.downloaded_path, new_version,
                                    dlg.downloaded_sha256)
        except Exception as e:
            _info_card(self, tr("upd_error_title"),
                       tr("upd_replace_failed", err=e), kind="warning")

    def _finish_update(self, tmp_exe, new_version, sha256=""):
        """写"替换 + 重启"脚本：先备份旧主程序，装完校验 SHA256，失败自动回滚。"""
        try:
            current_exe = self._dl_current
            exe_dir = os.path.dirname(current_exe)
            # 进替换流程前再体检一次下载好的包：残次品直接拦下，根本不碰主程序
            ok, reason = verify_update_file(tmp_exe)
            if not ok:
                _info_card(self, tr("upd_error_title"),
                           tr("upd_verify_failed", err=reason), kind="warning")
                return
            if not sha256:
                try:
                    sha256 = sha256_file(tmp_exe)
                except OSError:
                    sha256 = ""
            bat_path = os.path.join(exe_dir, "_update.bat")
            # 换包三步：① 旧主程序改名备份（原子操作，绝不先删）② 新文件搬进来
            # ③ 校验新文件的 SHA256，不对就回滚。任一步失败都不会留下打不开的程序。
            bat_content = f"""@echo off
chcp 65001 >nul 2>&1
rem 延时一律用 ping：timeout.exe 在没有控制台句柄 / 句柄异常的环境里会弹
rem   "timeout.exe - Application Error 0xc0000142" 并且不等待，ping 没这个问题
ping -n 4 127.0.0.1 >nul 2>&1
rem 清掉 PyInstaller onefile 继承来的环境变量再启动新版本。
rem 这套变量指向旧进程的临时目录，而 PyInstaller 6.22.3 起会校验它的名字/归属，
rem 不清掉的话新版本会直接报 "Failed to load Python DLL" 或
rem   Security validation failure: unexpected name of application's home directory!
rem 然后就退出了（用户必须手动重新打开才行）。
set "_PYI_APPLICATION_HOME_DIR="
set "_PYI_ARCHIVE_FILE="
set "_PYI_PARENT_PROCESS_LEVEL="
set "_PYI_SPLASH_IPC="
set "_MEIPASS="
set "_MEIPASS2="
set "_YB_NEW={tmp_exe}"
set "_YB_APP={current_exe}"
set "_YB_BAK={current_exe}.bak"
set "_YB_FLAG={current_exe}.update_failed"
set "_YB_OK={current_exe}.update_ok"
set "_YB_HASH={sha256}"
set /a _yb_try=0
rem 上一次更新被强行打断（主程序被挪走、新的没装进来）时先自愈：
rem 备份还在就先复制回主程序位置，保证目录里始终有一个能启动的程序。
if not exist "%_YB_APP%" if exist "%_YB_BAK%" copy /y "%_YB_BAK%" "%_YB_APP%" >nul 2>&1
rem 第 1 步：把旧主程序改名成 .bak（旧程序还在运行时改不了，退出了就能改）
rem  先清掉上一轮残留的 .bak：否则下面会把"旧备份"当成"这次备份好了"，
rem  于是在主程序还锁着的时候就去替换，最后回滚又找不到备份可用 —— 用户的主程序就没了。
if exist "%_YB_APP%" del /f /q "%_YB_BAK%" >nul 2>&1
:wait_loop
move /y "%_YB_APP%" "%_YB_BAK%" >nul 2>&1
if not exist "%_YB_APP%" goto _yb_install
set /a _yb_try+=1
if %_yb_try% GEQ 60 goto _yb_giveup
ping -n 2 127.0.0.1 >nul 2>&1
goto wait_loop
:_yb_install
rem 第 2 步：新文件搬进主程序位置
move /y "%_YB_NEW%" "%_YB_APP%" >nul 2>&1
if not exist "%_YB_APP%" goto _yb_rollback
rem 第 3 步：逐字节校验装好的文件（大小 + SHA256），和下载时算出来的比对
if "%_YB_HASH%"=="" goto _yb_ok
certutil -hashfile "%_YB_APP%" SHA256 >"%TEMP%\\_yb_hash.txt" 2>nul
findstr /i /c:"%_YB_HASH%" "%TEMP%\\_yb_hash.txt" >nul
if errorlevel 1 goto _yb_rollback
del "%TEMP%\\_yb_hash.txt" >nul 2>&1
:_yb_ok
set "_YB_VERIFIED=1"
for /d %%i in ("%TEMP%\\_MEI*") do rd /s /q "%%i" >nul 2>&1
del /f /q "%_YB_OK%" >nul 2>&1
ping -n 2 127.0.0.1 >nul 2>&1
start "" "%_YB_APP%"
rem 第 4 步：确认新版本真的活着。新版起来后会写一个 .update_ok 标记，
rem 看到它才算成功（PyInstaller 解压慢，最多等 60 秒）；万一标记写不出来
rem （目录只读、杀软拦写等），再用进程名兜底。起来就崩的话直接回滚，
rem 绝不让用户面对一个打不开的软件。
set /a _yb_wait=0
:_yb_live
if exist "%_YB_OK%" goto _yb_done
set /a _yb_wait+=1
if %_yb_wait% GEQ 30 goto _yb_live_fallback
ping -n 2 127.0.0.1 >nul 2>&1
goto _yb_live
:_yb_live_fallback
tasklist /fi "imagename eq YouBoard.exe" 2>nul | findstr /i "YouBoard.exe" >nul
if errorlevel 1 goto _yb_rollback
:_yb_done
rem 更新确实成功了：这时候才能删备份。以前是主程序一启动就删，
rem 它半路崩掉 / 被用户关掉时，回滚那边就没有备份可用了。
del /f /q "%_YB_OK%" >nul 2>&1
del /f /q "%_YB_BAK%" >nul 2>&1
del "%~f0"
exit /b 0
:_yb_rollback
rem 校验没过 / 起来就挂：把备份改回主程序位置，再用旧版本启动（用户只会看到一次提示）。
rem 备份不在就绝不能先删主程序——宁可留着刚装进去的那份，也不能让用户手上什么都没有。
if not exist "%_YB_BAK%" goto _yb_keep
if exist "%_YB_APP%" move /y "%_YB_APP%" "%_YB_APP%.failed" >nul 2>&1
if exist "%_YB_APP%" goto _yb_keep
move /y "%_YB_BAK%" "%_YB_APP%" >nul 2>&1
if not exist "%_YB_APP%" goto _yb_putback
del /f /q "%_YB_APP%.failed" >nul 2>&1
del /f /q "%_YB_OK%" >nul 2>&1
>"%_YB_FLAG%" echo 新版本没通过校验或启动失败，已自动回滚到更新前的版本
start "" "%_YB_APP%"
del "%~f0"
exit /b 0
:_yb_putback
rem 备份搬不回来（极少见）：把刚挪开的那份放回去，至少保证有一个主程序
move /y "%_YB_APP%.failed" "%_YB_APP%" >nul 2>&1
:_yb_keep
del /f /q "%_YB_OK%" >nul 2>&1
>"%_YB_FLAG%" echo 这次没能替换主程序（旧版本备份不可用，已保留现有主程序），建议到 Releases 页面重新下载安装包覆盖安装
rem 校验过的那份才值得启动；没通过校验的残缺文件让用户手动重装，别弹"不是有效应用"的错
if exist "%_YB_APP%" if "%_YB_VERIFIED%"=="1" start "" "%_YB_APP%"
del "%~f0"
exit /b 0
:_yb_giveup
rem 旧程序一直占着文件（极少见）：什么都不动，直接退出，用户下次再试
del "%~f0"
exit /b 0
"""
            with open(bat_path, "w", encoding="utf-8") as f:
                f.write(bat_content)
            import subprocess
            self._splash = _UpdateSplashDialog(self.app, new_version)

            # 交给新版本一份干净的环境（和批处理里的 set 双保险）
            clean_env = {
                k: v for k, v in os.environ.items()
                if not k.startswith("_PYI_") and k not in ("_MEIPASS", "_MEIPASS2")
            }

            def _launch():
                try:
                    subprocess.Popen(["cmd.exe", "/c", bat_path],
                                     # CREATE_NO_WINDOW：让批处理带一个"隐藏的控制台"，
                                     # 它跑 ping 时不会再闪出黑窗口（DETACHED_PROCESS 时
                                     # 没有控制台，子进程会被系统重新分配一个新控制台）
                                     creationflags=0x08000000,
                                     env=clean_env)
                finally:
                    self.app._real_quit()

            self._splash.ready.connect(_launch)
            self._splash.exec()
        except Exception as e:
            _info_card(self, tr("upd_error_title"),
                       tr("upd_replace_failed", err=e), kind="warning")


class CloudSyncDialog(QDialog):
    """云同步独立窗口：Gist / WebDAV 配置 + 手动上传下载（不占用设置页空间）。"""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self._sync_worker = None
        header = _make_frameless_dialog(self, tr("set_sync"))
        self.setFixedSize(520 if LANG == "en" else 500, 630)
        if LOGO_ICO and os.path.exists(LOGO_ICO):
            self.setWindowIcon(QIcon(LOGO_ICO))
        self.setStyleSheet(f"""
            QDialog {{ background-color: {C['DIALOG_BG']};
                border: 1px solid {C['BORDER_LT']}; }}
            QLabel {{ background: transparent; color: {C['TEXT']}; }}
            QLabel#muted {{ color: {C['TEXT_MUTED']}; font-size: 11px; }}
            QComboBox, QLineEdit {{ background: {C['INPUT_BG']}; color: {C['TEXT']};
                border: 1px solid {C['BORDER']}; border-radius: 8px; padding: 7px 9px; font-size: 12px; }}
            QPushButton {{ background: {C['SURFACE2']}; color: {C['TEXT_SEC']};
                border: 1px solid transparent; border-radius: 8px; padding: 7px 14px; font-size: 12px; }}
            QPushButton:hover {{ background: {C['SURFACE3']}; color: {C['TEXT']}; }}
            QPushButton[cssClass="accent"] {{ background: {C['ACCENT']}; color: #fff; border: none; font-weight: bold; }}
            QPushButton[cssClass="accent"]:hover {{ background: {C['ACCENT_HV']}; }}
        """)

        cfg = load_config()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(header)
        body = QWidget()
        root = QVBoxLayout(body)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)
        outer.addWidget(body, 1)

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

        # 多余高度收到下方，避免每行之间被均分出一堆空隙
        root.addStretch(1)

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
        """兜底光标刷新（只刷新按钮自己的手型光标，不再把缩放箭头钉到子控件上）。"""
        _apply_hand_cursor(self)
        _refresh_cursor_under_mouse()

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
# Vault（密库，3.3.1）：用户主动存放的私密数据
# ===========================================================================
def _edit_context_menu(widget, has_selection, read_only=False):
    """输入框的应用风格右键菜单：中文项 + 圆角弹层（替掉 Qt 自带那套英文直角菜单）。"""
    menu = _RoundMenu(widget)
    act_cut = menu.addAction(tr("ctx_cut"))
    act_cut.setEnabled(bool(has_selection) and not read_only)
    act_copy = menu.addAction(tr("ctx_copy"))
    act_copy.setEnabled(bool(has_selection))
    act_paste = menu.addAction(tr("ctx_paste"))
    act_paste.setEnabled(not read_only)
    menu.addSeparator()
    act_all = menu.addAction(tr("m_select_all"))
    chosen = menu.exec(QCursor.pos())
    if chosen is act_cut:
        widget.cut()
    elif chosen is act_copy:
        widget.copy()
    elif chosen is act_paste:
        widget.paste()
    elif chosen is act_all:
        widget.selectAll()


class _VaultLineEdit(QLineEdit):
    """密库用的单行输入框：右键换成应用自己的中文圆角菜单。"""

    def contextMenuEvent(self, event):
        _edit_context_menu(self, self.hasSelectedText(),
                           self.isReadOnly())
        event.accept()


class _VaultTextEdit(QPlainTextEdit):
    """密库用的多行输入框：右键换成应用自己的中文圆角菜单。"""

    def contextMenuEvent(self, event):
        _edit_context_menu(self, self.textCursor().hasSelection(),
                           self.isReadOnly())
        event.accept()


class _VaultEntryDialog(_CardOverlayDialog):
    """添加到密库 / 编辑密库内容：名称可选，留空就按内容显示。

    文本和网址用输入框；图片 / 文件用「选图片…」「选文件…」，选定后用一行摘要显示。
    """

    # 卡片铺在密库窗口里，比默认 620 收窄一点，避免被窗口裁掉
    CARD_WIDTH = 560

    def __init__(self, owner, app, entry=None, kind=None):
        entry = dict(entry or {})
        editing = bool(entry)
        super().__init__(owner, app, "", tr("vault_edit_title") if editing
                         else tr("vault_new_title"), tr("vault_new_sub"))
        try:
            self._icon_lbl.setPixmap(_lock_icon(34, C['TEXT']).pixmap(34, 34))
        except Exception:
            pass
        self._kind = kind or entry.get("type") or "text"
        # 复制内容时要标记 self-copy（密库内容不该被剪贴板监控再收一条）
        self._app = (app or getattr(owner, "app", None)
                     or (owner if hasattr(owner, "store") else None))
        self._image_name = str(entry.get("image") or "")
        # 从历史"加入密库"时带进来的是磁盘上的源文件，保存时才复制进密库目录
        self._image_src = str(entry.get("image_src") or "")
        self._paths = [str(p) for p in (entry.get("paths") or []) if p]
        # 从历史移入时带来的来源信息（时间 / 标签 / 收藏 / 置顶），原样带着走
        self._origin = {"ts": str(entry.get("ts") or ""),
                        "tags": list(entry.get("tags") or []),
                        "fav": bool(entry.get("fav")),
                        "pinned": bool(entry.get("pinned"))}
        lay = self._lay

        def _label(text, top=False):
            lbl = QLabel(text)
            lbl.setStyleSheet(f"color: {C['TEXT_SEC']}; font-size: 12px;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignRight
                             | (Qt.AlignmentFlag.AlignTop if top
                                else Qt.AlignmentFlag.AlignVCenter))
            return lbl

        form = QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)

        self._name_edit = _VaultLineEdit(str(entry.get("name", "") or ""))
        self._name_edit.setPlaceholderText(tr("vault_ph_name"))
        form.addWidget(_label(tr("vault_f_name")), 0, 0)
        form.addWidget(self._name_edit, 0, 1)

        self._content_lbl = _label(tr("vault_f_content"), top=True)
        self._content_edit = _VaultTextEdit()
        self._content_edit.setPlaceholderText(tr("vault_ph_content"))
        self._content_edit.setMinimumHeight(130)
        # 和单行输入框的文字起点对齐：去掉 QPlainTextEdit 自带的 frame / 文档边距
        # （边框和外边距都交给样式表，否则内容会比名称右移几个像素）
        self._content_edit.setFrameShape(QFrame.Shape.NoFrame)
        self._content_edit.document().setDocumentMargin(2)
        self._content_edit.setPlainText(str(entry.get("content", "") or ""))
        form.addWidget(self._content_lbl, 1, 0)
        form.addWidget(self._content_edit, 1, 1)

        self._summary = QLabel("")
        self._summary.setWordWrap(True)
        self._summary.setMinimumHeight(64)
        self._summary.setAlignment(Qt.AlignmentFlag.AlignTop
                                   | Qt.AlignmentFlag.AlignLeft)
        self._summary.setStyleSheet(
            f"QLabel {{ color: {C['TEXT_SEC']}; font-size: 12px;"
            f" background-color: {C['SURFACE2']};"
            f" border: 1px solid {C['BORDER']}; border-radius: 8px;"
            f" padding: 10px; }}")
        form.addWidget(self._summary, 1, 1)
        form.setColumnStretch(1, 1)
        lay.addLayout(form)

        pick = QHBoxLayout()
        self._copy_content_btn = QPushButton(tr("vault_copy_content"))
        self._copy_content_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._copy_content_btn.setAutoDefault(False)
        self._copy_content_btn.clicked.connect(self._copy_content)
        pick.addWidget(self._copy_content_btn)
        pick.addStretch()
        self._pick_img_btn = QPushButton(tr("vault_pick_image"))
        self._pick_file_btn = QPushButton(tr("vault_pick_file"))
        for b in (self._pick_img_btn, self._pick_file_btn):
            b.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            b.setAutoDefault(False)
            pick.addWidget(b)
        self._pick_img_btn.clicked.connect(self._pick_image)
        self._pick_file_btn.clicked.connect(self._pick_file)
        lay.addLayout(pick)

        self._hint = QLabel("")
        self._hint.setStyleSheet(f"color: {C['DANGER']}; font-size: 12px;")
        lay.addWidget(self._hint)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton(tr("btn_cancel"))
        cancel.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        save = QPushButton(tr("btn_save"))
        save.setObjectName("retSave")
        save.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        save.clicked.connect(self.accept)
        buttons.addWidget(save)
        # 名称框里按回车 = 保存
        cancel.setAutoDefault(False)
        cancel.setDefault(False)
        save.setAutoDefault(True)
        save.setDefault(True)
        lay.addLayout(buttons)

        self._sync_kind_ui()
        QTimer.singleShot(0, self._name_edit.setFocus)

    # ---- 类型切换 ----

    def _summary_text(self):
        if self._kind == "image":
            path = self._image_src or vault_image_path({"image": self._image_name})
            name = os.path.basename(path) if path else ""
            return tr("vault_summary_image", name=name or "—")
        names = [os.path.basename(p) for p in self._paths][:4]
        text = tr("vault_summary_file", n=len(self._paths))
        if names:
            text += "\n" + "、".join(names)
        return text

    def _sync_kind_ui(self):
        binary = self._kind in ("image", "file")
        self._content_lbl.setVisible(not binary)
        self._content_edit.setVisible(not binary)
        self._summary.setVisible(binary)
        if binary:
            self._summary.setText(self._summary_text())
        self._pick_img_btn.setVisible(not binary or self._kind == "image"
                                      or self._kind == "file")
        self._pick_file_btn.setVisible(True)

    def _copy_content(self):
        """一键复制正文（长内容不用手动全选）：复制出去不会被历史再收一条。"""
        body = self._content_edit.toPlainText()
        if not body:
            return
        app = getattr(self, "_app", None)
        try:
            if app is not None:
                app._last_self_copy = time.time()
                app.store.mark_self_copy()
        except Exception:
            pass
        try:
            set_clipboard_text(body)
        except Exception as ex:
            self._hint.setStyleSheet(f"color: {C['DANGER']}; font-size: 12px;")
            self._hint.setText(str(ex))
            return
        self._hint.setStyleSheet(f"color: {C['SUCCESS']}; font-size: 12px;")
        self._hint.setText(tr("vault_copied"))

    def _pick_image(self):
        path, _sel = QFileDialog.getOpenFileName(
            self, tr("vault_pick_image"), "",
            "Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp)")
        if not path:
            return
        self._kind = "image"
        self._image_src = path
        self._paths = []
        self._hint.setText("")
        self._sync_kind_ui()

    def _pick_file(self):
        paths, _sel = QFileDialog.getOpenFileNames(
            self, tr("vault_pick_file"), "")
        if not paths:
            return
        self._kind = "file"
        self._paths = list(paths)
        self._image_src = ""
        self._hint.setText("")
        self._sync_kind_ui()

    # ---- 取值 / 校验 ----

    def values(self):
        vals = {"kind": self._kind,
                "name": self._name_edit.text().strip(),
                "content": self._content_edit.toPlainText(),
                "paths": list(self._paths),
                "image": self._image_name,
                "image_src": self._image_src}
        vals.update(getattr(self, "_origin", {}) or {})
        return vals

    def accept(self):
        vals = self.values()
        has_content = bool(vals["content"].strip() or vals["paths"]
                           or vals["image_src"] or vals["image"])
        if not vals["name"] and not has_content:
            self._hint.setStyleSheet(f"color: {C['DANGER']}; font-size: 12px;")
            self._hint.setText(tr("vault_need_content"))
            return
        if self._kind in ("text", "url") and not vals["content"].strip():
            self._hint.setStyleSheet(f"color: {C['DANGER']}; font-size: 12px;")
            self._hint.setText(tr("vault_need_content"))
            return
        super().accept()


class _VaultNameDialog(_CardOverlayDialog):
    """只改名称（留空 = 回到按内容显示）。"""

    CARD_WIDTH = 460

    def __init__(self, owner, app, current=""):
        super().__init__(owner, app, "", tr("vault_rename_title"),
                         tr("vault_rename_sub"))
        try:
            self._icon_lbl.setPixmap(_lock_icon(34, C['TEXT']).pixmap(34, 34))
        except Exception:
            pass
        self._edit = _VaultLineEdit(str(current or ""))
        self._edit.setPlaceholderText(tr("vault_ph_name"))
        self._lay.addWidget(self._edit)
        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton(tr("btn_cancel"))
        cancel.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        save = QPushButton(tr("btn_save"))
        save.setObjectName("retSave")
        save.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        save.clicked.connect(self.accept)
        buttons.addWidget(save)
        cancel.setAutoDefault(False)
        save.setAutoDefault(True)
        save.setDefault(True)
        self._lay.addLayout(buttons)
        QTimer.singleShot(0, self._edit.setFocus)

    def name(self):
        return self._edit.text().strip()


def _vault_add_from_values(vault, vals):
    """把弹层返回的值写进密库（图片先复制进密库自己的目录）。返回新条目或 None。"""
    kind = str(vals.get("kind") or "text")
    name = str(vals.get("name") or "")
    # "从历史移入"带过来的来源信息：移出密库时要靠它回到原来的时间和位置
    extra = {"ts": str(vals.get("ts") or ""),
             "tags": list(vals.get("tags") or []),
             "fav": bool(vals.get("fav")),
             "pinned": bool(vals.get("pinned"))}
    if kind == "image":
        src = str(vals.get("image_src") or "")
        image = vault.store_image_file(src) if src else str(vals.get("image") or "")
        if not image:
            return None
        return vault.add(kind="image", image=image, name=name, **extra)
    if kind == "file":
        paths = [p for p in (vals.get("paths") or []) if p]
        if not paths:
            return None
        return vault.add(kind="file", paths=paths, name=name, **extra)
    content = str(vals.get("content") or "")
    if not content.strip():
        return None
    if kind == "url":
        return vault.add(kind="url", content=content, name=name, **extra)
    return vault.add(kind="text", content=content, name=name, **extra)


class _VaultPasswordDialog(_CardOverlayDialog):
    """设置 / 修改密库的主密码（卡片式弹层，和其它弹层同一套风格）。

    新密码留空 = 取消主密码（改回本机 youboard.key 加密）。
    """

    CARD_WIDTH = 520

    def __init__(self, owner, app, protected=False):
        super().__init__(owner, app, "🔒",
                         tr("vault_pw_change_title") if protected
                         else tr("vault_pw_set_title"),
                         tr("vault_pw_change_sub") if protected
                         else tr("vault_pw_set_sub"))
        self._icon_lbl.setPixmap(_vault_lock_pixmap(44))
        self._protected = bool(protected)
        self._result = ("", "")
        lay = self._lay
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)

        def add_row(row, text, widget):
            lbl = QLabel(text)
            lbl.setObjectName("retSub")
            grid.addWidget(lbl, row, 0,
                           Qt.AlignmentFlag.AlignLeft |
                           Qt.AlignmentFlag.AlignVCenter)
            grid.addWidget(widget, row, 1)
            return widget

        self._now = QLineEdit()
        self._now.setEchoMode(QLineEdit.EchoMode.Password)
        self._now.setPlaceholderText(tr("vault_pw_ph"))
        self._new = QLineEdit()
        self._new.setEchoMode(QLineEdit.EchoMode.Password)
        self._new.setPlaceholderText(tr("vault_f_pw_new"))
        self._again = QLineEdit()
        self._again.setEchoMode(QLineEdit.EchoMode.Password)
        self._again.setPlaceholderText(tr("vault_f_pw_again"))
        for _ed in (self._now, self._new, self._again):
            _attach_password_toggle(_ed)
        row = 0
        if self._protected:
            add_row(row, tr("vault_f_pw_now"), self._now)
            row += 1
        add_row(row, tr("vault_f_pw_new"), self._new)
        row += 1
        add_row(row, tr("vault_f_pw_again"), self._again)
        lay.addLayout(grid)

        self._err = QLabel("")
        self._err.setObjectName("retNote")
        self._err.setWordWrap(True)
        lay.addWidget(self._err)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton(tr("btn_cancel"))
        cancel.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        save = QPushButton(tr("btn_save"))
        save.setObjectName("retSave")
        save.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        save.clicked.connect(self._save)
        buttons.addWidget(save)
        lay.addLayout(buttons)

        target = self._now if self._protected else self._new
        QTimer.singleShot(0, target.setFocus)
        self._new.returnPressed.connect(self._save)
        self._again.returnPressed.connect(self._save)
        self._now.returnPressed.connect(self._save)

    def values(self):
        """(当前主密码, 新主密码)；新主密码为空表示取消主密码。"""
        return self._result

    def _save(self):
        now = self._now.text() if self._protected else ""
        new = self._new.text()
        again = self._again.text()
        if new or again:
            if len(new) < 6:
                self._err.setText(tr("vault_pw_len"))
                return
            if new != again:
                self._err.setText(tr("vault_pw_mismatch"))
                return
        elif self._protected:
            # 留空 = 取消主密码：仍然要验证当前密码（由调用方校验）
            new = ""
        if self._protected and not now:
            self._err.setText(tr("vault_pw_wrong"))
            return
        self._result = (now, new)
        self.accept()


class _VaultUnlockDialog(_CardOverlayDialog):
    """密库锁着时就地输入主密码（从主界面"加入密库"前用）。"""

    CARD_WIDTH = 480

    def __init__(self, owner, app, vault):
        super().__init__(owner, app, "🔒", tr("vault_locked_title"),
                         tr("vault_locked_sub"))
        self._icon_lbl.setPixmap(_vault_lock_pixmap(44))
        self._vault = vault
        lay = self._lay
        self._edit = QLineEdit()
        self._edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._edit.setPlaceholderText(tr("vault_pw_ph"))
        self._edit.returnPressed.connect(self._try)
        _attach_password_toggle(self._edit)
        lay.addWidget(self._edit)
        self._msg = QLabel("")
        self._msg.setObjectName("retNote")
        self._msg.setWordWrap(True)
        lay.addWidget(self._msg)
        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton(tr("btn_cancel"))
        cancel.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        ok = QPushButton(tr("vault_unlock"))
        ok.setObjectName("retSave")
        ok.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        ok.clicked.connect(self._try)
        buttons.addWidget(ok)
        lay.addLayout(buttons)
        QTimer.singleShot(0, self._edit.setFocus)

    def _try(self):
        if self._vault.unlock(self._edit.text()):
            self.accept()
            return
        self._msg.setText(tr("vault_pw_wrong"))
        self._msg.setStyleSheet(f"color: {C['DANGER']};")
        self._edit.selectAll()
        self._edit.setFocus()


class VaultDialog(QDialog):
    """密库独立窗口：用户主动存放的私密内容（文本 / 图片 / 文件 / 网址）。

    - 名称可选：不填就直接按内容显示（长内容自动截断显示）；
    - 分类胶囊和主界面一样（全部 / 文本 / 图片 / 文件 / 网址）；
    - 独立文件 youboard_vault.json（Fernet 加密 + 原子写入），图片复制进 vault_files/；
    - 复制走 mark_self_copy()，内容不会被剪贴板监控记进历史；
    - 不参与手机传输 / 云同步 / 历史快照。
    """

    KINDS = ("all", "text", "image", "file", "url")

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.vault = getattr(app, "vault", None) or VaultStore()
        # 弹层铺满本窗口时把桌面小组件抬回最上层（同「编辑标签」一套处理）
        self._desk_widget = getattr(app, "_desk_widget", None)
        header = _make_frameless_dialog(self, tr("vault_title"))
        self.setFixedSize(700 if LANG == "en" else 680, 580)
        _suo_ico = _res_icon("suo.ico")
        if os.path.exists(_suo_ico):
            self.setWindowIcon(QIcon(_suo_ico))
        elif LOGO_ICO and os.path.exists(LOGO_ICO):
            self.setWindowIcon(QIcon(LOGO_ICO))
        self.setStyleSheet(f"""
            QDialog {{ background-color: {C['DIALOG_BG']};
                border: 1px solid {C['BORDER_LT']}; }}
            QLabel {{ background: transparent; color: {C['TEXT']}; }}
            QLabel#muted {{ color: {C['TEXT_MUTED']}; font-size: 11px; }}
            QLineEdit {{ background: {C['INPUT_BG']}; color: {C['TEXT']};
                border: 1px solid {C['BORDER']}; border-radius: 8px;
                padding: 7px 9px; font-size: 12px; }}
            QPushButton {{ background: {C['SURFACE2']}; color: {C['TEXT_SEC']};
                border: 1px solid transparent; border-radius: 8px;
                padding: 7px 14px; font-size: 12px; }}
            QPushButton:hover {{ background: {C['SURFACE3']}; color: {C['TEXT']}; }}
            QPushButton:disabled {{ color: {C['TEXT_MUTED']}; }}
            QPushButton[cssClass="accent"] {{ background: {C['ACCENT']};
                color: #071116; border: none; font-weight: 700; }}
            QPushButton[cssClass="accent"]:hover {{ background: {C['ACCENT_HV']}; }}
            QTableWidget {{ background: {C['SURFACE']}; color: {C['TEXT']};
                border: 1px solid {C['BORDER']}; border-radius: 10px;
                gridline-color: transparent; font-size: 12px;
                outline: 0px; }}
            QTableWidget::item {{ padding: 6px 8px; border: none; }}
            QTableWidget::item:selected {{ background: {C['SURFACE3']};
                color: {C['TEXT']}; }}
            QHeaderView::section {{ background: {C['SURFACE2']};
                color: {C['TEXT_SEC']}; border: none; padding: 6px 8px;
                font-size: 11px; }}
        """)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(header)
        body = QWidget()
        root = QVBoxLayout(body)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)
        # 设了主密码就先用"锁屏页"挡住内容（解锁后才切到列表页）
        self._content_page = body
        self._lock_page = self._build_lock_page()
        self._stack = QStackedWidget()
        self._stack.addWidget(self._content_page)
        self._stack.addWidget(self._lock_page)
        outer.addWidget(self._stack, 1)

        desc = QLabel(tr("vault_sub"))
        desc.setObjectName("muted")
        desc.setWordWrap(True)
        root.addWidget(desc)

        # 分类胶囊：和主界面顶部一样的一排（带各自数量）
        self._kind_btns = {}
        kinds_row = QHBoxLayout()
        kinds_row.setSpacing(6)
        for key in self.KINDS:
            chip = QPushButton(tr("vault_kind_" + key))
            chip.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            chip.setCheckable(True)
            chip.clicked.connect(lambda _c=False, k=key: self._set_kind(k))
            kinds_row.addWidget(chip)
            self._kind_btns[key] = chip
        kinds_row.addStretch(1)
        root.addLayout(kinds_row)

        top = QHBoxLayout()
        self._search = _VaultLineEdit()
        self._search.setPlaceholderText(tr("vault_search_ph"))
        self._search.textChanged.connect(lambda _t: self._reload())
        top.addWidget(self._search, 1)
        add_btn = QPushButton(tr("vault_add"))
        add_btn.setProperty("cssClass", "accent")
        add_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        add_btn.clicked.connect(self._add_entry)
        top.addWidget(add_btn)
        root.addLayout(top)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(
            [tr("vault_col_name"), tr("vault_col_type"), tr("vault_col_time")])
        self._table.verticalHeader().setVisible(False)
        self._table.setShowGrid(False)
        self._table.setAlternatingRowColors(False)
        self._table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setWordWrap(False)
        # 点一下不要冒出焦点框（那圈虚线框就是用户说的"透明框"）
        self._table.setItemDelegate(_NoFocusDelegate(self._table))
        self._table.itemSelectionChanged.connect(self._sync_buttons)
        self._table.itemDoubleClicked.connect(lambda _i: self._activate_entry())
        # 右键菜单：和主界面历史列表一套（只是「加入密库」换成「移出密库」）
        self._table.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_right_click)
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        root.addWidget(self._table, 1)

        btns = QHBoxLayout()
        btns.setSpacing(8)
        self._copy_btn = QPushButton(tr("vault_copy"))
        self._open_btn = QPushButton(tr("vault_open"))
        self._edit_btn = QPushButton(tr("vault_edit"))
        self._rename_btn = QPushButton(tr("vault_rename"))
        self._del_btn = QPushButton(tr("vault_delete"))
        for b in (self._copy_btn, self._open_btn, self._edit_btn,
                  self._rename_btn, self._del_btn):
            b.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btns.addWidget(b)
        btns.addStretch()
        # 主密码：设置 / 修改 + 立即锁定（放在右下角，和内容操作分开）
        self._pw_btn = QPushButton(tr("vault_pw_btn"))
        self._lock_btn = QPushButton(tr("vault_lock_now"))
        # 「关闭密码」：用主密码打开之后，一键退回"直接就能打开"的状态（随时能再设回来）
        self._off_btn = QPushButton(tr("vault_pw_off"))
        for b in (self._pw_btn, self._off_btn, self._lock_btn):
            b.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btns.addWidget(b)
        self._pw_btn.clicked.connect(self._edit_master_password)
        self._off_btn.clicked.connect(self._disable_master_password)
        self._lock_btn.clicked.connect(self._lock_now)
        self._copy_btn.clicked.connect(self._copy_entry)
        self._open_btn.clicked.connect(self._open_entry)
        self._edit_btn.clicked.connect(self._edit_entry)
        self._rename_btn.clicked.connect(self._rename_entry)
        self._del_btn.clicked.connect(self._delete_entry)
        root.addLayout(btns)

        self._status = QLabel("")
        self._status.setObjectName("muted")
        root.addWidget(self._status)

        self._kind = "all"
        self._rows = []
        # 闲置自动锁定：设了主密码时，5 分钟没动过这个窗口就自己锁上
        self._lock_timer = QTimer(self)
        self._lock_timer.setSingleShot(True)
        self._lock_timer.timeout.connect(self._lock_now)
        _app_inst = QApplication.instance()
        if _app_inst is not None:
            _app_inst.installEventFilter(self)
        self._reload()
        self._refresh_lock_state()

    # ---- 主密码 / 锁屏 ----

    VAULT_IDLE_LOCK_MS = 5 * 60 * 1000

    def _build_lock_page(self):
        """锁屏页：设了主密码又没解锁时，密库内容一点都不露。"""
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(40, 24, 40, 24)
        lay.setSpacing(10)
        lay.addStretch(1)
        icon_row = QHBoxLayout()
        icon_row.addStretch(1)
        icon = QLabel()
        icon.setPixmap(_vault_lock_pixmap(64))
        icon.setFixedSize(72, 72)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet("background: transparent;")
        icon_row.addWidget(icon)
        icon_row.addStretch(1)
        lay.addLayout(icon_row)
        title = QLabel(tr("vault_locked_title"))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(
            f"color: {C['TEXT']}; font-size: 17px; font-weight: 700;"
            " background: transparent;")
        lay.addWidget(title)
        sub = QLabel(tr("vault_locked_sub"))
        sub.setObjectName("muted")
        sub.setWordWrap(True)
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(sub)
        row = QHBoxLayout()
        row.addStretch(1)
        self._unlock_edit = QLineEdit()
        self._unlock_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._unlock_edit.setPlaceholderText(tr("vault_pw_ph"))
        self._unlock_edit.setFixedWidth(240)
        self._unlock_edit.returnPressed.connect(self._try_unlock)
        _attach_password_toggle(self._unlock_edit)
        row.addWidget(self._unlock_edit)
        unlock_btn = QPushButton(tr("vault_unlock"))
        unlock_btn.setProperty("cssClass", "accent")
        unlock_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        unlock_btn.clicked.connect(self._try_unlock)
        row.addWidget(unlock_btn)
        row.addStretch(1)
        lay.addLayout(row)
        self._unlock_msg = QLabel("")
        self._unlock_msg.setWordWrap(True)
        self._unlock_msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._unlock_msg)
        forget = QLabel(tr("vault_pw_forget"))
        forget.setObjectName("muted")
        forget.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(forget)
        wipe_btn = QPushButton(tr("vault_pw_wipe"))
        wipe_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        wipe_btn.clicked.connect(self._wipe_vault)
        frow = QHBoxLayout()
        frow.addStretch(1)
        frow.addWidget(wipe_btn)
        frow.addStretch(1)
        lay.addLayout(frow)
        lay.addStretch(1)
        return page

    def _locked_now(self):
        return bool(self.vault is not None and self.vault.is_locked())

    def _try_unlock(self):
        text = self._unlock_edit.text()
        if not text:
            return
        if self.vault.unlock(text):
            self._unlock_edit.clear()
            self._unlock_msg.setText("")
            self._refresh_lock_state()
            self._reload()
        else:
            self._unlock_msg.setText(tr("vault_pw_wrong"))
            self._unlock_msg.setStyleSheet(
                f"color: {C['DANGER']}; font-size: 12px;"
                " background: transparent;")
            self._unlock_edit.selectAll()
            self._unlock_edit.setFocus()

    def _wipe_vault(self):
        if not _confirm_card(self, tr("vault_pw_forget"),
                             tr("vault_pw_forget_sub"),
                             ok_text=tr("vault_pw_wipe")):
            return
        self.vault.wipe()
        self._search.clear()
        self._unlock_edit.clear()
        self._unlock_msg.setText("")
        self._refresh_lock_state()
        self._reload()
        self._status.setText(tr("vault_wiped"))

    def _lock_now(self):
        """立刻锁定（也可以由闲置计时器触发）。"""
        if not self.vault.is_protected():
            return
        self.vault.lock()
        self._refresh_lock_state()

    def _disable_master_password(self):
        """关闭主密码：以后打开密库直接就能看（随时可以再设回来）。"""
        if not self.vault.is_protected() or self.vault.is_locked():
            return
        if not _confirm_card(self, tr("vault_pw_off_confirm"),
                             tr("vault_pw_off_sub"),
                             ok_text=tr("vault_pw_off")):
            return
        if self.vault.disable_master_password():
            self._refresh_lock_state()
            self._status.setText(tr("vault_pw_off_done"))
        else:
            _info_card(self, tr("vault_pw_off_confirm"),
                       tr("vault_pw_wrong"), kind="warning")

    def _refresh_lock_state(self):
        locked = self._locked_now()
        self._stack.setCurrentWidget(
            self._lock_page if locked else self._content_page)
        protected = bool(self.vault.is_protected())
        self._pw_btn.setText(tr("vault_pw_change_title") if protected
                             else tr("vault_pw_set_title"))
        self._off_btn.setVisible(protected and not locked)
        self._lock_btn.setVisible(protected and not locked)
        if locked:
            self._lock_timer.stop()
            QTimer.singleShot(0, self._unlock_edit.setFocus)
        else:
            self._restart_lock_timer()

    def _restart_lock_timer(self):
        if self.vault.is_protected() and not self.vault.is_locked():
            self._lock_timer.start(self.VAULT_IDLE_LOCK_MS)

    def _edit_master_password(self):
        """设置 / 修改 / 取消主密码。"""
        protected = bool(self.vault.is_protected())
        dlg = _VaultPasswordDialog(self, self.app, protected)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        old_pw, new_pw = dlg.values()
        if protected:
            ok = self.vault.change_master_password(old_pw, new_pw)
        else:
            ok = self.vault.set_master_password(new_pw)
        if not ok:
            msg = (tr("vault_pw_wrong") if self.vault.wrong_password()
                   else tr("vault_pw_len"))
            _info_card(self, tr("vault_pw_set_title"), msg, kind="warning")
            return
        self._refresh_lock_state()
        self._status.setText(tr("vault_pw_removed") if not new_pw
                             else (tr("vault_pw_change_ok") if protected
                                   else tr("vault_pw_set_ok")))

    def eventFilter(self, obj, event):
        """窗口里任何输入都算"还在用"，把闲置锁定倒计时往后推。"""
        try:
            if event.type() in (QEvent.Type.MouseButtonPress,
                                QEvent.Type.KeyPress, QEvent.Type.Wheel):
                widget = obj if isinstance(obj, QWidget) else None
                while widget is not None:
                    if widget is self:
                        self._restart_lock_timer()
                        break
                    widget = widget.parentWidget()
        except Exception:
            pass
        return super().eventFilter(obj, event)

    def closeEvent(self, event):
        self._release_vault()
        super().closeEvent(event)

    def done(self, result):
        """accept / reject（含 Esc）也会走到这里：收尾 + 锁定。"""
        self._release_vault()
        super().done(result)

    def _release_vault(self):
        """关窗收尾：卸掉全局事件过滤器，并把密库锁上（下次打开要重新输主密码）。"""
        try:
            _app_inst = QApplication.instance()
            if _app_inst is not None:
                _app_inst.removeEventFilter(self)
        except Exception:
            pass
        try:
            if self.vault.is_protected():
                self.vault.lock()
        except Exception:
            pass

    # ---- 列表 ----

    @staticmethod
    def _fallback_title(entry):
        """没填名称时，列表直接按内容显示。"""
        kind = entry.get("type")
        if kind in ("text", "url"):
            text = " ".join(str(entry.get("content", "")).split())
            return text[:90] + ("…" if len(text) > 90 else "")
        if kind == "image":
            name = os.path.basename(vault_image_path(entry))
            return tr("vault_summary_image", name=name or "—")
        paths = entry.get("paths") or []
        first = os.path.basename(paths[0]) if paths else "—"
        if len(paths) > 1:
            first += "（+%d）" % (len(paths) - 1)
        return first

    def _set_kind(self, kind):
        self._kind = kind if kind in self.KINDS else "all"
        self._reload()

    def _paint_kinds(self, counts):
        for key, chip in self._kind_btns.items():
            n = counts.get(key, 0)
            chip.setText("%s %d" % (tr("vault_kind_" + key), n))
            chip.setChecked(key == self._kind)
            if key == self._kind:
                chip.setStyleSheet(
                    f"QPushButton {{ background: {C['ACCENT_DIM']};"
                    f" color: {C['ACCENT']}; border: 1px solid {C['ACCENT']};"
                    f" border-radius: 11px; padding: 3px 12px;"
                    f" font-size: 11px; font-weight: 600; }}")
            else:
                chip.setStyleSheet(
                    f"QPushButton {{ background: {C['SURFACE2']};"
                    f" color: {C['TEXT_SEC']}; border: 1px solid {C['BORDER']};"
                    f" border-radius: 11px; padding: 3px 12px;"
                    f" font-size: 11px; }}"
                    f"QPushButton:hover {{ background: {C['SURFACE3']};"
                    f" color: {C['TEXT']}; }}")

    def _reload(self):
        rows = self.vault.search(self._search.text(), self._kind)
        self._rows = rows
        self._table.setRowCount(len(rows))
        for i, entry in enumerate(rows):
            title = entry.get("name") or self._fallback_title(entry)
            kind_label = tr("vault_kind_" + str(entry.get("type") or "text"))
            stamp = str(entry.get("updated") or "").replace("T", " ")[:19]
            for col, text in enumerate((title, kind_label, stamp)):
                item = QTableWidgetItem(str(text))
                if col == 1:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._table.setItem(i, col, item)
        if rows:
            self._table.selectRow(0)
        self._paint_kinds(self.vault.counts())
        total = self.vault.count()
        searching = bool(self._search.text().strip())
        if total == 0:
            self._status.setText(tr("vault_empty"))
        elif self._kind != "all":
            # 以前的 "网址 1 / 5" 会被读成"有 5 个网址"，其实 5 是全部条数
            self._status.setText(tr("vault_count_kind",
                                    kind=tr("vault_kind_" + self._kind),
                                    n=len(rows), total=total))
        elif searching:
            self._status.setText(tr("vault_count_search",
                                    n=len(rows), total=total))
        else:
            self._status.setText(tr("vault_count", n=total))
        self._sync_buttons()

    def _current_entry(self):
        row = self._table.currentRow()
        if row < 0 or row >= len(self._rows):
            return None
        return self._rows[row]

    def _sync_buttons(self):
        entry = self._current_entry()
        kind = (entry or {}).get("type")
        has = entry is not None
        self._copy_btn.setEnabled(has)
        self._rename_btn.setEnabled(has)
        self._del_btn.setEnabled(has)
        self._open_btn.setEnabled(has and kind in ("image", "file", "url"))
        self._edit_btn.setEnabled(has and kind in ("text", "url"))

    # ---- 操作 ----

    def _add_entry(self):
        dlg = _VaultEntryDialog(self, None)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        if _vault_add_from_values(self.vault, dlg.values()) is None:
            return
        self._search.clear()
        self._reload()
        self._status.setText(tr("vault_saved"))

    def _edit_entry(self):
        entry = self._current_entry()
        if entry is None or entry.get("type") not in ("text", "url"):
            return
        dlg = _VaultEntryDialog(self, None, entry)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        vals = dlg.values()
        if self.vault.update(entry["id"], content=vals["content"],
                             name=vals["name"]):
            self._reload()
            self._status.setText(tr("vault_saved"))

    def _rename_entry(self):
        entry = self._current_entry()
        if entry is None:
            return
        dlg = _VaultNameDialog(self, None, entry.get("name") or "")
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        if self.vault.rename(entry["id"], dlg.name()):
            self._reload()
            self._status.setText(tr("vault_renamed"))

    def _delete_entry(self):
        entry = self._current_entry()
        if entry is None:
            return
        if not _confirm_card(self, tr("vault_confirm_delete"),
                             tr("vault_confirm_delete_sub"),
                             ok_text=tr("vault_delete")):
            return
        if self.vault.delete(entry["id"]):
            self._reload()
            self._status.setText(tr("vault_deleted"))

    def _activate_entry(self):
        entry = self._current_entry()
        if entry is None:
            return
        if entry.get("type") in ("text", "url"):
            self._edit_entry()
        else:
            self._open_entry()

    def _on_right_click(self, pos):
        """密库列表的右键菜单：与历史列表同款，只是「加入密库」换成「移出密库」。"""
        idx = self._table.indexAt(pos)
        if idx.isValid():
            self._table.selectRow(idx.row())
        entry = self._current_entry()
        if entry is None:
            return
        kind = str(entry.get("type") or "text")
        menu = _RoundMenu(self._table)
        if kind in ("text", "url"):
            menu.addAction(tr("m_copy_content"), self._copy_entry)
            menu.addAction(tr("m_export_txt"), self._export_entry)
        elif kind == "image":
            menu.addAction(tr("m_copy_image"), self._copy_entry)
            menu.addAction(tr("m_open_viewer"), self._open_entry)
            menu.addAction(tr("m_open_folder"),
                           lambda: self._reveal_entry(entry))
            menu.addAction(tr("m_copy_path"),
                           lambda: self._copy_entry_path(entry))
        else:
            menu.addAction(tr("m_copy_files"), self._copy_entry)
            menu.addAction(tr("m_open_locate"), self._open_entry)
            menu.addAction(tr("m_open_folder"),
                           lambda: self._reveal_entry(entry))
            menu.addAction(tr("m_copy_paths"),
                           lambda: self._copy_entry_path(entry))
        menu.addSeparator()
        menu.addAction(tr("vault_edit"), self._edit_entry)
        menu.addAction(tr("vault_rename"), self._rename_entry)
        self._ai_menu_for(menu, entry)
        menu.addAction(tr("m_vault_out"), self._move_entry_out)
        menu.addSeparator()
        menu.addAction(tr("vault_delete"), self._delete_entry)
        menu.exec(self._table.viewport().mapToGlobal(pos))

    # ---- AI（与主界面历史列表同一套） ----

    def _vault_proxy_entry(self, entry):
        """把密库条目包装成"历史条目"的形状，让 AI 弹层原样复用。

        图片给绝对路径（密库的图片在 vault_files/ 下），文件给 file_paths，
        文本 / 网址给正文——AI 那边不用关心这条到底在密库还是历史里。
        """
        kind = str(entry.get("type") or "text")
        proxy = {"type": kind, "hash": str(entry.get("id") or ""),
                 "content": str(entry.get("content") or ""),
                 "source_name": str(entry.get("name") or ""),
                 "timestamp": str(entry.get("ts") or entry.get("created") or "")}
        if kind == "image":
            proxy["filename"] = vault_image_path(entry)
        elif kind == "file":
            paths = [p for p in (entry.get("paths") or []) if p]
            proxy["file_paths"] = paths
            proxy["file_sizes"] = [
                os.path.getsize(p) if os.path.exists(p) else -1 for p in paths]
        return proxy

    def _ai_menu_for(self, menu, entry):
        """「AI 处理 ▸」子菜单：动作和历史列表完全一致。"""
        kind = str(entry.get("type") or "text")
        sub = _RoundMenu(menu)
        sub.setTitle(tr("ai_menu"))
        for act in YouBoardApp._ai_actions_for(kind):
            if act == "custom":
                continue
            sub.addAction(tr("ai_act_" + act),
                          lambda a=act: self._run_ai(a))
        sub.addSeparator()
        sub.addAction(tr("ai_act_custom"), self._run_ai_custom)
        sub.addAction(tr("ai_menu_chat"), self._run_ai_chat)
        menu.addMenu(sub)
        return sub

    def _open_ai(self, action, custom_prompt="", chat=False):
        entry = self._current_entry()
        if entry is None:
            return
        dlg = _AIDialog(self, self.app, self._vault_proxy_entry(entry), action,
                        custom_prompt=custom_prompt, chat=chat,
                        vault=self.vault,
                        vault_uid=str(entry.get("id") or ""),
                        on_changed=self._reload)
        dlg.exec()

    def _run_ai(self, action, custom_prompt=""):
        self._open_ai(action, custom_prompt=custom_prompt)

    def _run_ai_custom(self):
        entry = self._current_entry()
        if entry is None:
            return
        ask = _AIPromptDialog(self, self.app, self._vault_proxy_entry(entry))
        if ask.exec() != QDialog.DialogCode.Accepted:
            return
        prompt = ask.prompt_text()
        if prompt:
            self._open_ai("custom", custom_prompt=prompt)

    def _run_ai_chat(self):
        self._open_ai("summarize", chat=True)

    def _copy_entry_path(self, entry):
        """复制图片 / 文件在磁盘上的路径（和历史列表里的行为一致）。"""
        if str(entry.get("type")) == "image":
            path = vault_image_path(entry)
        else:
            paths = [p for p in (entry.get("paths") or []) if p]
            path = paths[0] if paths else ""
        if not path:
            self._status.setText(tr("vault_no_file"))
            return
        try:
            set_clipboard_text(path)
        except Exception:
            return
        self._status.setText(tr("vault_copied"))

    def _reveal_entry(self, entry):
        """在资源管理器里定位这个文件。"""
        if str(entry.get("type")) == "image":
            path = vault_image_path(entry)
        else:
            paths = [p for p in (entry.get("paths") or []) if p]
            path = paths[0] if paths else ""
        if not path or not os.path.exists(path):
            self._status.setText(tr("vault_no_file"))
            return
        try:
            self.app._reveal_in_explorer(path)
        except Exception:
            pass

    def _export_entry(self):
        """导出文本 / 网址（和历史列表的「导出为 .txt…」一致）。"""
        entry = self._current_entry()
        if entry is None:
            return
        body = str(entry.get("content") or "")
        if not body:
            return
        default = (entry.get("name") or "YouBoard").strip() or "YouBoard"
        for ch in '\\/:*?"<>|':
            default = default.replace(ch, "_")
        path, _sel = QFileDialog.getSaveFileName(
            self, tr("m_export_txt"), default + ".txt",
            "Text (*.txt)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(body)
        except OSError as ex:
            self._status.setText(str(ex))
            return
        self._status.setText(tr("st_exported", name=os.path.basename(path)))

    def _move_entry_out(self):
        """移出密库：把这条放回剪贴板历史，时间仍是当初复制的时间。"""
        entry = self._current_entry()
        if entry is None:
            return
        item = self._to_history_entry(entry)
        if item is None:
            self._status.setText(tr("vault_no_file"))
            return
        store = getattr(self.app, "store", None)
        if store is None:
            return
        try:
            if not store.insert_entry(item, pinned=bool(entry.get("pinned"))):
                return
        except Exception as ex:
            self._status.setText(tr("vault_move_out_failed", err=str(ex)[:80]))
            return
        self.vault.delete(entry["id"])
        self._reload()
        self._status.setText(tr("vault_move_out_done"))
        try:
            self.app._refresh_all()      # 主界面立刻能看到它按原时间归位
            self.app._update_desk_widget()
        except Exception:
            pass

    def _to_history_entry(self, entry):
        """把密库条目还原成一条历史记录（保留原时间 / 标签 / 收藏）。"""
        store = getattr(self.app, "store", None)
        if store is None:
            return None
        kind = str(entry.get("type") or "text")
        ts = str(entry.get("ts") or entry.get("created") or "")
        tags = list(entry.get("tags") or [])
        fav = bool(entry.get("fav"))
        if kind in ("text", "url"):
            content = str(entry.get("content") or "")
            if not content:
                return None
            item = {"hash": store._text_hash(content), "type": kind,
                    "content": content, "timestamp": ts}
        elif kind == "image":
            src = vault_image_path(entry)
            if not (src and os.path.exists(src)):
                return None
            try:
                from PIL import Image as PILImage
                img = PILImage.open(src)
                img.load()
            except Exception:
                return None
            h = store._image_hash(img)
            try:
                store.add_image(img, h, source_name=str(entry.get("name") or ""))
            except Exception:
                return None
            item = {"hash": h, "type": "image",
                    "filename": "images/%s.png" % h,
                    "original_format": img.format or "PNG",
                    "source_name": str(entry.get("name") or ""),
                    "width": img.width, "height": img.height,
                    "timestamp": ts}
        else:
            paths = [p for p in (entry.get("paths") or []) if p]
            if not paths:
                return None
            try:
                sizes = [os.path.getsize(p) if os.path.exists(p) else -1
                         for p in paths]
            except OSError:
                sizes = [-1] * len(paths)
            item = {"hash": store._files_hash(paths), "type": "file",
                    "file_paths": paths, "file_sizes": sizes,
                    "file_count": len(paths), "timestamp": ts}
        # 顺带把来源信息带回去：移出后还是原来那条（时间 / 标签 / 收藏）
        if tags:
            item["tags"] = tags
        if fav:
            item["fav"] = True
        return item

    def _mark_self_copy(self):
        """密库复制同样要把内容挡在剪贴板历史之外。"""
        try:
            self.app._last_self_copy = time.time()
        except Exception:
            pass
        try:
            self.app.store.mark_self_copy()
        except Exception:
            pass

    def _copy_entry(self):
        entry = self._current_entry()
        if entry is None:
            return
        kind = entry.get("type")
        self._mark_self_copy()
        try:
            if kind in ("text", "url"):
                set_clipboard_text(str(entry.get("content", "") or ""))
                msg = tr("vault_copied")
            elif kind == "image":
                path = vault_image_path(entry)
                if not (path and os.path.exists(path)):
                    self._status.setText(tr("vault_no_file"))
                    return
                from PIL import Image as PILImage
                img = PILImage.open(path)
                img.load()
                set_clipboard_image(img)
                msg = tr("vault_copied_image")
            else:
                paths = [p for p in (entry.get("paths") or [])
                         if os.path.exists(p)]
                if not paths:
                    self._status.setText(tr("vault_no_file"))
                    return
                set_clipboard_files(paths)
                msg = tr("vault_copied_files")
        except Exception:
            self._status.setText(tr("vault_no_file"))
            return
        self._status.setText(msg)

    def _open_entry(self):
        entry = self._current_entry()
        if entry is None:
            return
        kind = entry.get("type")
        try:
            if kind == "url":
                self.app._open_url(str(entry.get("content", "") or ""))
            elif kind == "image":
                path = vault_image_path(entry)
                if path and os.path.exists(path):
                    _open_path(path)
                else:
                    self._status.setText(tr("vault_no_file"))
            elif kind == "file":
                paths = [p for p in (entry.get("paths") or [])
                         if os.path.exists(p)]
                if paths:
                    _open_path(paths[0])
                else:
                    self._status.setText(tr("vault_no_file"))
        except Exception:
            self._status.setText(tr("vault_no_file"))

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
def _find_youboard_window():
    """查找已有 YouBoard 顶层窗口（包括最小化或隐藏到托盘的窗口）。"""
    if not IS_WIN:
        return None
    found = []
    user32 = ctypes.windll.user32
    enum_proc = ctypes.WINFUNCTYPE(
        ctypes.wintypes.BOOL, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)

    def _visit(hwnd, _lparam):
        length = user32.GetWindowTextLengthW(hwnd)
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            if "YouBoard" in (buf.value or ""):
                found.append(hwnd)
                return False
        return True

    callback = enum_proc(_visit)
    user32.EnumWindows.argtypes = [enum_proc, ctypes.wintypes.LPARAM]
    user32.EnumWindows.restype = ctypes.wintypes.BOOL
    user32.EnumWindows(callback, 0)
    return found[0] if found else None


def _activate_existing_window(hwnd):
    """把已有实例恢复并带到前台。"""
    if not IS_WIN or not hwnd:
        return
    user32 = ctypes.windll.user32
    try:
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    except Exception:
        try:
            user32.ShowWindow(hwnd, 5)  # SW_SHOW
        except Exception:
            pass
    try:
        user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


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
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.argtypes = [
        ctypes.c_void_p, ctypes.wintypes.BOOL, ctypes.wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = ctypes.wintypes.HANDLE
    kernel32.GetLastError.restype = ctypes.wintypes.DWORD
    kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
    handle = kernel32.CreateMutexW(None, False, mutex_name)
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        # 启动早期窗口标题可能尚未创建完整，短暂等待后按标题子串查找。
        hwnd = None
        for _ in range(8):
            hwnd = _find_youboard_window()
            if hwnd:
                break
            time.sleep(0.25)
        if hwnd:
            _activate_existing_window(hwnd)
        else:
            # 避免静默退出：明确告诉用户如何处理占用单实例锁的旧进程。
            ctypes.windll.user32.MessageBoxW(
                None,
                "YouBoard 已在后台运行，但窗口暂时无法显示。\n"
                "请从任务栏托盘退出旧实例，或在任务管理器中结束 "
                "YouBoard.exe 后重新打开。\n\n"
                "YouBoard is already running, but its window could not "
                "be shown. Please quit the old instance and try again.",
                "YouBoard",
                0x00000040)  # MB_ICONINFORMATION
        try:
            kernel32.CloseHandle(handle)
        except Exception:
            pass
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
    # 托盘程序：绝不因为"最后一个窗口被关掉"就自己结束进程。
    # 否则收进托盘 / 关掉导入窗口这类操作会让程序悄悄退出（用户要手动再打开）。
    # 真正的退出只走托盘右键 → 退出，以及主窗口 closeEvent 里的显式 quit。
    app.setQuitOnLastWindowClosed(False)
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
