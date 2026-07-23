#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
YouBoard v1.5.0 — 剪贴板历史管理器 / Clipboard History Manager
PyQt6 重构版：透明毛玻璃背景、QPropertyAnimation 动效、原生系统托盘。
"""

import colorsys
import ctypes
import locale
import math
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime

# ---------------------------------------------------------------------------
# High DPI setup (must precede QApplication creation)
# ---------------------------------------------------------------------------
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

APP_USER_MODEL_ID = "YouBoard.ClipboardHistory.1.5"
try:
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
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
    QCheckBox, QTextEdit, QListWidget, QListWidgetItem,
    QStyle,
)
from PyQt6.QtCore import (
    Qt, QTimer, QPropertyAnimation, QEasingCurve, pyqtSignal,
    QThread, QObject, QSize, QRect, QPoint, QEvent,
)
from PyQt6.QtGui import (
    QIcon, QPixmap, QImage, QPainter, QColor, QFont,
    QAction, QKeySequence, QShortcut, QBrush, QPen,
    QLinearGradient, QPainterPath, QCursor, QMovie,
)

try:
    from PIL import Image as PILImage
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

from youboard_core import (
    ClipboardStore, ClipboardMonitor, HISTORY_FILE, TIME_FORMAT,
    set_clipboard_text, set_clipboard_image, set_clipboard_files,
    load_config, save_config, get_autostart, set_autostart,
    get_icon_path, get_app_icon,
)

# ===========================================================================
# Constants
# ===========================================================================
APP_NAME = "YouBoard"
APP_VERSION = "1.5.0"
LOGO_ICO = get_icon_path()
DISPLAY_LIMIT = 400
HIST_DISPLAY = 60
PREVIEW_MAX = 1600
TAB_ICONS = {"text": "\u270e", "image": "\u25a3", "file": "\u25a0", "url": "\u25c9"}

# ===========================================================================
# Theme colors
# ===========================================================================
THEME_DARK = {
    "BG": "#131418", "SURFACE": "#1a1c22", "SURFACE2": "#20232b",
    "SURFACE3": "#272b34", "ROW_ALT": "#1d2027", "BORDER": "#2c303a",
    "BORDER_LT": "#383d4a", "TEXT": "#e8eaf0", "TEXT_SEC": "#a3a9b8",
    "TEXT_MUTED": "#6d7486", "ACCENT": "#4f9df8", "ACCENT_HV": "#6cb0ff",
    "ACCENT_DIM": "#28374e", "TEAL": "#3fd0b6", "AMBER": "#f2b54d",
    "PIN_BG": "#2a2517", "DANGER": "#f16a5c", "SUCCESS": "#45d18c",
    "FLASH_BG": "#1e3a2c",
    "PANEL_ALPHA": "rgba(20, 22, 28, 105)", "PANEL_ALPHA2": "rgba(26, 29, 36, 95)",
    "HEADER_ALPHA": "rgba(16, 17, 22, 80)",
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

# ===========================================================================
# QSS stylesheet generation
# ===========================================================================

def build_qss(theme_name="dark"):
    """Generate a complete QSS stylesheet for the given theme."""
    apply_theme(theme_name)
    c = C
    return f"""
    QMainWindow, QDialog {{ background-color: {c['BG']}; }}
    QWidget {{ color: {c['TEXT']}; font-family: "Microsoft YaHei UI","Segoe UI",sans-serif; font-size: 13px; }}
    QTabWidget::pane {{ border: none; background: {c['PANEL_ALPHA']}; border-radius: 8px; }}
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
    QComboBox QAbstractItemView {{ background: {c['SURFACE2']}; color: {c['TEXT']};
        selection-background-color: {c['ACCENT_DIM']}; border: 1px solid {c['BORDER']}; }}
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
        "type_text": "文字", "type_image": "图片", "type_file": "文件", "type_url": "网址",
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
        "hint_image": "双击 用默认看图打开 · Enter 复制图片 · Ctrl+O 打开 · Ctrl+E 导出",
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
        "st_copied_preview": "已复制预览文字（{n} 字符）",
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
        "preview_dblclick_viewer": "双击用看图软件打开",
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
        "m_copy_selection": "复制选中文字", "m_copy_all": "复制全部内容",
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
        "hint_image": "Double-click open in viewer · Enter copy image · Ctrl+O open · Ctrl+E export",
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
        "preview_dblclick_viewer": "Double-click to open in viewer",
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
        n = max(8, w // self.SEG_W)
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
# YouBoardApp — Main Window
# ===========================================================================
class YouBoardApp(QMainWindow):

    def __init__(self, store, monitor=None):
        super().__init__()
        self.store = store
        self.monitor = monitor
        self._active_type = "text"
        self._tables = {}
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

        self.setWindowTitle(tr("win_title"))
        self.resize(1180, 720)
        self.setMinimumSize(920, 540)
        # Restore saved window geometry
        cfg = load_config()
        saved_geo = cfg.get("win_geometry")
        if saved_geo and len(saved_geo) == 4:
            self.setGeometry(saved_geo[0], saved_geo[1], saved_geo[2], saved_geo[3])
        if cfg.get("win_maximized", False):
            self.showMaximized()
        if LOGO_ICO and os.path.exists(LOGO_ICO):
            self.setWindowIcon(QIcon(LOGO_ICO))

        self._build_ui()
        self._apply_background()
        QTimer.singleShot(150, self._initial_refresh)
        QTimer.singleShot(250, self._focus_search)
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_monitor)
        self._poll_timer.start(120)
        self._animate_dot()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self):
        central = QWidget()
        central.setStyleSheet("background: transparent;")
        self.setCentralWidget(central)
        self._bg_label = QLabel(central)
        self._bg_label.setGeometry(0, 0, 9999, 9999)
        self._bg_label.lower()

        root = QVBoxLayout(central)
        root.setContentsMargins(6, 0, 6, 6)
        root.setSpacing(0)
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
        for etype in ("text", "image", "file", "url"):
            tab_w = QWidget()
            self._tabs.addTab(tab_w, f"  {TAB_ICONS[etype]}  {self._type_label(etype)}  0  ")
            self._build_tab(tab_w, etype)
        self._tabs.blockSignals(False)

        self._build_statusbar(root)
        self._bind_shortcuts()

    def _build_header(self, root):
        header = QFrame()
        header.setStyleSheet(f"""
            QFrame {{
                background: {C['HEADER_ALPHA']};
                border: none;
                border-radius: 0;
            }}
        """)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(18, 12, 18, 10)
        hl.setSpacing(14)

        if LOGO_ICO and os.path.exists(LOGO_ICO):
            logo_lbl = QLabel()
            pm = QPixmap(LOGO_ICO).scaled(36, 36, Qt.AspectRatioMode.KeepAspectRatio,
                                          Qt.TransformationMode.SmoothTransformation)
            logo_lbl.setPixmap(pm)
            logo_lbl.setStyleSheet("background: transparent;")
            hl.addWidget(logo_lbl)

        brand_box = QVBoxLayout()
        brand_box.setSpacing(2)
        brand_row = QHBoxLayout()
        name_lbl = QLabel(APP_NAME)
        name_lbl.setFont(QFont("Bahnschrift", 17, QFont.Weight.Bold))
        name_lbl.setStyleSheet(f"color: {C['TEXT']};")
        brand_row.addWidget(name_lbl)
        sub_lbl = QLabel(tr("brand_sub"))
        sub_lbl.setStyleSheet(f"color: {C['TEXT_SEC']}; font-size: 12px;")
        brand_row.addWidget(sub_lbl)
        brand_row.addStretch()
        brand_box.addLayout(brand_row)
        sub_row = QHBoxLayout()
        self._dot_lbl = QLabel()
        self._dot_lbl.setFixedSize(8, 8)
        self._dot_lbl.setStyleSheet(f"background: {C['SUCCESS']}; border-radius: 4px;")
        sub_row.addWidget(self._dot_lbl)
        tag_lbl = QLabel("  CLIPBOARD HISTORY")
        tag_lbl.setStyleSheet(f"color: {C['TEXT_MUTED']}; font-size: 10px; letter-spacing: 2px;")
        sub_row.addWidget(tag_lbl)
        sub_row.addStretch()
        brand_box.addLayout(sub_row)
        hl.addLayout(brand_box)
        hl.addStretch()

        self._monitor_lbl = QLabel(tr("monitor_live") if self.monitor else tr("monitor_off"))
        self._monitor_lbl.setStyleSheet(f"color: {C['SUCCESS']}; font-size: 11px;")
        hl.addWidget(self._monitor_lbl)
        self._header_count = QLabel(tr("total_records", n=0))
        self._header_count.setStyleSheet(f"color: {C['TEXT_SEC']}; font-size: 12px;")
        hl.addWidget(self._header_count)

        settings_btn = QPushButton(tr("settings_btn"))
        settings_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
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
        """Load and display custom background image (static or animated GIF)."""
        cfg = load_config()
        bg_path = cfg.get("bg_image", "")
        if not bg_path or not os.path.exists(bg_path):
            self._bg_label.hide()
            return
        self._bg_label.show()
        if bg_path.lower().endswith(".gif"):
            self._bg_movie = QMovie(bg_path)
            self._bg_movie.frameChanged.connect(self._on_bg_frame)
            self._bg_movie.start()
        else:
            self._bg_pixmap = QPixmap(bg_path)
            self._scale_bg()

    def _on_bg_frame(self):
        if self._bg_movie:
            self._bg_pixmap = self._bg_movie.currentPixmap()
            self._scale_bg()

    def _scale_bg(self):
        if self._bg_pixmap and not self._bg_pixmap.isNull():
            scaled = self._bg_pixmap.scaled(
                self.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation)
            self._bg_label.setPixmap(scaled)
            self._bg_label.setGeometry(self.rect())

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
                self._bg_resize_timer.start(200)

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

        search_row = QHBoxLayout()
        search_edit = QLineEdit()
        search_edit.setPlaceholderText("\U0001f50d  " + tr("col_preview") + "...")
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
        combo = QComboBox()
        combo.addItems([tr("sort_" + sid) for sid in sort_ids])
        combo.setFixedWidth(150)
        combo.currentIndexChanged.connect(
            lambda idx, t=etype, ids=sort_ids: self._on_sort_changed(t, idx, ids))
        self._sort_combos[etype] = combo
        search_row.addWidget(combo)
        lay.addLayout(search_row)

        act_row = QHBoxLayout()
        copy_btn = QPushButton(tr("btn_copy"))
        copy_btn.setProperty("cssClass", "accent")
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

    def _build_preview_panel(self, parent_split):
        pf = QFrame()
        pf.setStyleSheet(f"background: {C['PANEL_ALPHA2']}; border: none; border-radius: 8px;")
        pl = QVBoxLayout(pf)
        pl.setContentsMargins(8, 6, 8, 6)
        pl.setSpacing(4)
        title = QLabel(tr("panel_preview"))
        title.setStyleSheet(f"color: {C['ACCENT']}; font-weight: bold; font-size: 12px;")
        pl.addWidget(title)
        self._preview_scroll = QScrollArea()
        self._preview_scroll.setWidgetResizable(True)
        self._preview_inner = QWidget()
        self._preview_layout = QVBoxLayout(self._preview_inner)
        self._preview_layout.setContentsMargins(4, 4, 4, 4)
        self._preview_layout.setSpacing(4)
        self._preview_scroll.setWidget(self._preview_inner)
        pl.addWidget(self._preview_scroll, 1)
        self._show_preview_placeholder()
        parent_split.addWidget(pf)

    def _build_history_panel(self, parent_split):
        hf = QFrame()
        hf.setStyleSheet(f"background: {C['PANEL_ALPHA2']}; border: none; border-radius: 8px;")
        hl = QVBoxLayout(hf)
        hl.setContentsMargins(8, 6, 8, 6)
        hl.setSpacing(4)
        title = QLabel(tr("panel_snapshots"))
        title.setStyleSheet(f"color: {C['ACCENT']}; font-weight: bold; font-size: 12px;")
        hl.addWidget(title)
        self._hist_list = QListWidget()
        self._hist_list.setAlternatingRowColors(False)
        self._hist_list.setSpacing(2)
        self._hist_list.doubleClicked.connect(lambda: self._restore_history())
        hl.addWidget(self._hist_list, 1)
        btn_row = QHBoxLayout()
        rb = QPushButton(tr("btn_restore"))
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
        bar.setStyleSheet(f"background: {C['PANEL_ALPHA']}; border-top: 1px solid {C['BORDER']};")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(12, 0, 12, 0)
        self._hint_lbl = QLabel()
        self._hint_lbl.setStyleSheet(f"color: {C['TEXT_MUTED']}; font-size: 11px;")
        bl.addWidget(self._hint_lbl)
        bl.addStretch()
        self._sel_lbl = QLabel()
        self._sel_lbl.setStyleSheet(f"color: {C['ACCENT']}; font-size: 12px; font-weight: bold;")
        bl.addWidget(self._sel_lbl)
        self._status_lbl = QLabel()
        self._status_lbl.setStyleSheet(f"color: {C['TEXT_SEC']}; font-size: 12px; font-weight: bold;")
        bl.addWidget(self._status_lbl)
        root.addWidget(bar)

    def _bind_shortcuts(self):
        QShortcut(QKeySequence("F5"), self, activated=self._refresh_all)
        QShortcut(QKeySequence("Ctrl+A"), self, activated=self._select_all)
        QShortcut(QKeySequence("Ctrl+O"), self, activated=self._open_selected)
        QShortcut(QKeySequence("Ctrl+E"), self, activated=self._export_selected)
        QShortcut(QKeySequence("Delete"), self, activated=self._delete_selected)
        QShortcut(QKeySequence("Return"), self, activated=self._copy_selected)
        QShortcut(QKeySequence("Space"), self, activated=self._toggle_pin_selected)
        QShortcut(QKeySequence("Escape"), self, activated=self._focus_search)

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
                paths = entry.get("file_paths", [])
                sizes = entry.get("file_sizes", [])
                total_sz = sum(s for s in sizes if s > 0) if sizes else 0
                fp = "  |  ".join(os.path.basename(p) for p in paths[:6])
                if len(paths) > 6:
                    fp += f"  …(+{len(paths) - 6})"
                vals = [str(i + 1), time_str, status,
                        str(entry.get("file_count", len(paths))),
                        _extract_extensions(paths),
                        fmt_size(total_sz) if total_sz > 0 else "?", fp]
            for col, val in enumerate(vals):
                item = QTableWidgetItem(val)
                if col in (0, 2):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if is_pin:
                    item.setBackground(pin_color)
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

    def _update_tab_badge(self, etype):
        n = self.store.count(etype)
        idx = ("text", "image", "file", "url").index(etype)
        self._tabs.setTabText(idx, f"  {TAB_ICONS[etype]}  {self._type_label(etype)}  {n}  ")

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
        self._status_lbl.setStyleSheet(f"color: {color}; font-size: 12px; font-weight: bold;")
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
        while self._preview_layout.count():
            item = self._preview_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

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
        self._preview_img_lbl.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._preview_img_lbl.mouseDoubleClickEvent = lambda e: self._open_selected()
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
        self._render_preview_image()

    def _render_preview_image(self):
        if not self._cached_pil or not self._cur_image_path:
            return
        if not hasattr(self, '_preview_img_lbl'):
            return
        max_w = max(40, self._preview_inner.width() - 24)
        max_h = max(40, self._preview_inner.height() - 70)
        key = (self._cur_image_path, max_w, max_h)
        if key == self._last_render_key:
            return
        self._last_render_key = key
        try:
            src = self._cached_pil
            ratio = min(max_w / src.width, max_h / src.height, 1.0)
            nw, nh = max(1, int(src.width * ratio)), max(1, int(src.height * ratio))
            resized = src if ratio >= 1.0 else src.resize((nw, nh), PILImage.LANCZOS)
            mode = "RGBA" if resized.mode == "RGBA" else "RGB"
            data = resized.tobytes("raw", mode)
            qimg = QImage(data, resized.width, resized.height,
                          QImage.Format.Format_RGBA8888 if mode == "RGBA" else QImage.Format.Format_RGB888)
            self._preview_img_lbl.setPixmap(QPixmap.fromImage(qimg))
        except Exception:
            pass

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
            os.startfile(path)
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
                    os.startfile(img_path)
                    self._set_status(tr("st_opened_viewer"), "ok")
                else:
                    self._set_status(tr("st_image_missing"), "err")
            elif etype == "file":
                paths = [p for p in entry.get("file_paths", []) if os.path.exists(p)]
                if not paths:
                    self._set_status(tr("st_path_missing"), "err")
                    return
                if len(paths) == 1:
                    os.startfile(paths[0])
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
        subprocess.Popen(f'explorer /select,"{os.path.abspath(path)}"')

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
        if self.monitor and self.monitor.consume_change():
            self._on_clip_changed()

    def _on_clip_changed(self):
        self._refresh_tab(self._active_type)
        for etype in ("text", "image", "file", "url"):
            if etype != self._active_type:
                self._update_tab_badge(etype)
        self._update_header_stats()
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
            self._monitor_lbl.setStyleSheet(f"color: {C['SUCCESS']}; font-size: 11px;")
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
        if need_restart:
            # Save window state so it persists across restart
            geo = self.geometry()
            cfg["win_geometry"] = [geo.x(), geo.y(), geo.width(), geo.height()]
            cfg["win_maximized"] = self.isMaximized()
            save_config(cfg)
            self.restart_flag = True
            self.close()

    # ------------------------------------------------------------------
    # Cleanup / tray / fade-in
    # ------------------------------------------------------------------
    def closeEvent(self, event):
        self._real_quit()
        event.accept()

    def _real_quit(self):
        if self.monitor:
            self.monitor.stop()
        if hasattr(self, '_tray') and self._tray:
            self._tray.hide()

    def run(self):
        """Show window with tray icon and fade-in animation."""
        # System tray
        self._tray = QSystemTrayIcon(QIcon(LOGO_ICO) if LOGO_ICO else QIcon(), self)
        tray_menu = QMenu()
        show_act = QAction("Show YouBoard", self)
        show_act.triggered.connect(self._tray_show)
        quit_act = QAction("Quit", self)
        quit_act.triggered.connect(self._tray_quit)
        tray_menu.addAction(show_act)
        tray_menu.addSeparator()
        tray_menu.addAction(quit_act)
        self._tray.setContextMenu(tray_menu)
        self._tray.setToolTip("YouBoard v" + APP_VERSION)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()
        # Fade-in animation
        self._fade_in()
        self.show()

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
        effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", self)
        anim.setDuration(420)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(lambda: self.setGraphicsEffect(None))
        anim.start()
        self._fade_anim = anim  # prevent GC


# ===========================================================================
# Settings Dialog
# ===========================================================================
class SettingsDialog(QDialog):

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.setWindowTitle(tr("settings_title"))
        self.setFixedSize(480, 620)
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
        scroll.setFrameShape(QFrame.Shape.NoFrame)
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
        note = QLabel(tr("set_lang_note"))
        note.setStyleSheet(f"color: {C['TEXT_MUTED']}; font-size: 11px;")
        self._lay.addWidget(note)
        self._paint_lang()

        # General card
        self._card(tr("set_general"))
        auto_row = QHBoxLayout()
        auto_txt = QVBoxLayout()
        t1 = QLabel(tr("set_autostart"))
        t1.setStyleSheet(f"color: {C['TEXT']}; font-weight: bold;")
        t2 = QLabel(tr("set_autostart_desc"))
        t2.setStyleSheet(f"color: {C['TEXT_MUTED']}; font-size: 11px;")
        t2.setWordWrap(True)
        auto_txt.addWidget(t1)
        auto_txt.addWidget(t2)
        auto_row.addLayout(auto_txt, 1)
        self._auto_cb = QCheckBox()
        self._auto_cb.setChecked(get_autostart())
        auto_row.addWidget(self._auto_cb)
        self._lay.addLayout(auto_row)

        # Theme card
        self._card(tr("set_theme"))
        theme_row = QHBoxLayout()
        self._theme_btns = {}
        for tname, icon in (("dark", "\U0001f319"), ("light", "\u2600\ufe0f")):
            btn = QPushButton(f"{icon}  {tr('set_theme_' + tname)}")
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn.clicked.connect(lambda _, t=tname: self._pick_theme(t))
            self._theme_btns[tname] = btn
            theme_row.addWidget(btn)
        self._lay.addLayout(theme_row)
        note2 = QLabel(tr("set_theme_note"))
        note2.setStyleSheet(f"color: {C['TEXT_MUTED']}; font-size: 11px;")
        self._lay.addWidget(note2)
        self._paint_theme()

        # Background card
        self._card(tr("set_bg"))
        bg_row = QHBoxLayout()
        self._bg_lbl = QLabel(self._bg_display_name())
        self._bg_lbl.setStyleSheet(f"color: {C['TEXT_SEC']}; font-size: 11px;")
        bg_row.addWidget(self._bg_lbl, 1)
        sel_btn = QPushButton(tr("set_bg_select"))
        sel_btn.clicked.connect(self._select_bg)
        bg_row.addWidget(sel_btn)
        clr_btn = QPushButton(tr("set_bg_clear"))
        clr_btn.clicked.connect(self._clear_bg)
        bg_row.addWidget(clr_btn)
        self._lay.addLayout(bg_row)
        hint = QLabel(tr("set_bg_hint"))
        hint.setStyleSheet(f"color: {C['TEXT_MUTED']}; font-size: 10px;")
        self._lay.addWidget(hint)

        # About card
        self._card(tr("set_about"))
        ver = QLabel(f"{APP_NAME}  v{APP_VERSION}")
        ver.setStyleSheet(f"color: {C['TEXT']}; font-family: Bahnschrift; font-size: 14px; font-weight: bold;")
        self._lay.addWidget(ver)
        data_lbl = QLabel(f"{tr('set_data_location')}: {os.path.dirname(HISTORY_FILE)}")
        data_lbl.setStyleSheet(f"color: {C['TEXT_MUTED']}; font-size: 10px; font-family: Consolas;")
        data_lbl.setWordWrap(True)
        self._lay.addWidget(data_lbl)
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

    def _card(self, title):
        lbl = QLabel(title)
        lbl.setStyleSheet(f"color: {C['TEXT_MUTED']}; font-size: 11px; font-weight: bold; "
                          f"letter-spacing: 1px; padding-top: 8px;")
        self._lay.addWidget(lbl)

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
            return os.path.basename(self._bg_path)
        return tr("set_bg_current")

    def _select_bg(self):
        path, _ = QFileDialog.getOpenFileName(
            self, tr("set_bg_select"), "",
            "Images (*.png *.jpg *.jpeg *.bmp *.gif);;All (*.*)")
        if path:
            self._bg_path = path
            self._bg_lbl.setText(os.path.basename(path))

    def _clear_bg(self):
        self._bg_path = ""
        self._bg_lbl.setText(tr("set_bg_current"))

    def _save(self):
        cfg = load_config()
        old_bg = cfg.get("bg_image", "")
        bg_changed = (old_bg != self._bg_path)
        cfg["bg_image"] = self._bg_path
        save_config(cfg)
        self.accept()
        self.app.apply_settings(self._lang_sel, self._auto_cb.isChecked(),
                                self._theme_sel, bg_changed)


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
    try:
        restart = True
        while restart:
            cfg = load_config()
            theme_name = cfg.get("theme", "dark")
            apply_language(cfg.get("language", "zh"))
            apply_theme(theme_name)
            app.setStyleSheet(build_qss(theme_name))
            gui = YouBoardApp(store, monitor)
            gui.run()
            app.exec()
            restart = gui.restart_flag
    finally:
        monitor.stop()


if __name__ == "__main__":
    main()
