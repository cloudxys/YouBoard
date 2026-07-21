#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
YouBoard — 剪贴板历史管理器 / Clipboard History Manager。
文字 / 图片 / 文件 三分类 + 快照历史；暗色主题 GUI：环境灯带、实时监听、
缩略图渐进预览、防抖搜索、中英双语、开机自启动。
"""

import colorsys
import ctypes
import locale
import math
import os
import queue
import random
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import ttk, messagebox, filedialog
from datetime import datetime

# 高 DPI 支持（必须在创建 Tk 之前）
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

# 任务栏图标：必须在创建任何窗口之前设置 AppUserModelID
APP_USER_MODEL_ID = "YouBoard.ClipboardHistory.1.2"
try:
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
except Exception:
    pass

# 中文友好的字符串排序
try:
    locale.setlocale(locale.LC_COLLATE, '')
except Exception:
    pass

try:
    from PIL import Image, ImageTk
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

from youboard_core import (
    ClipboardStore, ClipboardMonitor, HISTORY_FILE, TIME_FORMAT,
    set_clipboard_text, set_clipboard_image, set_clipboard_files,
    load_config, save_config, get_autostart, set_autostart,
    get_icon_path, get_app_icon, TrayIcon,
)

# ===========================================================================
# 主题配色（暗色、多层次）
# ===========================================================================

BG         = "#131418"   # 窗口基底
SURFACE    = "#1a1c22"   # 主内容面板
SURFACE2   = "#20232b"   # 卡片 / 输入框 / 侧栏
SURFACE3   = "#272b34"   # 悬停
ROW_ALT    = "#1d2027"   # 斑马纹
BORDER     = "#2c303a"
BORDER_LT  = "#383d4a"

TEXT       = "#e8eaf0"
TEXT_SEC   = "#a3a9b8"
TEXT_MUTED = "#6d7486"

ACCENT     = "#4f9df8"   # 主蓝
ACCENT_HV  = "#6cb0ff"
ACCENT_DIM = "#28374e"   # 选中行
TEAL       = "#3fd0b6"
AMBER      = "#f2b54d"
PIN_BG     = "#2a2517"   # 置顶行底色
DANGER     = "#f16a5c"
SUCCESS    = "#45d18c"
FLASH_BG   = "#1e3a2c"   # 复制成功闪烁

TAB_ICONS = {"text": "\U0001f4dd", "image": "\U0001f5bc", "file": "\U0001f4c1", "url": "\U0001f310"}

APP_NAME    = "YouBoard"
APP_VERSION = "1.2.0"


def _find_logo():
    """查找 Logo 图标：复用 core 的跨路径兼容函数。"""
    return get_icon_path()


LOGO_ICO = _find_logo()

# 字体（品牌展示字体 + 正文字体搭配）
F_BRAND   = ("Bahnschrift", 19, "bold")          # Windows 现代展示字体
F_UI      = ("Microsoft YaHei UI", 9)
F_UI_B    = ("Microsoft YaHei UI", 9, "bold")
F_SUB     = ("Segoe UI", 8)
F_MONO    = ("Consolas", 10)
F_SMALL   = ("Microsoft YaHei UI", 8)
F_HEAD    = ("Microsoft YaHei UI", 8, "bold")

DISPLAY_LIMIT = 400     # 每个列表最多渲染的行数（保证滚动流畅）
HIST_DISPLAY  = 60      # 快照历史最多显示条数
PREVIEW_MAX   = 1600    # 预览图最大边长（内存缓存上限）


# ===========================================================================
# i18n — 中英双语词典（切换语言后应用重启生效）
# ===========================================================================

STRINGS = {
    "zh": {
        # 窗口 / 页头
        "win_title": "YouBoard · 剪贴板历史",
        "brand_sub": "剪贴板历史",
        "manage": " 管理 \u25be ",
        "settings_btn": " \u2699 设置 ",
        "total_records": "共 {n} 条记录",
        "monitor_live": "实时监控中",
        "monitor_off": "未监控",
        "monitor_stopped": "监控已停止",
        # 分类 / 标签页
        "type_text": "文字",
        "type_image": "图片",
        "type_file": "文件",
        "type_url": "网址",
        # 面板
        "panel_preview": " 预览 ",
        "panel_snapshots": " 历史快照 ",
        "panel_urls": " 网址 ",
        "preview_placeholder": "\n选择一条记录\n即可预览\n",
        "btn_restore": "恢复选中状态",
        "btn_clear_history": "清空历史",
        # 排序
        "sort_default": "默认(时间最新)",
        "sort_oldest": "时间(最早)",
        "sort_name_az": "文件名(A-Z)",
        "sort_name_za": "文件名(Z-A)",
        "sort_fmt_az": "格式(A-Z)",
        "sort_fmt_za": "格式(Z-A)",
        "sort_size_desc": "大小(最大)",
        "sort_size_asc": "大小(最小)",
        # 操作按钮
        "btn_copy": "复制  Enter",
        "btn_pin": "置顶",
        "btn_unpin": "取消置顶",
        "btn_delete": "删除  Del",
        "btn_export": "导出",
        "btn_open": "打开  双击",
        # 表头
        "col_time": "时间",
        "col_preview": "内容预览",
        "col_filename": "文件名",
        "col_format": "格式",
        "col_dims": "尺寸",
        "col_size": "大小",
        "col_count": "数量",
        "col_files": "文件列表",
        "col_url": "网址",
        # 列表状态
        "empty_state": "\n还没有记录\n复制任意内容即可自动捕获\n",
        "selected_n": "已选 {n} 项",
        "count_match": "匹配 {n} 条",
        "count_shown": "显示 {shown} / {total} 条 · 置顶 {pinned}",
        "count_total": "共 {total} 条 · 置顶 {pinned}",
        "no_ext": "无后缀",
        # 底部提示
        "hint_text": "Enter/双击 复制  ·  Space 置顶  ·  Del 删除  ·  Ctrl+A 全选  ·  F5 刷新",
        "hint_image": "双击 用默认看图打开  ·  Enter 复制图片  ·  Ctrl+O 打开  ·  Ctrl+E 导出",
        "hint_file": "双击 打开文件  ·  Enter 复制文件  ·  Ctrl+O 打开  ·  右键查看更多",
        "hint_url": "双击/Enter 在浏览器打开  ·  Space 置顶  ·  Del 删除  ·  Ctrl+A 全选",
        # 状态消息
        "st_refreshed": "已刷新",
        "st_captured": "捕获到新的剪贴板内容",
        "st_nothing_to_copy": "没有可复制的记录",
        "st_copied_chars": "已复制（{n} 字符）",
        "st_image_copied": "图片已复制到剪贴板",
        "st_image_missing": "图片文件未找到",
        "st_files_copied": "已复制 {n} 个文件",
        "st_paths_missing": "记录的文件路径已不存在",
        "st_opened_viewer": "已用默认看图软件打开",
        "st_opened_url": "已在浏览器中打开网址",
        "st_path_missing": "文件路径已不存在",
        "st_opened_file": "已打开文件",
        "st_revealed": "已在资源管理器中定位（共 {n} 个文件）",
        "st_already_pinned": "选中的都已置顶",
        "st_pinned": "已置顶 {n} 条",
        "st_not_pinned": "选中的都未置顶",
        "st_unpinned": "已取消置顶 {n} 条",
        "st_pin_toggled": "置顶 {a} 条 · 取消 {b} 条",
        "st_deleted": "已删除 {n} 条",
        "st_no_type_records": "没有{t}记录可清空",
        "st_cleared_type": "已清空{t}",
        "st_no_unpinned_type": "没有{t}非置顶记录可清除",
        "st_cleared_unpinned_type": "已清除{t}非置顶记录",
        "st_no_unpinned": "没有非置顶记录可清除",
        "st_cleared_unpinned": "已清除全部非置顶记录",
        "st_nothing_to_clear": "没有可清空的内容",
        "st_cleared_all": "已全部清空",
        "st_nothing_to_export": "没有可导出的记录",
        "st_exported": "已导出至 {name}",
        "st_export_files_hint": "文件记录引用的是外部路径，可用「复制路径」",
        "st_copied_image_path": "已复制图片文件路径",
        "st_copied_paths": "已复制文件路径列表",
        "st_copied_preview": "已复制预览文字（{n} 字符）",
        "st_autostart_on": "已开启开机自启动",
        "st_autostart_off": "已关闭开机自启动",
        "st_autostart_failed": "设置开机自启动失败",
        # 快照
        "snap_select_first": "请先选择一条历史快照",
        "st_restored": "已恢复历史状态",
        "snap_empty": "历史记录为空",
        "st_history_cleared": "历史记录已清空",
        "snap_pin": "置顶 {n} 条（{t}）",
        "snap_unpin": "取消置顶 {n} 条（{t}）",
        "snap_toggle_pin": "切换置顶（{t}）",
        "snap_delete": "删除 {n} 条（{t}）",
        "snap_clear_type": "清空分类：{t}（{n} 条）",
        "snap_clear_type_unpinned": "清空{t}非置顶（{n} 条）",
        "snap_clear_unpinned": "清除非置顶（{n} 条）",
        "snap_clear_all": "清空全部（{n} 条）",
        "snap_before_restore": "恢复前：当前状态",
        # 预览
        "preview_truncated": "\n\n…（内容过长，已截断显示）",
        "chip_chars": " {n} 字符 ",
        "chip_lines": " {n} 行 ",
        "preview_unavailable": "（预览不可用）",
        "preview_dblclick_viewer": "双击用看图软件打开",
        "preview_dblclick_url": "双击在浏览器中打开",
        "chip_files": " {n} 个文件 ",
        "preview_dblclick_open": "双击打开文件",
        # 对话框
        "dlg_error": "错误",
        "dlg_info": "提示",
        "dlg_confirm_delete": "确认删除",
        "dlg_confirm_clear": "确认清空",
        "dlg_confirm_restore": "确认恢复",
        "dlg_confirm_remove": "确认清除",
        "msg_copy_failed": "复制失败：{err}",
        "msg_open_failed": "打开失败：{err}",
        "msg_file_not_found": "文件未找到：\n{path}",
        "msg_delete_confirm": "确定要删除选中的 {n} 条记录吗？",
        "msg_clear_type": "确定要清空全部{t}记录（{n} 条）吗？",
        "msg_clear_type_unpinned": "确定要清除{t}分类的非置顶记录吗？\n（删除 {n} 条，保留置顶）",
        "msg_clear_unpinned": "确定要清除全部非置顶记录吗？\n（删除 {n} 条，保留置顶）",
        "msg_clear_all": "确定要清空全部剪贴板历史吗？（共 {n} 条）",
        "msg_clear_history": "确定要清空全部 {n} 条历史记录吗？",
        "msg_restore_confirm": "确定要恢复到以下状态吗？\n\n{ts}\n{desc}\n\n当前状态将先存入历史。",
        # 右键菜单
        "m_copy_content": "复制内容  (Enter)",
        "m_export_txt": "导出为 .txt…",
        "m_copy_image": "复制图片到剪贴板  (Enter)",
        "m_open_viewer": "用默认看图软件打开  (Ctrl+O)",
        "m_open_viewer_plain": "用默认看图软件打开",
        "m_open_folder": "打开所在文件夹",
        "m_copy_path": "复制文件路径",
        "m_export_image": "导出图片…  (Ctrl+E)",
        "m_copy_files": "复制文件到剪贴板  (Enter)",
        "m_open_locate": "打开 / 定位文件  (Ctrl+O)",
        "m_copy_paths": "复制路径列表",
        "m_toggle_pin": "置顶 / 取消置顶  (Space)",
        "m_delete": "删除  (Del)",
        "m_delete_n": "删除（{n} 条）  (Del)",
        "m_copy_selection": "复制选中文字",
        "m_copy_all": "复制全部内容",
        "m_select_all": "全选",
        "m_open_url": "在浏览器中打开网址",
        "m_refresh": "刷新列表  (F5)",
        "m_clear_type": "清空「{t}」分类…",
        "m_clear_type_unpinned": "清除「{t}」非置顶…",
        "m_clear_unpinned": "清除全部非置顶…",
        "m_clear_all": "清空全部…",
        # 设置
        "settings_title": "YouBoard · 设置",
        "set_language": "语言 / LANGUAGE",
        "set_lang_zh": "简体中文",
        "set_lang_en": "English",
        "set_lang_note": "切换语言后应用将立即重启",
        "set_general": "通用 / GENERAL",
        "set_autostart": "开机自启动",
        "set_autostart_desc": "登录 Windows 后自动启动 YouBoard 并监听剪贴板",
        "set_about": "关于 / ABOUT",
        "set_data_location": "数据位置",
        "btn_save": "保存",
        "btn_cancel": "取消",
        # 文件对话框
        "ft_text": "文本文件",
        "ft_all": "所有文件",
        # CLI
        "cli_empty": "剪贴板历史为空。",
        "cli_h_pin": "置顶",
        "cli_h_type": "类型",
        "cli_h_time": "时间",
        "cli_h_preview": "预览",
        "cli_not_found": "未找到包含 '{kw}' 的记录。",
        "cli_found": "找到 {n} 条：",
        "cli_cleared": "剪贴板历史已清空。",
        "cli_daemon_started": "YouBoard 剪贴板监控已启动（后台模式）...",
        "cli_history_file": "历史文件：{path}",
        "cli_ctrl_c": "按 Ctrl+C 停止。",
        "cli_stopped": "\n监控已停止。",
    },
    "en": {
        # Window / header
        "win_title": "YouBoard · Clipboard History",
        "brand_sub": "Clipboard History",
        "manage": " Manage \u25be ",
        "settings_btn": " \u2699 Settings ",
        "total_records": "{n} records",
        "monitor_live": "Live monitoring",
        "monitor_off": "Not monitoring",
        "monitor_stopped": "Monitor stopped",
        # Categories / tabs
        "type_text": "Text",
        "type_image": "Images",
        "type_file": "Files",
        "type_url": "URLs",
        # Panels
        "panel_preview": " Preview ",
        "panel_snapshots": " Snapshots ",
        "panel_urls": " URLs ",
        "preview_placeholder": "\nSelect a record\nto preview\n",
        "btn_restore": "Restore selected",
        "btn_clear_history": "Clear history",
        # Sorting
        "sort_default": "Default (newest)",
        "sort_oldest": "Oldest first",
        "sort_name_az": "Name (A-Z)",
        "sort_name_za": "Name (Z-A)",
        "sort_fmt_az": "Format (A-Z)",
        "sort_fmt_za": "Format (Z-A)",
        "sort_size_desc": "Size (largest)",
        "sort_size_asc": "Size (smallest)",
        # Action buttons
        "btn_copy": "Copy  Enter",
        "btn_pin": "Pin",
        "btn_unpin": "Unpin",
        "btn_delete": "Delete",
        "btn_export": "Export",
        "btn_open": "Open  Dbl-click",
        # Column headings
        "col_time": "Time",
        "col_preview": "Preview",
        "col_filename": "Filename",
        "col_format": "Format",
        "col_dims": "Dimensions",
        "col_size": "Size",
        "col_count": "Count",
        "col_files": "Files",
        "col_url": "URL",
        # List states
        "empty_state": "\nNo records yet\nCopy anything and it will be captured\n",
        "selected_n": "{n} selected",
        "count_match": "{n} matched",
        "count_shown": "Showing {shown} / {total} · {pinned} pinned",
        "count_total": "{total} records · {pinned} pinned",
        "no_ext": "no ext",
        # Footer hints
        "hint_text": "Enter/double-click copy · Space pin · Del delete · Ctrl+A select all · F5 refresh",
        "hint_image": "Double-click open in viewer · Enter copy image · Ctrl+O open · Ctrl+E export",
        "hint_file": "Double-click open file · Enter copy files · Ctrl+O open · Right-click for more",
        "hint_url": "Double-click/Enter open in browser · Space pin · Del delete · Ctrl+A select all",
        # Status messages
        "st_refreshed": "Refreshed",
        "st_captured": "New clipboard content captured",
        "st_nothing_to_copy": "Nothing to copy",
        "st_copied_chars": "Copied ({n} chars)",
        "st_image_copied": "Image copied to clipboard",
        "st_image_missing": "Image file not found",
        "st_files_copied": "Copied {n} file(s)",
        "st_paths_missing": "Recorded file paths no longer exist",
        "st_opened_viewer": "Opened in default viewer",
        "st_opened_url": "Opened URL in browser",
        "st_path_missing": "File path no longer exists",
        "st_opened_file": "File opened",
        "st_revealed": "Revealed in Explorer ({n} files)",
        "st_already_pinned": "Selection already pinned",
        "st_pinned": "Pinned {n}",
        "st_not_pinned": "Selection not pinned",
        "st_unpinned": "Unpinned {n}",
        "st_pin_toggled": "Pinned {a} · Unpinned {b}",
        "st_deleted": "Deleted {n}",
        "st_no_type_records": "No {t} records to clear",
        "st_cleared_type": "Cleared {t}",
        "st_no_unpinned_type": "No unpinned {t} records to remove",
        "st_cleared_unpinned_type": "Removed unpinned {t} records",
        "st_no_unpinned": "No unpinned records to remove",
        "st_cleared_unpinned": "Removed all unpinned records",
        "st_nothing_to_clear": "Nothing to clear",
        "st_cleared_all": "All cleared",
        "st_nothing_to_export": "Nothing to export",
        "st_exported": "Exported to {name}",
        "st_export_files_hint": "File records reference external paths - use 'Copy path list'",
        "st_copied_image_path": "Image path copied",
        "st_copied_paths": "File path list copied",
        "st_copied_preview": "Copied preview text ({n} chars)",
        "st_autostart_on": "Start with Windows enabled",
        "st_autostart_off": "Start with Windows disabled",
        "st_autostart_failed": "Failed to change autostart setting",
        # Snapshots
        "snap_select_first": "Select a snapshot first",
        "st_restored": "Snapshot restored",
        "snap_empty": "No snapshots",
        "st_history_cleared": "Snapshots cleared",
        "snap_pin": "Pinned {n} ({t})",
        "snap_unpin": "Unpinned {n} ({t})",
        "snap_toggle_pin": "Toggled pin ({t})",
        "snap_delete": "Deleted {n} ({t})",
        "snap_clear_type": "Cleared {t} ({n})",
        "snap_clear_type_unpinned": "Removed unpinned {t} ({n})",
        "snap_clear_unpinned": "Removed unpinned ({n})",
        "snap_clear_all": "Cleared all ({n})",
        "snap_before_restore": "Before restore: current state",
        # Preview
        "preview_truncated": "\n\n…(content too long, truncated)",
        "chip_chars": " {n} chars ",
        "chip_lines": " {n} lines ",
        "preview_unavailable": "(Preview unavailable)",
        "preview_dblclick_viewer": "Double-click to open in viewer",
        "preview_dblclick_url": "Double-click to open in browser",
        "chip_files": " {n} files ",
        "preview_dblclick_open": "Double-click to open file",
        # Dialogs
        "dlg_error": "Error",
        "dlg_info": "Notice",
        "dlg_confirm_delete": "Confirm delete",
        "dlg_confirm_clear": "Confirm clear",
        "dlg_confirm_restore": "Confirm restore",
        "dlg_confirm_remove": "Confirm remove",
        "msg_copy_failed": "Copy failed: {err}",
        "msg_open_failed": "Open failed: {err}",
        "msg_file_not_found": "File not found:\n{path}",
        "msg_delete_confirm": "Delete {n} selected record(s)?",
        "msg_clear_type": "Clear all {t} records ({n})?",
        "msg_clear_type_unpinned": "Remove unpinned {t} records?\n({n} will be deleted, pinned ones are kept)",
        "msg_clear_unpinned": "Remove all unpinned records?\n({n} will be deleted, pinned ones are kept)",
        "msg_clear_all": "Clear the entire clipboard history? ({n} records)",
        "msg_clear_history": "Clear all {n} snapshots?",
        "msg_restore_confirm": "Restore to the following state?\n\n{ts}\n{desc}\n\nThe current state will be saved to history first.",
        # Context menus
        "m_copy_content": "Copy content  (Enter)",
        "m_export_txt": "Export as .txt…",
        "m_copy_image": "Copy image to clipboard  (Enter)",
        "m_open_viewer": "Open in default viewer  (Ctrl+O)",
        "m_open_viewer_plain": "Open in default viewer",
        "m_open_folder": "Open containing folder",
        "m_copy_path": "Copy file path",
        "m_export_image": "Export image…  (Ctrl+E)",
        "m_copy_files": "Copy files to clipboard  (Enter)",
        "m_open_locate": "Open / locate files  (Ctrl+O)",
        "m_copy_paths": "Copy path list",
        "m_toggle_pin": "Pin / Unpin  (Space)",
        "m_delete": "Delete  (Del)",
        "m_delete_n": "Delete ({n})  (Del)",
        "m_copy_selection": "Copy selection",
        "m_copy_all": "Copy all",
        "m_select_all": "Select all",
        "m_open_url": "Open URL in browser",
        "m_refresh": "Refresh list  (F5)",
        "m_clear_type": "Clear '{t}'…",
        "m_clear_type_unpinned": "Remove unpinned '{t}'…",
        "m_clear_unpinned": "Remove all unpinned…",
        "m_clear_all": "Clear all…",
        # Settings
        "settings_title": "YouBoard · Settings",
        "set_language": "Language / 语言",
        "set_lang_zh": "简体中文",
        "set_lang_en": "English",
        "set_lang_note": "The app restarts immediately after switching language",
        "set_general": "General / 通用",
        "set_autostart": "Start with Windows",
        "set_autostart_desc": "Automatically start YouBoard and monitor the clipboard when you sign in",
        "set_about": "About / 关于",
        "set_data_location": "Data location",
        "btn_save": "Save",
        "btn_cancel": "Cancel",
        # File dialogs
        "ft_text": "Text files",
        "ft_all": "All files",
        # CLI
        "cli_empty": "Clipboard history is empty.",
        "cli_h_pin": "Pin",
        "cli_h_type": "Type",
        "cli_h_time": "Time",
        "cli_h_preview": "Preview",
        "cli_not_found": "No records containing '{kw}'.",
        "cli_found": "Found {n}:",
        "cli_cleared": "Clipboard history cleared.",
        "cli_daemon_started": "YouBoard clipboard monitor started (background mode)...",
        "cli_history_file": "History file: {path}",
        "cli_ctrl_c": "Press Ctrl+C to stop.",
        "cli_stopped": "\nMonitor stopped.",
    },
}

LANG = "zh"


def tr(key, **kw):
    """按当前语言取字符串；缺失时回退中文，支持 {占位符} 格式化。"""
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
    global LANG
    LANG = lang if lang in STRINGS else "zh"


def _lerp_color(c1, c2, t):
    """两个 #rrggbb 颜色线性插值。"""
    a = tuple(int(c1[i:i + 2], 16) for i in (1, 3, 5))
    b = tuple(int(c2[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % tuple(int(x + (y - x) * t) for x, y in zip(a, b))


def fmt_image_type(fmt_str):
    fmt = fmt_str.upper()
    if fmt == "WEBP":
        return "Webp"
    if fmt == "DIB":
        return "PNG"
    return fmt


def fmt_size(n):
    if n < 1024:
        return f"{n} B"
    elif n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


# ===========================================================================
# AmbientLightBar — 全宽环境灯带
# 空闲时缓慢呼吸流转；按键从对应位置泛起彩色波纹；操作时整条浪涌。
# ===========================================================================

# 键盘字符 → 灯带横向位置（模拟机械键盘 RGB 灯效）
_KEY_ROW_POS = {}
for _i, _ch in enumerate("1234567890-=qwertyuiop[]asdfghjkl;'zxcvbnm,./"):
    _KEY_ROW_POS[_ch] = _i / 44.0


class AmbientLightBar(tk.Canvas):
    """Canvas 灯带：把宽度切成若干段，每段独立着色。

    - 基色：色相沿横向铺开并随时间缓慢漂移，亮度按 ~4s 周期呼吸
    - pulse(hue, x)：从 x 处扩散的彩色波纹（按键/捕获）
    - surge(hue)：整条灯带短暂亮起指定颜色（复制=绿 / 删除=红 / 置顶=琥珀）
    颜色按 16 级量化缓存，未变化的段跳过重绘，空窗时自动暂停。
    """

    SEG_W = 9          # 每段像素宽
    HEIGHT = 4
    FPS_MS = 33        # ~30fps

    def __init__(self, parent):
        super().__init__(parent, height=self.HEIGHT, bg=BG,
                         highlightthickness=0, bd=0)
        self._segs = []          # canvas item ids
        self._seg_hex = []       # 上次颜色（量化后）
        self._n = 0
        self._pulses = []        # [x01, hue, t0, strength]
        self._surge = 0.0        # 全局浪涌强度 0..1
        self._surge_hue = 210.0
        self._t0 = time.perf_counter()
        self._last_tick = self._t0
        self.suppress_until = 0.0    # 滚动期间暂停动画，把主线程让给列表
        self.bind("<Configure>", self._on_configure)
        self.after(self.FPS_MS, self._tick)

    # ---- 对外 API ----

    def pulse(self, hue, x=None, strength=1.0):
        """在灯带 x(0..1) 处激起一圈 hue 色波纹。"""
        if x is None:
            x = random.uniform(0.15, 0.85)
        self._pulses.append([float(x), float(hue) % 360.0,
                             time.perf_counter() - self._t0, strength])
        if len(self._pulses) > 14:
            del self._pulses[:len(self._pulses) - 14]

    def surge(self, hue, amount=1.0):
        """整条灯带浪涌（操作反馈）。"""
        self._surge = max(self._surge, min(1.0, amount))
        self._surge_hue = float(hue) % 360.0

    def key_light(self, keysym, char):
        """按键 → 波纹：字符映射到键盘横向位置，不同键不同色相。"""
        ch = (char or "").lower()
        x = _KEY_ROW_POS.get(ch)
        if x is None:
            x = {"space": 0.5, "return": 0.94, "backspace": 0.06,
                 "delete": 0.97, "escape": 0.02, "tab": 0.04}.get(keysym.lower())
        if x is None:
            x = random.uniform(0.05, 0.95)
        hue = (abs(hash(keysym)) * 137.508) % 360.0   # 黄金角散布色相
        self.pulse(hue, x, strength=0.95)

    # ---- 内部 ----

    def _on_configure(self, event=None):
        w = self.winfo_width()
        if w < 20:
            return
        n = max(8, w // self.SEG_W)
        if n == self._n:
            return
        self.delete("all")
        self._segs = []
        self._seg_hex = [""] * n
        for i in range(n):
            x0, x1 = i * self.SEG_W, (i + 1) * self.SEG_W + 1
            item = self.create_rectangle(x0, 0, x1, self.HEIGHT,
                                         fill=BG, outline="")
            self._segs.append(item)
        self._n = n

    def _tick(self):
        try:
            if not self.winfo_exists():
                return
            top = self.winfo_toplevel()
            # 窗口最小化时暂停渲染
            if top.state() == "iconic":
                self.after(250, self._tick)
                return
            now = time.perf_counter()
            # 滚动期间暂停动画帧，保证列表滚动满帧
            if now < self.suppress_until:
                self._last_tick = now
                self.after(self.FPS_MS, self._tick)
                return
            dt = now - self._last_tick
            self._last_tick = now
            t = now - self._t0

            # 浪涌衰减
            if self._surge > 0.001:
                self._surge *= math.exp(-dt * 2.6)
            else:
                self._surge = 0.0

            # 清理过期波纹
            life = 1.15
            self._pulses = [p for p in self._pulses if t - p[2] < life]

            n = self._n
            if n == 0:
                self.after(self.FPS_MS, self._tick)
                return

            # 呼吸：亮度在 0.13~0.24 间缓慢起伏
            breath = 0.5 + 0.5 * math.sin(t * 2.0 * math.pi / 4.2)
            base_l = 0.13 + 0.11 * breath
            drift = t * 9.0                      # 色相漂移速度
            surge_rgb = None
            if self._surge > 0.0:
                surge_rgb = colorsys.hls_to_rgb(self._surge_hue / 360.0, 0.55, 0.9)

            for i in range(n):
                x = i / (n - 1) if n > 1 else 0.5
                hue = (drift + x * 46.0) % 360.0
                r, g, b = colorsys.hls_to_rgb(hue / 360.0, base_l, 0.62)
                # 波纹叠加
                for px, phue, pt0, pstr in self._pulses:
                    age = t - pt0
                    ring = age * 1.5             # 波纹传播速度（整带/秒）
                    d = abs(x - px)
                    glow = math.exp(-((d - ring) ** 2) / 0.0162) * (1.0 - age / life) * pstr
                    if glow > 0.02:
                        pr, pg, pb = colorsys.hls_to_rgb(phue / 360.0, 0.58, 0.95)
                        r += pr * glow * 0.85
                        g += pg * glow * 0.85
                        b += pb * glow * 0.85
                # 浪涌叠加
                if surge_rgb:
                    k = self._surge * 0.8
                    r += surge_rgb[0] * k
                    g += surge_rgb[1] * k
                    b += surge_rgb[2] * k
                hexc = "#%02x%02x%02x" % (
                    min(255, int(r * 255)) >> 4 << 4,
                    min(255, int(g * 255)) >> 4 << 4,
                    min(255, int(b * 255)) >> 4 << 4)
                if hexc != self._seg_hex[i]:
                    self._seg_hex[i] = hexc
                    self.itemconfig(self._segs[i], fill=hexc)

            self.after(self.FPS_MS, self._tick)
        except tk.TclError:
            return


# ===========================================================================
# GUI
# ===========================================================================

class YouBoardApp:

    def __init__(self, store, monitor=None):
        self.store = store
        self.monitor = monitor
        self._active_type = "text"

        self._trees = {}
        self._tree_to_type = {}
        self._tab_ids = {}
        self._tab_counts = {}
        self._iid_to_hash = {"text": {}, "image": {}, "file": {}, "url": {}}
        self._search_vars = {}
        self._search_entries = {}
        self._search_after = {}
        self._count_labels = {}
        self._empty_labels = {}
        self._sort_orders = {"text": "default", "image": "default", "file": "default", "url": "default"}
        self._sort_ids = {}
        self._sort_combos = {}

        # 性能相关
        self._entry_index = {}          # hash -> entry，O(1) 查找
        self._pinned_hashes = set()
        self._sel_set = {"text": set(), "image": set(), "file": set(), "url": set()}
        self._hover_iid = {"text": None, "image": None, "file": None, "url": None}
        self._last_hover_t = 0.0        # 悬停节流
        self._hover_pending = None
        self._hover_after = None
        self._scroll_suppress_until = 0.0   # 滚轮期间抑制悬停重绘
        self._preview_after = None
        self._resize_after = None
        self._preview_gen = 0
        self._ui_queue = queue.Queue()
        self._in_refresh = False
        self._hist_ids = []

        # 预览状态
        self._preview_photo = None
        self._thumb_photo = None
        self._cached_pil = None
        self._cached_path = None
        self._cur_image_path = None
        self._cur_image_entry = None
        self._cur_text_entry = None
        self._last_render_key = None

        self._status_timer = None
        self._dot_phase = 0
        self._last_self_copy = 0.0
        self._tray = None              # 系统托盘（run 时启动）
        self.restart_flag = False       # True = 关闭后以新语言重建界面

        self.root = tk.Tk()
        self.root.title(tr("win_title"))
        self.root.geometry("1180x720")
        self.root.minsize(920, 540)
        self.root.configure(bg=BG)

        # 窗口 / 任务栏图标
        try:
            if LOGO_ICO and os.path.exists(LOGO_ICO):
                self.root.iconbitmap(LOGO_ICO)
        except Exception:
            pass

        # 页头 Logo 图像
        self._logo_photo = None
        try:
            if HAS_PIL and LOGO_ICO and os.path.exists(LOGO_ICO):
                _im = Image.open(LOGO_ICO)
                _im = _im.resize((34, 34), Image.LANCZOS)
                self._logo_photo = ImageTk.PhotoImage(_im)
        except Exception:
            self._logo_photo = None

        self._setup_styles()
        self._build_ui()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(150, self._initial_refresh)
        self.root.after(250, self._focus_search)
        self.root.after(100, self._poll_ui_queue)
        self._animate_dot()

    # ------------------------------------------------------------------
    # 样式
    # ------------------------------------------------------------------

    def _setup_styles(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass

        style.configure("TFrame", background=SURFACE)
        style.configure("Card.TFrame", background=SURFACE2)
        style.configure("TLabel", background=SURFACE, foreground=TEXT, font=F_UI)

        # Treeview
        style.configure("Treeview", background=SURFACE, fieldbackground=SURFACE,
                        foreground=TEXT, rowheight=31, borderwidth=0, relief="flat",
                        font=F_UI)
        style.map("Treeview",
                  background=[("selected", ACCENT_DIM)],
                  foreground=[("selected", "#ffffff")])
        style.configure("Treeview.Heading", background=SURFACE2, foreground=TEXT_SEC,
                        relief="flat", font=F_HEAD, padding=(8, 6), borderwidth=0)
        style.map("Treeview.Heading", background=[("active", SURFACE3)])

        # 滚动条（直接用默认样式名，避免自定义布局缺失）
        style.configure("TScrollbar", background=SURFACE3, troughcolor=SURFACE,
                        bordercolor=SURFACE, arrowcolor=TEXT_MUTED,
                        lightcolor=SURFACE3, darkcolor=SURFACE3, borderwidth=0)
        style.map("TScrollbar", background=[("active", BORDER_LT)])

        # 按钮
        style.configure("Ghost.TButton", background=SURFACE2, foreground=TEXT_SEC,
                        bordercolor=BORDER, darkcolor=BORDER, lightcolor=BORDER,
                        focusthickness=0, focuscolor=SURFACE2, padding=(12, 5), font=F_UI)
        style.map("Ghost.TButton",
                  background=[("pressed", BORDER), ("active", SURFACE3)],
                  foreground=[("active", TEXT)],
                  bordercolor=[("active", BORDER_LT)])
        style.configure("Accent.TButton", background=ACCENT, foreground="#0c1420",
                        bordercolor=ACCENT, darkcolor=ACCENT, lightcolor=ACCENT,
                        focusthickness=0, focuscolor=ACCENT, padding=(14, 5), font=F_UI_B)
        style.map("Accent.TButton",
                  background=[("pressed", "#3b87e0"), ("active", ACCENT_HV)],
                  bordercolor=[("active", ACCENT_HV)])
        style.configure("Warn.TButton", background=SURFACE2, foreground=DANGER,
                        bordercolor=BORDER, darkcolor=BORDER, lightcolor=BORDER,
                        focusthickness=0, focuscolor=SURFACE2, padding=(12, 5), font=F_UI)
        style.map("Warn.TButton",
                  background=[("pressed", "#3a2226"), ("active", "#33232a")],
                  foreground=[("active", "#ff8d80")],
                  bordercolor=[("active", "#5a3038")])

        # 下拉框
        style.configure("Dark.TCombobox", fieldbackground=SURFACE2, background=SURFACE2,
                        foreground=TEXT, arrowcolor=TEXT_SEC, bordercolor=BORDER,
                        selectbackground=ACCENT_DIM, selectforeground=TEXT,
                        padding=(6, 4), font=F_UI)
        style.map("Dark.TCombobox",
                  fieldbackground=[("readonly", SURFACE2), ("focus", SURFACE2)],
                  background=[("active", SURFACE3)])
        self.root.option_add("*TCombobox*Listbox.background", SURFACE2)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT_DIM)
        self.root.option_add("*TCombobox*Listbox.selectForeground", TEXT)
        self.root.option_add("*TCombobox*Listbox.font", F_UI)

        # Notebook 标签页
        style.configure("TNotebook", background=BG, borderwidth=0, tabmargins=[10, 6, 0, 0])
        style.configure("TNotebook.Tab", background=BG, foreground=TEXT_MUTED,
                        padding=[18, 8], font=F_UI_B, borderwidth=0)
        style.map("TNotebook.Tab",
                  background=[("selected", SURFACE), ("active", SURFACE2)],
                  foreground=[("selected", ACCENT), ("active", TEXT_SEC)],
                  expand=[("selected", [0, 0, 0, 2])])

        # 分割条
        style.configure("TPanedwindow", background=BG)
        style.configure("Sash", sashthickness=6, background=BG, gripcount=0, borderwidth=0)

    # ------------------------------------------------------------------
    # 构建界面
    # ------------------------------------------------------------------

    def _build_ui(self):
        self._build_header()

        # 主体：左 notebook + 右侧栏
        self.pane = ttk.Panedwindow(self.root, orient=tk.HORIZONTAL)
        self.pane.pack(fill=tk.BOTH, expand=True, padx=(10, 10), pady=(0, 6))

        self.notebook = ttk.Notebook(self.pane)
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)
        self.pane.add(self.notebook, weight=1)

        self.sidebar_pane = ttk.Panedwindow(self.pane, orient=tk.VERTICAL)
        self.pane.add(self.sidebar_pane, weight=0)

        self._build_preview_panel()
        self._build_history_panel()

        for etype in ("text", "image", "file", "url"):
            tab_frame = ttk.Frame(self.notebook)
            self.notebook.add(tab_frame, text=self._tab_text(etype, 0))
            self._tab_ids[etype] = tab_frame
            self._build_tab(tab_frame, etype)

        self._build_statusbar()
        self._bind_global_keys()

    def _build_header(self):
        header = tk.Frame(self.root, bg=BG)
        header.pack(fill=tk.X, padx=14, pady=(12, 2))

        # 左侧：Logo + 品牌名 + 呼吸状态点
        left = tk.Frame(header, bg=BG)
        left.pack(side=tk.LEFT)

        if self._logo_photo:
            tk.Label(left, image=self._logo_photo, bg=BG).pack(
                side=tk.LEFT, padx=(0, 12), pady=(0, 6))

        brand_box = tk.Frame(left, bg=BG)
        brand_box.pack(side=tk.LEFT)

        brand_row = tk.Frame(brand_box, bg=BG)
        brand_row.pack(anchor=tk.W)
        # Bahnschrift 缺失时 tkinter 会自动回退到系统字体
        tk.Label(brand_row, text=APP_NAME, bg=BG, fg=TEXT,
                 font=F_BRAND).pack(side=tk.LEFT)
        tk.Label(brand_row, text=tr("brand_sub"), bg=BG, fg=TEXT_SEC,
                 font=F_UI).pack(side=tk.LEFT, padx=(10, 0), pady=(8, 0))

        sub_row = tk.Frame(brand_box, bg=BG)
        sub_row.pack(anchor=tk.W, pady=(2, 0))
        self.dot_canvas = tk.Canvas(sub_row, width=8, height=8, bg=BG,
                                    highlightthickness=0)
        self.dot_canvas.pack(side=tk.LEFT, padx=(2, 6))
        self.dot_canvas.create_oval(0, 0, 8, 8, fill=SUCCESS, outline="", tags="dot")
        tk.Label(sub_row, text=" ".join("CLIPBOARD HISTORY"), bg=BG,
                 fg=TEXT_MUTED, font=F_SUB).pack(side=tk.LEFT)

        # 右侧：管理菜单 + 设置 + 统计 + 监控状态
        self.manage_btn = tk.Button(
            header, text=tr("manage"), bg=SURFACE2, fg=TEXT_SEC, activebackground=SURFACE3,
            activeforeground=TEXT, relief=tk.FLAT, font=F_UI, cursor="hand2",
            padx=10, pady=4, command=self._show_manage_menu)
        self.manage_btn.pack(side=tk.RIGHT, padx=(10, 0))

        self.settings_btn = tk.Button(
            header, text=tr("settings_btn"), bg=SURFACE2, fg=TEXT_SEC,
            activebackground=SURFACE3, activeforeground=TEXT, relief=tk.FLAT,
            font=F_UI, cursor="hand2", padx=10, pady=4,
            command=self._open_settings)
        self.settings_btn.pack(side=tk.RIGHT)

        self.header_count_var = tk.StringVar(value=tr("total_records", n=0))
        tk.Label(header, textvariable=self.header_count_var, bg=BG, fg=TEXT_SEC,
                 font=F_UI).pack(side=tk.RIGHT, padx=(0, 6))

        self.monitor_var = tk.StringVar(
            value=tr("monitor_live") if self.monitor else tr("monitor_off"))
        tk.Label(header, textvariable=self.monitor_var, bg=BG, fg=SUCCESS,
                 font=F_SMALL).pack(side=tk.RIGHT, padx=(0, 14))

        # 全宽环境灯带（呼吸流转 + 按键波纹 + 操作浪涌）
        self.lightbar = AmbientLightBar(self.root)
        self.lightbar.pack(fill=tk.X, padx=0, pady=(10, 0))

    def _build_preview_panel(self):
        self.preview_container = ttk.LabelFrame(
            self.sidebar_pane, text=tr("panel_preview"), padding=(8, 6),
            style="Card.TLabelframe")
        style = ttk.Style(self.root)
        style.configure("Card.TLabelframe", background=SURFACE2, relief="solid",
                        borderwidth=1, bordercolor=BORDER)
        style.configure("Card.TLabelframe.Label", background=SURFACE2,
                        font=F_UI_B, foreground=ACCENT)
        self.preview_container.bind("<Configure>", self._on_preview_resize)

        # 可滚动预览区域：Canvas + 滚动条 + 鼠标滚轮
        self._preview_canvas = tk.Canvas(
            self.preview_container, bg=SURFACE2, highlightthickness=0, bd=0)
        self._preview_scrollbar = ttk.Scrollbar(
            self.preview_container, orient=tk.VERTICAL,
            command=self._preview_canvas.yview)
        self._preview_canvas.configure(yscrollcommand=self._preview_scrollbar.set)
        self._preview_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._preview_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.preview_inner = ttk.Frame(self._preview_canvas, style="Card.TFrame")
        self._preview_window_id = self._preview_canvas.create_window(
            (0, 0), window=self.preview_inner, anchor="nw")

        # 内部内容尺寸变化时更新滚动区域
        self.preview_inner.bind("<Configure>", self._on_preview_inner_configure)
        self._preview_canvas.bind("<Configure>", self._on_preview_canvas_resize)
        # 鼠标滚轮绑定
        self._preview_canvas.bind("<Enter>", self._bind_preview_mousewheel)
        self._preview_canvas.bind("<Leave>", self._unbind_preview_mousewheel)

        self._show_preview_placeholder()
        self.sidebar_pane.add(self.preview_container, weight=1)

    def _build_history_panel(self):
        hist_frame = ttk.LabelFrame(self.sidebar_pane, text=tr("panel_snapshots"),
                                    padding=(8, 6), style="Card.TLabelframe")

        list_frame = ttk.Frame(hist_frame, style="Card.TFrame")
        list_frame.pack(fill=tk.BOTH, expand=True)

        self.hist_listbox = tk.Listbox(
            list_frame, font=F_UI, relief=tk.FLAT, borderwidth=0, bg=SURFACE2,
            fg=TEXT, selectbackground=ACCENT_DIM, selectforeground="#ffffff",
            activestyle="none", height=6, highlightthickness=0)
        hist_scroll = ttk.Scrollbar(list_frame,
                                    command=self.hist_listbox.yview)
        self.hist_listbox.configure(yscrollcommand=hist_scroll.set)
        self.hist_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        hist_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.hist_listbox.bind("<Double-1>", lambda e: self._restore_history())

        btns = ttk.Frame(hist_frame, style="Card.TFrame")
        btns.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(btns, text=tr("btn_restore"), command=self._restore_history,
                   style="Ghost.TButton").pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(btns, text=tr("btn_clear_history"), command=self._clear_history,
                   style="Warn.TButton").pack(side=tk.RIGHT)

        self.sidebar_pane.add(hist_frame, weight=1)

    def _build_statusbar(self):
        tk.Frame(self.root, bg=BORDER, height=1).pack(side=tk.TOP, fill=tk.X)
        bar = tk.Frame(self.root, bg=SURFACE)
        bar.pack(side=tk.BOTTOM, fill=tk.X)

        self.hint_var = tk.StringVar()
        tk.Label(bar, textvariable=self.hint_var, bg=SURFACE, fg=TEXT_MUTED,
                 font=F_SMALL, anchor=tk.W).pack(side=tk.LEFT, padx=12, pady=6)

        self.status_var = tk.StringVar()
        self.status_label = tk.Label(bar, textvariable=self.status_var, bg=SURFACE,
                                     fg=TEXT_SEC, font=F_UI_B, anchor=tk.E)
        self.status_label.pack(side=tk.RIGHT, padx=12, pady=6)

        self.sel_count_var = tk.StringVar()
        tk.Label(bar, textvariable=self.sel_count_var, bg=SURFACE, fg=ACCENT,
                 font=F_UI_B).pack(side=tk.RIGHT, padx=(0, 16))

    def _bind_global_keys(self):
        self.root.bind("<F5>", lambda e: self._refresh_all())
        self.root.bind("<Control-a>", lambda e: self._select_all())
        self.root.bind("<Control-o>", lambda e: self._open_selected())
        self.root.bind("<Control-e>", lambda e: self._export_selected())
        # 按键 → 灯带彩色波纹
        self.root.bind("<Key>", self._on_any_key, add="+")
        # 全局滚轮转发：窗口任意位置滚动都能翻动列表
        self.root.bind("<MouseWheel>", self._on_global_wheel)

    _MODIFIER_KEYS = frozenset({
        "Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R",
        "Caps_Lock", "Num_Lock", "Scroll_Lock", "Win_L", "Win_R",
        "Alt_Gr", "Super_L", "Super_R",
    })

    def _on_tree_wheel(self, event):
        """指针在列表上的原生滚动：不拦截事件，只开启滚动期抑制。"""
        self._scroll_suppress_until = time.perf_counter() + 0.2
        lb = getattr(self, "lightbar", None)
        if lb is not None:
            lb.suppress_until = time.perf_counter() + 0.25

    def _on_any_key(self, event):
        if event.keysym in self._MODIFIER_KEYS:
            return
        try:
            self.lightbar.key_light(event.keysym, event.char)
        except Exception:
            pass

    def _on_global_wheel(self, event):
        """滚轮转发 + 滚动期悬停抑制。

        指针在列表上：交给 Treeview 原生滚动；
        在预览/历史的 Text、Listbox 上：滚动它们；
        在其它区域（搜索栏、空白等）：转发给当前列表。
        """
        self._scroll_suppress_until = time.perf_counter() + 0.18
        lb = getattr(self, "lightbar", None)
        if lb is not None:
            lb.suppress_until = time.perf_counter() + 0.25
        units = -1 if event.delta > 0 else 1
        try:
            w = self.root.winfo_containing(event.x_root, event.y_root)
        except Exception:
            w = None
        cur = w
        while cur is not None:
            if cur in self._tree_to_type:
                return                      # 列表原生处理
            if isinstance(cur, (tk.Text, tk.Listbox)):
                cur.yview_scroll(units, "units")
                return "break"
            cur = getattr(cur, "master", None)
        tree = self._get_active_tree()
        if tree is not None:
            # 滚动时清掉过时的悬停高亮
            etype = self._active_type
            old = self._hover_iid.get(etype)
            if old:
                self._hover_iid[etype] = None
                self._retag(etype, old)
            tree.yview_scroll(units, "units")

    # ------------------------------------------------------------------
    # 标签页构建
    # ------------------------------------------------------------------

    @staticmethod
    def _type_label(etype):
        return {"text": tr("type_text"), "image": tr("type_image"),
                "file": tr("type_file"), "url": tr("type_url")}[etype]

    def _tab_text(self, etype, count):
        return f"  {TAB_ICONS[etype]}  {self._type_label(etype)}  {count}  "

    def _build_tab(self, parent, etype):
        # 搜索行
        search_frame = ttk.Frame(parent, padding=(6, 8, 6, 4))
        search_frame.pack(fill=tk.X)

        box = tk.Frame(search_frame, bg=SURFACE2, highlightthickness=1,
                       highlightbackground=BORDER, highlightcolor=ACCENT)
        box.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))
        tk.Label(box, text="\U0001f50d", bg=SURFACE2, fg=TEXT_MUTED, font=F_UI).pack(
            side=tk.LEFT, padx=(8, 2))
        sv = tk.StringVar()
        sv.trace_add("write", lambda *a, t=etype: self._debounce_search(t))
        self._search_vars[etype] = sv
        entry = tk.Entry(box, textvariable=sv, bg=SURFACE2, fg=TEXT, font=F_UI,
                         relief=tk.FLAT, insertbackground=ACCENT, bd=0)
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 8), ipady=5)
        entry.bind("<Return>", lambda e: self._copy_selected())
        entry.bind("<Escape>", lambda e: (sv.set(""), self._focus_search()))
        self._search_entries[etype] = entry

        sort_ids = (["default", "oldest"] if etype in ("text", "url") else
                    ["default", "oldest", "name_az", "name_za",
                     "fmt_az", "fmt_za", "size_desc", "size_asc"])
        sort_labels = [tr("sort_" + sid) for sid in sort_ids]
        combo = ttk.Combobox(search_frame, values=sort_labels, state="readonly",
                             width=17, style="Dark.TCombobox")
        combo.set(sort_labels[0])
        combo.bind("<<ComboboxSelected>>",
                   lambda e, t=etype, c=combo: self._on_sort_changed(t, c))
        combo.pack(side=tk.RIGHT, padx=(6, 0))
        self._sort_ids[etype] = sort_ids
        self._sort_combos[etype] = combo

        count_label = ttk.Label(search_frame, text="", foreground=TEXT_MUTED, font=F_SMALL)
        count_label.pack(side=tk.RIGHT, padx=(0, 8))
        self._count_labels[etype] = count_label

        # 操作行
        act = ttk.Frame(parent, padding=(6, 0, 6, 6))
        act.pack(fill=tk.X)
        ttk.Button(act, text=tr("btn_copy"), command=self._copy_selected,
                   style="Accent.TButton").pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(act, text=tr("btn_pin"), command=self._pin_selected,
                   style="Ghost.TButton").pack(side=tk.LEFT, padx=2)
        ttk.Button(act, text=tr("btn_unpin"), command=self._unpin_selected,
                   style="Ghost.TButton").pack(side=tk.LEFT, padx=2)
        ttk.Button(act, text=tr("btn_delete"), command=self._delete_selected,
                   style="Ghost.TButton").pack(side=tk.LEFT, padx=2)
        ttk.Button(act, text=tr("btn_export"), command=self._export_selected,
                   style="Ghost.TButton").pack(side=tk.RIGHT)
        if etype in ("image", "file", "url"):
            ttk.Button(act, text=tr("btn_open"), command=self._open_selected,
                       style="Ghost.TButton").pack(side=tk.RIGHT, padx=4)

        # 列表
        tv_frame = ttk.Frame(parent, padding=(6, 0, 6, 8))
        tv_frame.pack(fill=tk.BOTH, expand=True)

        if etype == "text":
            cols = ("#", "status", "time", "preview")
            tv = ttk.Treeview(tv_frame, columns=cols, show="headings", selectmode="extended")
            tv.heading("#", text="#");        tv.column("#", width=44, anchor=tk.CENTER, stretch=False)
            tv.heading("status", text="");    tv.column("status", width=34, anchor=tk.CENTER, stretch=False)
            tv.heading("time", text=tr("col_time"));  tv.column("time", width=150, anchor=tk.CENTER, stretch=False)
            tv.heading("preview", text=tr("col_preview")); tv.column("preview", width=420, stretch=True)
        elif etype == "url":
            cols = ("#", "status", "time", "url")
            tv = ttk.Treeview(tv_frame, columns=cols, show="headings", selectmode="extended")
            tv.heading("#", text="#");        tv.column("#", width=44, anchor=tk.CENTER, stretch=False)
            tv.heading("status", text="");    tv.column("status", width=34, anchor=tk.CENTER, stretch=False)
            tv.heading("time", text=tr("col_time"));  tv.column("time", width=150, anchor=tk.CENTER, stretch=False)
            tv.heading("url", text=tr("col_url")); tv.column("url", width=420, stretch=True)
        elif etype == "image":
            cols = ("#", "status", "time", "filename", "fmt", "dims", "size")
            tv = ttk.Treeview(tv_frame, columns=cols, show="headings", selectmode="extended")
            tv.heading("#", text="#");        tv.column("#", width=44, anchor=tk.CENTER, stretch=False)
            tv.heading("status", text="");    tv.column("status", width=34, anchor=tk.CENTER, stretch=False)
            tv.heading("time", text=tr("col_time"));  tv.column("time", width=140, anchor=tk.CENTER, stretch=False)
            tv.heading("filename", text=tr("col_filename")); tv.column("filename", width=150, stretch=True)
            tv.heading("fmt", text=tr("col_format"));   tv.column("fmt", width=58, anchor=tk.CENTER, stretch=False)
            tv.heading("dims", text=tr("col_dims"));  tv.column("dims", width=92, anchor=tk.CENTER, stretch=False)
            tv.heading("size", text=tr("col_size"));  tv.column("size", width=74, anchor=tk.CENTER, stretch=False)
        else:
            cols = ("#", "status", "time", "count", "fmt", "size", "files")
            tv = ttk.Treeview(tv_frame, columns=cols, show="headings", selectmode="extended")
            tv.heading("#", text="#");        tv.column("#", width=44, anchor=tk.CENTER, stretch=False)
            tv.heading("status", text="");    tv.column("status", width=34, anchor=tk.CENTER, stretch=False)
            tv.heading("time", text=tr("col_time"));  tv.column("time", width=132, anchor=tk.CENTER, stretch=False)
            tv.heading("count", text=tr("col_count")); tv.column("count", width=46, anchor=tk.CENTER, stretch=False)
            tv.heading("fmt", text=tr("col_format"));   tv.column("fmt", width=62, anchor=tk.CENTER, stretch=False)
            tv.heading("size", text=tr("col_size"));  tv.column("size", width=74, anchor=tk.CENTER, stretch=False)
            tv.heading("files", text=tr("col_files")); tv.column("files", width=320, stretch=True)

        scrollbar = ttk.Scrollbar(tv_frame, orient=tk.VERTICAL,
                                  command=tv.yview)
        tv.configure(yscrollcommand=scrollbar.set)
        tv.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        tv.tag_configure("even", background=SURFACE)
        tv.tag_configure("odd", background=ROW_ALT)
        tv.tag_configure("pinned", background=PIN_BG)
        tv.tag_configure("sel", background=ACCENT_DIM, foreground="#ffffff")
        tv.tag_configure("hover", background=SURFACE3)
        tv.tag_configure("flash", background=FLASH_BG, foreground="#ffffff")

        tv.bind("<Double-1>", self._on_tree_double_click)
        tv.bind("<Return>", lambda e: self._copy_selected())
        tv.bind("<Delete>", lambda e: self._delete_selected())
        tv.bind("<space>", lambda e: self._toggle_pin_selected())
        tv.bind("<Escape>", lambda e: self._focus_search())
        tv.bind("<Button-3>", self._on_right_click)
        tv.bind("<<TreeviewSelect>>", self._on_selection_changed)
        tv.bind("<Motion>", lambda e, t=etype: self._on_tree_motion(t, e))
        tv.bind("<Leave>", lambda e, t=etype: self._on_tree_leave(t))
        tv.bind("<MouseWheel>", self._on_tree_wheel, add="+")

        # 空状态提示（覆盖在列表上方）
        empty = tk.Label(tv_frame, text=tr("empty_state"),
                         bg=SURFACE, fg=TEXT_MUTED, font=F_UI, justify=tk.CENTER)
        empty.place(relx=0.5, rely=0.42, anchor=tk.CENTER)
        empty.lower()
        self._empty_labels[etype] = empty

        self._trees[etype] = tv
        self._tree_to_type[tv] = etype

    # ------------------------------------------------------------------
    # 行标签（悬停 / 选中 / 置顶 / 斑马纹），O(1) 局部更新
    # ------------------------------------------------------------------

    def _retag(self, etype, iid):
        tree = self._trees[etype]
        if not iid or iid not in self._iid_to_hash[etype]:
            return
        tags = []
        if self._hover_iid[etype] == iid:
            tags.append("hover")
        if iid in self._sel_set[etype]:
            tags.append("sel")
        h = self._iid_to_hash[etype][iid]
        if h in self._pinned_hashes:
            tags.append("pinned")
        tags.append("odd" if int(iid) % 2 else "even")
        try:
            tree.item(iid, tags=tags)
        except tk.TclError:
            pass

    def _on_tree_motion(self, etype, event):
        # 节流：最多 ~25fps 处理悬停；滚动期间完全跳过，避免重绘卡顿
        now = time.perf_counter()
        if now < self._scroll_suppress_until:
            return
        if now - self._last_hover_t < 0.04:
            self._hover_pending = (etype, event.y)
            if self._hover_after is None:
                self._hover_after = self.root.after(45, self._flush_hover)
            return
        self._last_hover_t = now
        self._do_hover(etype, event.y)

    def _flush_hover(self):
        self._hover_after = None
        if self._hover_pending is not None:
            etype, y = self._hover_pending
            self._hover_pending = None
            self._last_hover_t = time.perf_counter()
            self._do_hover(etype, y)

    def _do_hover(self, etype, y):
        tree = self._trees.get(etype)
        if not tree:
            return
        iid = tree.identify_row(y) or None
        old = self._hover_iid[etype]
        if iid != old:
            self._hover_iid[etype] = iid
            if old:
                self._retag(etype, old)
            if iid:
                self._retag(etype, iid)

    def _on_tree_leave(self, etype):
        old = self._hover_iid[etype]
        if old:
            self._hover_iid[etype] = None
            self._retag(etype, old)

    def _on_selection_changed(self, event=None):
        if self._in_refresh:
            return
        w = getattr(event, "widget", None)
        etype = self._tree_to_type.get(w, self._active_type)
        tree = self._trees[etype]
        new_sel = set(tree.selection())
        old_sel = self._sel_set[etype]
        for iid in old_sel - new_sel:
            self._retag(etype, iid)
        for iid in new_sel - old_sel:
            self._retag(etype, iid)
        self._sel_set[etype] = new_sel

        if etype == self._active_type:
            n = len(new_sel)
            self.sel_count_var.set(tr("selected_n", n=n) if n > 1 else "")
            self._schedule_preview()

    # ------------------------------------------------------------------
    # 状态栏
    # ------------------------------------------------------------------

    def _set_status(self, msg, kind="info"):
        if self._status_timer is not None:
            self.root.after_cancel(self._status_timer)
        color = {"ok": SUCCESS, "err": DANGER, "warn": AMBER}.get(kind, TEXT_SEC)
        self.status_label.configure(fg=color)
        self.status_var.set(msg)
        self._status_timer = self.root.after(4500, self._clear_status)
        # 灯带浪涌：绿=成功 红=错误 琥珀=警告 蓝=普通
        lb = getattr(self, "lightbar", None)
        if lb is not None:
            hue = {"ok": 140.0, "err": 4.0, "warn": 38.0}.get(kind, 215.0)
            lb.surge(hue, 0.85 if kind in ("ok", "err") else 0.5)

    def _clear_status(self):
        self.status_var.set("")
        self._status_timer = None

    def _update_hint(self):
        hints = {
            "text": tr("hint_text"),
            "image": tr("hint_image"),
            "file": tr("hint_file"),
            "url": tr("hint_url"),
        }
        self.hint_var.set(hints.get(self._active_type, ""))

    # ------------------------------------------------------------------
    # 刷新
    # ------------------------------------------------------------------

    def _rebuild_index(self):
        idx = {}
        pinned = set()
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
        # 启动欢迎灯效：一道蓝色浪涌 + 三点涟漪
        try:
            self.lightbar.surge(210.0, 0.8)
            for i, x in enumerate((0.2, 0.5, 0.8)):
                self.root.after(180 * i, lambda x=x: self.lightbar.pulse(
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
        if self._search_after.get(etype):
            self.root.after_cancel(self._search_after[etype])
        self._search_after[etype] = self.root.after(160, lambda: self._refresh_tab(etype))

    def _refresh_tab(self, etype):
        tree = self._trees.get(etype)
        if not tree:
            return

        self._rebuild_index()
        old_selected = {self._iid_to_hash[etype][iid] for iid in tree.selection()
                        if iid in self._iid_to_hash[etype]}

        kw = self._search_vars.get(etype, tk.StringVar()).get().strip().lower()
        entries = self.store.search(kw, etype) if kw else self.store.get_by_type(etype)
        entries = self._apply_sort(etype, entries)

        total_all = len(entries)
        shown = entries[:DISPLAY_LIMIT]

        self._in_refresh = True
        try:
            tree.delete(*tree.get_children())
        finally:
            self._in_refresh = False
        iid_map = {}
        for i, entry in enumerate(shown):
            ts = entry.get("timestamp", "")
            try:
                time_str = datetime.fromisoformat(ts).strftime(TIME_FORMAT)
            except ValueError:
                time_str = ts[:19] if len(ts) >= 19 else ts

            is_pin = entry["hash"] in self._pinned_hashes
            status = "\U0001f4cc" if is_pin else ""
            base = ("pinned",) if is_pin else ()

            if etype == "text":
                content = entry.get("content", "")
                preview = content[:120].replace("\n", " ⏎ ").replace("\t", "  ")
                if len(content) > 120:
                    preview += "…"
                values = (i + 1, status, time_str, preview)
            elif etype == "url":
                content = entry.get("content", "")
                values = (i + 1, status, time_str, content)
            elif etype == "image":
                src = entry.get("source_name", "")
                fn = src if src else os.path.basename(entry.get("filename", ""))
                values = (i + 1, status, time_str, fn,
                          fmt_image_type(entry.get("original_format", "?")),
                          f"{entry.get('width', '?')}x{entry.get('height', '?')}",
                          fmt_size(entry.get("file_size", 0)))
            else:
                paths = entry.get("file_paths", [])
                sizes = entry.get("file_sizes", [])
                total_sz = sum(s for s in sizes if s > 0) if sizes else 0
                fp = "  |  ".join(os.path.basename(p) for p in paths[:6])
                if len(paths) > 6:
                    fp += f"  …(+{len(paths) - 6})"
                values = (i + 1, status, time_str,
                          entry.get("file_count", len(paths)),
                          self._extract_extensions(paths),
                          fmt_size(total_sz) if total_sz > 0 else "?", fp)

            iid = str(i)
            iid_map[iid] = entry["hash"]
            tree.insert("", tk.END, iid=iid, values=values,
                        tags=base + ("odd" if i % 2 else "even",))

        self._iid_to_hash[etype] = iid_map
        self._sel_set[etype] = set()
        self._hover_iid[etype] = None

        for iid, h in iid_map.items():
            if h in old_selected:
                tree.selection_add(iid)

        # 空状态提示
        empty = self._empty_labels.get(etype)
        if empty:
            if total_all == 0 and not kw:
                empty.lift()
            else:
                empty.lower()

        # 计数标签
        pin_n = self.store.pinned_count(etype)
        cl = self._count_labels.get(etype)
        if cl:
            if kw:
                cl.config(text=tr("count_match", n=total_all))
            elif total_all > DISPLAY_LIMIT:
                cl.config(text=tr("count_shown", shown=DISPLAY_LIMIT,
                                  total=total_all, pinned=pin_n))
            else:
                cl.config(text=tr("count_total", total=total_all, pinned=pin_n))

        self._update_tab_badge(etype)
        self._update_header_stats()

    def _update_tab_badge(self, etype):
        n = self.store.count(etype)
        if self._tab_counts.get(etype) != n:
            self._tab_counts[etype] = n
            self.notebook.tab(self._tab_ids[etype], text=self._tab_text(etype, n))

    def _update_header_stats(self):
        self.header_count_var.set(tr("total_records", n=self.store.count()))

    # ------------------------------------------------------------------
    # 排序
    # ------------------------------------------------------------------

    def _on_sort_changed(self, etype, combo):
        idx = combo.current()
        ids = self._sort_ids.get(etype, [])
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
                return self._filename_sort_key(name)
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

    @staticmethod
    def _filename_sort_key(name):
        """中文(0) > 英文(1) > 数字(2) > 其他(3)，中文内按拼音。"""
        if not name:
            return (4, "")
        ch = name[0]
        if '一' <= ch <= '鿿' or '㐀' <= ch <= '䶿' or '豈' <= ch <= '﫿':
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

    @staticmethod
    def _extract_extensions(paths):
        seen = set()
        for p in paths:
            ext = os.path.splitext(p)[1].lstrip(".").upper()
            seen.add(ext if ext else tr("no_ext"))
        return ", ".join(sorted(seen)) if seen else "?"

    # ------------------------------------------------------------------
    # 历史快照
    # ------------------------------------------------------------------

    def _refresh_history_list(self):
        self.hist_listbox.delete(0, tk.END)
        snaps = list(reversed(self.store.get_snapshots()))
        self._hist_ids = []
        for snap in snaps[:HIST_DISPLAY]:
            ts = snap.get("time", "")
            try:
                ts_str = datetime.fromisoformat(ts).strftime("%m-%d %H:%M:%S")
            except ValueError:
                ts_str = ts[:16]
            self.hist_listbox.insert(tk.END, f" {ts_str}   {snap.get('desc', '?')}")
            self._hist_ids.append(snap["id"])

    def _get_selected_history_id(self):
        sel = self.hist_listbox.curselection()
        if sel and sel[0] < len(self._hist_ids):
            return self._hist_ids[sel[0]]
        return None

    def _restore_history(self):
        sid = self._get_selected_history_id()
        if not sid:
            self._set_status(tr("snap_select_first"), "warn")
            return
        snap = next((s for s in self.store.get_snapshots() if s["id"] == sid), None)
        if not snap:
            return
        ts = snap.get("time", "")[:19]
        if not messagebox.askyesno(
                tr("dlg_confirm_restore"),
                tr("msg_restore_confirm", ts=ts, desc=snap.get("desc", "?"))):
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
        if not messagebox.askyesno(tr("dlg_confirm_clear"),
                                   tr("msg_clear_history", n=len(snaps))):
            return
        self.store.clear_snapshots()
        self._refresh_history_list()
        self._set_status(tr("st_history_cleared"), "ok")

    # ------------------------------------------------------------------
    # 标签页切换 / 快捷键
    # ------------------------------------------------------------------

    def _on_tab_changed(self, event=None):
        idx = self.notebook.index("current")
        types = ("text", "image", "file", "url")
        if idx < len(types):
            self._active_type = types[idx]
            self._refresh_tab(self._active_type)
            self._update_preview()
            self._update_hint()
            self._focus_search()

    def _get_active_tree(self):
        return self._trees.get(self._active_type)

    def _focus_search(self):
        entry = self._search_entries.get(self._active_type)
        if entry:
            entry.focus_set()
            entry.selection_range(0, tk.END)

    def _select_all(self):
        focused = self.root.focus_get()
        if isinstance(focused, (tk.Entry, tk.Text)):
            return  # 输入框内的 Ctrl+A 交给输入框自己处理
        tree = self._get_active_tree()
        if tree:
            tree.selection_set(tree.get_children())

    # ------------------------------------------------------------------
    # 预览（防抖 + 异步加载 + 缩略图渐进）
    # ------------------------------------------------------------------

    def _show_preview_placeholder(self):
        self._preview_gen += 1
        self._cur_image_path = None
        self._cur_image_entry = None
        for w in self.preview_inner.winfo_children():
            w.destroy()
        tk.Label(self.preview_inner, text=tr("preview_placeholder"), bg=SURFACE2,
                 fg=TEXT_MUTED, font=F_UI, justify=tk.CENTER).pack(expand=True, fill=tk.BOTH)
        self._preview_photo = None
        self._thumb_photo = None

    def _schedule_preview(self, delay=130):
        if self._preview_after:
            self.root.after_cancel(self._preview_after)
        self._preview_after = self.root.after(delay, self._update_preview)

    def _update_preview(self):
        self._preview_after = None
        tree = self._get_active_tree()
        sel = tree.selection() if tree else ()
        entry = None
        if sel:
            h = self._iid_to_hash[self._active_type].get(sel[0])
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
        for w in self.preview_inner.winfo_children():
            w.destroy()

        content = entry.get("content", "")

        # URL 智能识别
        url_pattern = re.compile(r'https?://\S+|www\.\S+')
        urls = url_pattern.findall(content)
        # 判断是否为纯网址内容（去掉所有网址和空白后无剩余文字）
        stripped = url_pattern.sub('', content).strip()
        is_pure_url = bool(urls) and not stripped

        # 网址区（纯网址或混合内容均显示）
        if urls:
            url_frame = tk.LabelFrame(self.preview_inner, text=tr("panel_urls"),
                                      bg=SURFACE2, fg=ACCENT, font=F_SMALL)
            url_frame.pack(fill=tk.X, pady=(0, 4))
            for u in urls:
                lbl = tk.Label(url_frame, text=u, bg=SURFACE2, fg=ACCENT,
                               font=F_MONO, cursor="hand2", anchor="w",
                               padx=6, pady=2)
                lbl.pack(fill=tk.X)
                lbl.bind("<Double-1>", lambda e, url=u: self._open_url(url))
                lbl.bind("<Button-3>", lambda e, url=u: self._on_url_right_click(e, url))

        # 纯网址内容不再显示文字区
        if not is_pure_url:
            body = ttk.Frame(self.preview_inner, style="Card.TFrame")
            body.pack(fill=tk.BOTH, expand=True)
            txt = tk.Text(body, wrap=tk.WORD, font=F_MONO, bg=SURFACE2, fg=TEXT,
                          relief=tk.FLAT, borderwidth=0, padx=10, pady=8,
                          insertbackground=ACCENT, state=tk.NORMAL, highlightthickness=0)
            txt.tag_configure("sel", background=ACCENT_DIM, foreground="#ffffff")
            sb = ttk.Scrollbar(body, command=txt.yview)
            txt.configure(yscrollcommand=sb.set)
            shown = content[:20000]
            txt.insert("1.0", shown + (tr("preview_truncated") if len(content) > 20000 else ""))
            txt.configure(state=tk.DISABLED)
            txt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            sb.pack(side=tk.RIGHT, fill=tk.Y)
            # 右键菜单：复制选中 / 复制全部 / 全选
            txt.bind("<Button-3>", lambda e, w=txt: self._on_right_click_text_preview(e, w))

        info = tk.Frame(self.preview_inner, bg=SURFACE2)
        info.pack(fill=tk.X, pady=(6, 0))
        n_lines = content.count("\n") + 1
        tk.Label(info, text=tr("chip_chars", n=f"{len(content):,}"), bg=ACCENT_DIM,
                 fg=ACCENT, font=F_SMALL, padx=6, pady=2).pack(side=tk.LEFT, padx=(0, 4))
        tk.Label(info, text=tr("chip_lines", n=f"{n_lines:,}"), bg=SURFACE3,
                 fg=TEXT_SEC, font=F_SMALL, padx=6, pady=2).pack(side=tk.LEFT)

    def _open_url(self, url):
        """用默认浏览器打开网址。"""
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        webbrowser.open(url)

    def _on_url_right_click(self, event, url):
        """网址右键菜单：复制 / 打开。"""
        menu = self._make_menu()
        menu.add_command(label=tr("m_copy_content"),
                         command=lambda: (self._set_clip(url),
                                          self._set_status(tr("st_copied_preview",
                                                              n=f"{len(url):,}"), "ok")))
        menu.add_command(label=tr("m_open_url"),
                         command=lambda: self._open_url(url))
        menu.tk_popup(event.x_root, event.y_root)

    def _set_clip(self, text):
        """设置剪贴板文字（内部辅助）。"""
        self._last_self_copy = time.time()
        self.store.mark_self_copy()
        set_clipboard_text(text)

    def _preview_url(self, entry):
        """网址分类的预览面板：显示可点击的大号网址。"""
        self._preview_gen += 1
        self._cur_image_path = None
        self._cur_text_entry = entry
        for w in self.preview_inner.winfo_children():
            w.destroy()

        url = entry.get("content", "")
        body = tk.Frame(self.preview_inner, bg=SURFACE2)
        body.pack(fill=tk.BOTH, expand=True, padx=10, pady=12)

        tk.Label(body, text="\U0001f310", bg=SURFACE2, fg=ACCENT,
                 font=("Segoe UI Emoji", 28)).pack(pady=(8, 4))
        lbl = tk.Label(body, text=url, bg=SURFACE2, fg=ACCENT,
                       font=F_MONO, cursor="hand2", wraplength=380,
                       justify=tk.CENTER)
        lbl.pack(pady=4)
        lbl.bind("<Double-1>", lambda e: self._open_url(url))
        lbl.bind("<Button-3>", lambda e: self._on_url_right_click(e, url))

        tk.Label(body, text=tr("preview_dblclick_url"), bg=SURFACE2,
                 fg=TEXT_MUTED, font=F_SMALL).pack(pady=(12, 0))

        # 信息条
        info = tk.Frame(self.preview_inner, bg=SURFACE2)
        info.pack(fill=tk.X, pady=(6, 0))
        tk.Label(info, text=tr("chip_chars", n=f"{len(url):,}"), bg=ACCENT_DIM,
                 fg=ACCENT, font=F_SMALL, padx=6, pady=2).pack(side=tk.LEFT, padx=(0, 4))

    def _image_full_path(self, entry):
        base = os.path.dirname(self.store.path)
        return os.path.join(base, entry.get("filename", ""))

    def _preview_image(self, entry):
        self._preview_gen += 1
        gen = self._preview_gen
        self._cur_image_entry = entry
        for w in self.preview_inner.winfo_children():
            w.destroy()

        img_path = self._image_full_path(entry)
        thumb_path = os.path.join(os.path.dirname(img_path),
                                  "thumb_" + os.path.basename(img_path))

        img_area = tk.Frame(self.preview_inner, bg=SURFACE2)
        img_area.pack(fill=tk.BOTH, expand=True)
        self._preview_label = tk.Label(img_area, bg=SURFACE2, cursor="hand2")
        self._preview_label.pack(expand=True, padx=8, pady=8)
        self._preview_label.bind("<Double-1>", lambda e: self._open_selected())
        self._preview_label.bind("<Button-3>", self._on_right_click_preview)

        if not (HAS_PIL and os.path.exists(img_path)):
            self._cur_image_path = None
            self._preview_label.configure(text=tr("preview_unavailable"),
                                          fg=TEXT_MUTED, font=F_UI)
        else:
            self._cur_image_path = img_path
            self._thumb_photo = None
            self._preview_photo = None
            self._last_render_key = None
            # 1) 立即显示缩略图（快）
            if os.path.exists(thumb_path):
                try:
                    t = Image.open(thumb_path)
                    t.load()
                    self._thumb_photo = ImageTk.PhotoImage(t)
                    self._preview_label.configure(image=self._thumb_photo)
                except Exception:
                    pass
            # 2) 后台线程加载清晰大图
            threading.Thread(target=self._load_image_worker,
                             args=(img_path, gen), daemon=True).start()

        # 信息条
        info = tk.Frame(self.preview_inner, bg=SURFACE2)
        info.pack(fill=tk.X, pady=(6, 0))
        chips = [
            (f" {entry.get('width', '?')} × {entry.get('height', '?')} ", ACCENT_DIM, ACCENT),
            (f" {fmt_image_type(entry.get('original_format', '?'))} ", SURFACE3, TEAL),
            (f" {fmt_size(entry.get('file_size', 0))} ", SURFACE3, TEXT_SEC),
        ]
        for text, bg, fg in chips:
            tk.Label(info, text=text, bg=bg, fg=fg, font=F_SMALL,
                     padx=6, pady=2).pack(side=tk.LEFT, padx=(0, 4))
        tk.Label(info, text=tr("preview_dblclick_viewer"), bg=SURFACE2, fg=TEXT_MUTED,
                 font=F_SMALL).pack(side=tk.RIGHT)

    def _load_image_worker(self, path, gen):
        """后台线程：加载并预缩放图片，经队列送回主线程。"""
        try:
            img = Image.open(path)
            img.load()
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGB")
            if max(img.size) > PREVIEW_MAX:
                ratio = PREVIEW_MAX / max(img.size)
                img = img.resize((max(1, int(img.width * ratio)),
                                  max(1, int(img.height * ratio))), Image.LANCZOS)
            self._ui_queue.put(("image", (gen, path, img)))
        except Exception:
            pass

    def _on_image_loaded(self, gen, path, img):
        if gen != self._preview_gen or path != self._cur_image_path:
            return  # 已切换选中项，丢弃过期结果
        self._cached_pil = img
        self._cached_path = path
        self._last_render_key = None
        self._render_preview_image()

    def _render_preview_image(self):
        """把缓存的图片按预览区尺寸缩放显示。"""
        if not HAS_PIL or not self._cur_image_path:
            return
        if not hasattr(self, "_preview_label") or not self._preview_label.winfo_exists():
            return
        src = self._cached_pil if self._cached_path == self._cur_image_path else None
        if src is None:
            return
        self.preview_inner.update_idletasks()
        max_w = max(40, self.preview_inner.winfo_width() - 24)
        max_h = max(40, self.preview_inner.winfo_height() - 70)
        key = (self._cur_image_path, max_w, max_h)
        if key == self._last_render_key:
            return
        self._last_render_key = key
        try:
            ratio = min(max_w / src.width, max_h / src.height, 1.0)
            nw, nh = max(1, int(src.width * ratio)), max(1, int(src.height * ratio))
            resized = src if ratio >= 1.0 else src.resize((nw, nh), Image.LANCZOS)
            self._preview_photo = ImageTk.PhotoImage(resized)
            self._preview_label.configure(image=self._preview_photo)
        except Exception:
            pass

    def _on_preview_resize(self, event=None):
        if not self._cur_image_path:
            return
        if self._resize_after:
            self.root.after_cancel(self._resize_after)
        self._resize_after = self.root.after(80, self._render_preview_image)

    # ---- 预览区滚动支持 ----

    def _on_preview_inner_configure(self, event=None):
        """内部内容尺寸变化时，更新 Canvas 的 scrollregion。"""
        self._preview_canvas.configure(scrollregion=self._preview_canvas.bbox("all"))

    def _on_preview_canvas_resize(self, event=None):
        """Canvas 尺寸变化时，让内部 Frame 宽度跟随 Canvas 宽度。"""
        self._preview_canvas.itemconfig(self._preview_window_id, width=event.width)

    def _bind_preview_mousewheel(self, event=None):
        self._preview_canvas.bind_all("<MouseWheel>", self._on_preview_mousewheel)

    def _unbind_preview_mousewheel(self, event=None):
        self._preview_canvas.unbind_all("<MouseWheel>")

    def _on_preview_mousewheel(self, event):
        # 如果鼠标在内部 Text 控件上，让 Text 自己处理滚动
        w = event.widget
        if isinstance(w, tk.Text):
            return
        self._preview_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _preview_files(self, entry):
        self._preview_gen += 1
        self._cur_image_path = None
        for w in self.preview_inner.winfo_children():
            w.destroy()
        paths = entry.get("file_paths", [])

        info = tk.Frame(self.preview_inner, bg=SURFACE2)
        info.pack(fill=tk.X, pady=(2, 6))
        tk.Label(info, text=tr("chip_files", n=entry.get("file_count", len(paths))),
                 bg=ACCENT_DIM, fg=ACCENT, font=F_SMALL, padx=6, pady=2).pack(
                     side=tk.LEFT, padx=(0, 4))
        tk.Label(info, text=f" {self._extract_extensions(paths)} ", bg=SURFACE3,
                 fg=TEAL, font=F_SMALL, padx=6, pady=2).pack(side=tk.LEFT)

        lb = tk.Listbox(self.preview_inner, font=("Consolas", 9), relief=tk.FLAT,
                        borderwidth=0, bg=SURFACE2, fg=TEXT, selectbackground=ACCENT_DIM,
                        selectforeground="#ffffff", activestyle="none", highlightthickness=0)
        sb = ttk.Scrollbar(self.preview_inner, command=lb.yview)
        lb.configure(yscrollcommand=sb.set)
        for fp in paths:
            lb.insert(tk.END, " " + fp)
        lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        lb.bind("<Double-1>", lambda e, lb=lb: self._open_path_from_listbox(lb))

        tk.Label(self.preview_inner, text=tr("preview_dblclick_open"), bg=SURFACE2,
                 fg=TEXT_MUTED, font=F_SMALL, anchor=tk.E).pack(fill=tk.X, pady=(4, 0))

    def _open_path_from_listbox(self, listbox):
        sel = listbox.curselection()
        if not sel:
            return
        path = listbox.get(sel[0]).strip()
        if os.path.exists(path):
            os.startfile(path)
        else:
            messagebox.showinfo(tr("dlg_info"), tr("msg_file_not_found", path=path))


    # ------------------------------------------------------------------
    # 选中项读取
    # ------------------------------------------------------------------

    def _get_selected_hashes(self):
        tree = self._get_active_tree()
        if not tree:
            return []
        iid_map = self._iid_to_hash[self._active_type]
        return [iid_map[iid] for iid in tree.selection() if iid in iid_map]

    def _get_selected_entry(self):
        """取第一个选中项；若无选中但列表有内容，取第一行（便于搜索后直接回车）。"""
        hashes = self._get_selected_hashes()
        if hashes:
            return self._entry_index.get(hashes[0])
        tree = self._get_active_tree()
        if tree:
            children = tree.get_children()
            if children:
                h = self._iid_to_hash[self._active_type].get(children[0])
                return self._entry_index.get(h) if h else None
        return None

    # ------------------------------------------------------------------
    # 动作：复制 / 打开 / 置顶 / 删除 / 导出
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
            if etype == "text":
                set_clipboard_text(entry["content"])
                self._set_status(tr("st_copied_chars", n=f"{len(entry['content']):,}"), "ok")
            elif etype == "url":
                set_clipboard_text(entry["content"])
                self._set_status(tr("st_copied_chars", n=f"{len(entry['content']):,}"), "ok")
            elif etype == "image":
                img_path = self._image_full_path(entry)
                if os.path.exists(img_path) and HAS_PIL:
                    img = Image.open(img_path)
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
            messagebox.showerror(tr("dlg_error"), tr("msg_copy_failed", err=ex))

    def _flash_selected(self):
        tree = self._get_active_tree()
        if not tree:
            return
        etype = self._active_type
        iids = list(tree.selection())
        for iid in iids:
            tree.item(iid, tags=("flash", "sel"))
        self.root.after(550, lambda: [self._retag(etype, iid) for iid in iids
                                      if iid in self._iid_to_hash[etype]])

    def _open_selected(self):
        """打开选中项：图片→系统默认看图软件；文件→打开/定位；网址→浏览器；文字→复制。"""
        entry = self._get_selected_entry()
        if not entry:
            return
        etype = entry.get("type", "text")
        try:
            if etype == "image":
                img_path = self._image_full_path(entry)
                if os.path.exists(img_path):
                    os.startfile(img_path)  # 系统默认看图软件
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
            messagebox.showerror(tr("dlg_error"), tr("msg_open_failed", err=ex))

    @staticmethod
    def _reveal_in_explorer(path):
        subprocess.Popen(f'explorer /select,"{os.path.abspath(path)}"')

    def _on_tree_double_click(self, event):
        tree = self._get_active_tree()
        if not tree:
            return
        iid = tree.identify_row(event.y)
        if iid:
            if iid not in tree.selection():
                tree.selection_set(iid)
            self._open_selected()

    def _pin_selected(self):
        hashes = self._get_selected_hashes()
        if not hashes:
            return
        to_pin = [h for h in hashes if h not in self._pinned_hashes]
        if not to_pin:
            self._set_status(tr("st_already_pinned"))
            return
        self.store.save_snapshot(tr("snap_pin", n=len(to_pin),
                                    t=self._type_label(self._active_type)))
        n = self.store.pin_many(to_pin)
        self._after_mutate(tr("st_pinned", n=n))
        self.lightbar.surge(42.0, 0.9)          # 琥珀色浪涌

    def _unpin_selected(self):
        hashes = self._get_selected_hashes()
        if not hashes:
            return
        to_unpin = [h for h in hashes if h in self._pinned_hashes]
        if not to_unpin:
            self._set_status(tr("st_not_pinned"))
            return
        self.store.save_snapshot(tr("snap_unpin", n=len(to_unpin),
                                    t=self._type_label(self._active_type)))
        n = self.store.unpin_many(to_unpin)
        self._after_mutate(tr("st_unpinned", n=n))

    def _toggle_pin_selected(self):
        hashes = self._get_selected_hashes()
        if not hashes:
            return "break"
        pinned = unpinned = 0
        self.store.save_snapshot(tr("snap_toggle_pin",
                                    t=self._type_label(self._active_type)))
        for h in hashes:
            if self.store.toggle_pin(h):
                pinned += 1
            else:
                unpinned += 1
        self._after_mutate(tr("st_pin_toggled", a=pinned, b=unpinned))
        self.lightbar.surge(42.0, 0.9)          # 琥珀色浪涌
        return "break"

    def _delete_selected(self):
        hashes = self._get_selected_hashes()
        if not hashes:
            return
        if not messagebox.askyesno(tr("dlg_confirm_delete"),
                                   tr("msg_delete_confirm", n=len(hashes))):
            return
        self.store.save_snapshot(tr("snap_delete", n=len(hashes),
                                    t=self._type_label(self._active_type)))
        self.store.delete_many(hashes)
        self._after_mutate(tr("st_deleted", n=len(hashes)))
        self.lightbar.surge(4.0, 0.95)          # 红色浪涌

    def _after_mutate(self, status_msg):
        self._refresh_tab(self._active_type)
        self._update_preview()
        self._refresh_history_list()
        self._set_status(status_msg, "ok")

    # ---- 清空类 ----

    def _clear_type(self):
        etype, label = self._active_type, self._type_label(self._active_type)
        count = self.store.count(etype)
        if count == 0:
            self._set_status(tr("st_no_type_records", t=label))
            return
        if not messagebox.askyesno(tr("dlg_confirm_clear"),
                                   tr("msg_clear_type", t=label, n=count)):
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
        if not messagebox.askyesno(tr("dlg_confirm_remove"),
                                   tr("msg_clear_type_unpinned", t=label, n=unpinned)):
            return
        self.store.save_snapshot(tr("snap_clear_type_unpinned", t=label, n=unpinned))
        self.store.clear_type_unpinned(etype)
        self._after_mutate(tr("st_cleared_unpinned_type", t=label))

    def _clear_unpinned(self):
        unpinned = self.store.count() - self.store.pinned_count()
        if unpinned == 0:
            self._set_status(tr("st_no_unpinned"))
            return
        if not messagebox.askyesno(tr("dlg_confirm_remove"),
                                   tr("msg_clear_unpinned", n=unpinned)):
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
        if not messagebox.askyesno(tr("dlg_confirm_clear"),
                                   tr("msg_clear_all", n=total)):
            return
        self.store.save_snapshot(tr("snap_clear_all", n=total))
        self.store.clear()
        self._refresh_all()
        self._set_status(tr("st_cleared_all"), "ok")

    # ---- 导出 / 复制路径 ----

    def _export_selected(self):
        entry = self._get_selected_entry()
        if not entry:
            self._set_status(tr("st_nothing_to_export"), "warn")
            return
        etype = entry.get("type", "text")
        if etype == "text":
            path = filedialog.asksaveasfilename(
                defaultextension=".txt",
                filetypes=[(tr("ft_text"), "*.txt"), (tr("ft_all"), "*.*")])
            if path:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(entry["content"])
                self._set_status(tr("st_exported", name=os.path.basename(path)), "ok")
        elif etype == "image":
            img_path = self._image_full_path(entry)
            if not os.path.exists(img_path):
                self._set_status(tr("st_image_missing"), "err")
                return
            out = filedialog.asksaveasfilename(
                defaultextension=".png",
                filetypes=[("PNG", "*.png"), ("JPEG", "*.jpg"), (tr("ft_all"), "*.*")])
            if out:
                import shutil
                shutil.copy2(img_path, out)
                self._set_status(tr("st_exported", name=os.path.basename(out)), "ok")
        else:
            self._set_status(tr("st_export_files_hint"), "warn")

    def _copy_image_path(self, entry):
        img_path = self._image_full_path(entry)
        set_clipboard_text(img_path)
        self._set_status(tr("st_copied_image_path"), "ok")

    def _copy_file_paths(self, entry):
        set_clipboard_text("\n".join(entry.get("file_paths", [])))
        self._set_status(tr("st_copied_paths"), "ok")

    # ------------------------------------------------------------------
    # 右键菜单
    # ------------------------------------------------------------------

    def _make_menu(self):
        return tk.Menu(self.root, tearoff=0, bg=SURFACE2, fg=TEXT,
                       activebackground=ACCENT_DIM, activeforeground="#ffffff",
                       relief="flat", bd=1, font=F_UI)

    def _on_right_click(self, event):
        tree = self._get_active_tree()
        if not tree:
            return
        item = tree.identify_row(event.y)
        if item and item not in tree.selection():
            tree.selection_set(item)
        entry = self._get_selected_entry()
        if not entry:
            return
        etype = entry.get("type", "text")
        n = len(tree.selection())
        menu = self._make_menu()

        if etype == "text":
            menu.add_command(label=tr("m_copy_content"), command=self._copy_selected)
            menu.add_command(label=tr("m_export_txt"), command=self._export_selected)
        elif etype == "url":
            menu.add_command(label=tr("m_copy_content"), command=self._copy_selected)
            menu.add_command(label=tr("m_open_url"), command=self._open_selected)
        elif etype == "image":
            menu.add_command(label=tr("m_copy_image"), command=self._copy_selected)
            menu.add_command(label=tr("m_open_viewer"), command=self._open_selected)
            menu.add_command(label=tr("m_open_folder"),
                             command=lambda: self._reveal_in_explorer(self._image_full_path(entry)))
            menu.add_command(label=tr("m_copy_path"), command=lambda: self._copy_image_path(entry))
            menu.add_command(label=tr("m_export_image"), command=self._export_selected)
        else:
            menu.add_command(label=tr("m_copy_files"), command=self._copy_selected)
            menu.add_command(label=tr("m_open_locate"), command=self._open_selected)
            first = next((p for p in entry.get("file_paths", []) if os.path.exists(p)), None)
            if first:
                menu.add_command(label=tr("m_open_folder"),
                                 command=lambda p=first: self._reveal_in_explorer(p))
            menu.add_command(label=tr("m_copy_paths"), command=lambda: self._copy_file_paths(entry))

        menu.add_separator()
        menu.add_command(label=tr("m_toggle_pin"), command=self._toggle_pin_selected)
        menu.add_command(label=tr("m_delete_n", n=n) if n > 1 else tr("m_delete"),
                         command=self._delete_selected)
        menu.tk_popup(event.x_root, event.y_root)

    def _on_right_click_preview(self, event):
        """预览图上的右键 → 图片操作菜单。"""
        entry = self._cur_image_entry
        if not entry or entry.get("type") != "image":
            return
        menu = self._make_menu()
        menu.add_command(label=tr("m_open_viewer_plain"), command=self._open_selected)
        menu.add_command(label=tr("m_open_folder"),
                         command=lambda: self._reveal_in_explorer(self._image_full_path(entry)))
        menu.add_command(label=tr("m_copy_path"), command=lambda: self._copy_image_path(entry))
        menu.tk_popup(event.x_root, event.y_root)

    def _on_right_click_text_preview(self, event, txt):
        """文字预览上的右键 → 复制选中 / 复制全部 / 全选。"""
        menu = self._make_menu()
        has_sel = bool(txt.tag_ranges("sel"))
        if has_sel:
            menu.add_command(label=tr("m_copy_selection"),
                             command=lambda: self._copy_preview_text(txt, only_sel=True))
        menu.add_command(label=tr("m_copy_all"),
                         command=lambda: self._copy_preview_text(txt, only_sel=False))
        menu.add_separator()
        menu.add_command(label=tr("m_select_all"),
                         command=lambda: txt.tag_add("sel", "1.0", "end-1c"))
        menu.tk_popup(event.x_root, event.y_root)

    def _copy_preview_text(self, txt, only_sel):
        """复制预览文字：选中部分取自界面，全部内容取自原始记录（不截断）。"""
        try:
            if only_sel:
                content = txt.get("sel.first", "sel.last")
            else:
                entry = self._cur_text_entry
                content = entry.get("content", "") if entry else txt.get("1.0", "end-1c")
        except tk.TclError:
            return
        if not content:
            return
        self._last_self_copy = time.time()
        self.store.mark_self_copy()
        set_clipboard_text(content)
        self._set_status(tr("st_copied_preview", n=f"{len(content):,}"), "ok")

    # ------------------------------------------------------------------
    # 管理菜单（页头）
    # ------------------------------------------------------------------

    def _show_manage_menu(self):
        menu = self._make_menu()
        menu.add_command(label=tr("m_refresh"), command=self._refresh_all)
        menu.add_separator()
        menu.add_command(label=tr("m_clear_type", t=self._type_label(self._active_type)),
                         command=self._clear_type)
        menu.add_command(label=tr("m_clear_type_unpinned",
                                  t=self._type_label(self._active_type)),
                         command=self._clear_type_unpinned)
        menu.add_command(label=tr("m_clear_unpinned"), command=self._clear_unpinned)
        menu.add_separator()
        menu.add_command(label=tr("m_clear_all"), command=self._clear_all)
        x = self.manage_btn.winfo_rootx()
        y = self.manage_btn.winfo_rooty() + self.manage_btn.winfo_height() + 2
        menu.tk_popup(x, y)

    # ------------------------------------------------------------------
    # 监控集成：主线程消息队列（线程安全）
    # ------------------------------------------------------------------

    def _poll_ui_queue(self):
        try:
            while True:
                kind, payload = self._ui_queue.get_nowait()
                if kind == "clip":
                    self._on_clip_changed()
                elif kind == "image":
                    self._on_image_loaded(*payload)
        except queue.Empty:
            pass
        if self.monitor and self.monitor.consume_change():
            self._on_clip_changed()
        self.root.after(120, self._poll_ui_queue)

    def _on_clip_changed(self):
        self._refresh_tab(self._active_type)
        for etype in ("text", "image", "file", "url"):
            if etype != self._active_type:
                self._update_tab_badge(etype)
        self._update_header_stats()
        # 捕获到新内容：一道青色波纹从左向右掠过灯带
        try:
            self.lightbar.pulse(190.0, 0.0, strength=1.0)
        except Exception:
            pass
        # 应用内复制引发的剪贴板变化不重复提示，避免覆盖"已复制"反馈
        if time.time() - self._last_self_copy > 1.2:
            self._set_status(tr("st_captured"), "ok")

    # ------------------------------------------------------------------
    # 页头呼吸灯
    # ------------------------------------------------------------------

    def _animate_dot(self):
        if self.monitor and self.monitor.is_alive():
            frames = [SUCCESS, "#37b87b", "#2b9a67", "#37b878"]
            self._dot_phase = (self._dot_phase + 1) % len(frames)
            color = frames[self._dot_phase]
        else:
            color = TEXT_MUTED
            self.monitor_var.set(tr("monitor_stopped"))
        try:
            self.dot_canvas.itemconfig("dot", fill=color)
        except tk.TclError:
            return
        self.root.after(700, self._animate_dot)

    # ------------------------------------------------------------------
    # 设置
    # ------------------------------------------------------------------

    def _open_settings(self):
        SettingsDialog(self)

    def apply_settings(self, lang, autostart):
        """保存设置：自启动写注册表；语言变化则重启界面。"""
        if autostart != get_autostart():
            if set_autostart(autostart):
                self._set_status(tr("st_autostart_on") if autostart
                                 else tr("st_autostart_off"), "ok")
            else:
                self._set_status(tr("st_autostart_failed"), "err")
        cfg = load_config()
        if cfg.get("language", "zh") != lang:
            cfg["language"] = lang
            save_config(cfg)
            self.restart_flag = True
            if self._tray:
                self._tray.stop()
            self.root.destroy()     # main() 主循环将以新语言重建界面

    # ------------------------------------------------------------------
    # 收尾
    # ------------------------------------------------------------------

    def _on_close(self):
        """点击 X 按钮：最小化到系统托盘，而非退出。"""
        self.root.withdraw()

    def _tray_show(self):
        """从托盘恢复主窗口。"""
        self.root.after(0, self._restore_window)

    def _restore_window(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _tray_quit(self):
        """从托盘菜单彻底退出程序。"""
        self.root.after(0, self._real_quit)

    def _real_quit(self):
        if self.monitor:
            self.monitor.stop()
        if self._tray:
            self._tray.stop()
        self.root.destroy()

    def run(self):
        # 启动系统托盘图标
        self._tray = TrayIcon(
            on_show=self._tray_show,
            on_quit=self._tray_quit,
            title="YouBoard",
        )
        self._tray.start()
        self.root.mainloop()


# ===========================================================================
# 设置对话框（语言切换 + 开机自启动 + 关于）
# ===========================================================================

class ToggleSwitch(tk.Canvas):
    """iOS 风格滑动开关：点击切换，旋钮带缓动动画，轨道颜色渐变。"""

    W, H = 46, 24

    def __init__(self, parent, initial=False, on_change=None):
        super().__init__(parent, width=self.W, height=self.H, bg=SURFACE2,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.on = bool(initial)
        self._pos = 1.0 if self.on else 0.0
        self._on_change = on_change
        self._token = 0
        self.bind("<Button-1>", self._toggle)
        self._draw()

    def _toggle(self, event=None):
        self.on = not self.on
        self._token += 1
        self._step(self._token)
        if self._on_change:
            self._on_change(self.on)

    def set_value(self, value):
        if self.on == bool(value):
            return
        self.on = bool(value)
        self._token += 1
        self._step(self._token)

    def _step(self, token):
        if token != self._token:
            return
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        target = 1.0 if self.on else 0.0
        d = target - self._pos
        if abs(d) < 0.04:
            self._pos = target
            self._draw()
            return
        self._pos += d * 0.42           # 缓动逼近
        self._draw()
        self.after(16, lambda: self._step(token))

    def _draw(self):
        self.delete("all")
        p = self._pos
        r = self.H // 2
        track = _lerp_color(BORDER_LT, ACCENT, p)
        self.create_rectangle(r, 3, self.W - r, self.H - 3, fill=track, outline="")
        self.create_oval(2, 3, self.H - 2, self.H - 3, fill=track, outline="")
        self.create_oval(self.W - self.H + 2, 3, self.W - 2, self.H - 3,
                         fill=track, outline="")
        cx = 2 + r + p * (self.W - 2 * r - 4) + 2
        self.create_oval(cx - r + 4, 5, cx + r - 4, self.H - 5,
                         fill="#f2f5fa", outline="")


class SettingsDialog:
    """暗色设置窗口：语言分段选择 / 开机自启动开关 / 关于信息。"""

    def __init__(self, app):
        self.app = app
        root = app.root
        self.win = tk.Toplevel(root)
        self.win.title(tr("settings_title"))
        self.win.configure(bg=BG)
        self.win.geometry("470x640")
        self.win.minsize(470, 640)
        self.win.transient(root)
        self.win.grab_set()
        try:
            if LOGO_ICO and os.path.exists(LOGO_ICO):
                self.win.iconbitmap(LOGO_ICO)
        except Exception:
            pass

        self._lang_sel = LANG if LANG in STRINGS else "zh"
        self._lang_hover_code = None

        # 顶部迷你环境灯带（与主界面一致的呼吸效果）
        self.light = AmbientLightBar(self.win)
        self.light.pack(fill=tk.X)
        self.light.surge(215.0, 0.5)

        # ---- 语言卡片 ----
        card = self._card(tr("set_language"))
        seg = tk.Frame(card, bg=SURFACE3, padx=3, pady=3)
        seg.pack(fill=tk.X, padx=14)
        self._lang_btns = {}
        for code in ("zh", "en"):
            lbl = tk.Label(seg, text=tr("set_lang_" + code), font=F_UI_B,
                           cursor="hand2", pady=7)
            lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)
            lbl.bind("<Button-1>", lambda e, c=code: self._pick_lang(c))
            lbl.bind("<Enter>", lambda e, c=code: self._lang_hover(c, True))
            lbl.bind("<Leave>", lambda e, c=code: self._lang_hover(c, False))
            self._lang_btns[code] = lbl
        self._paint_lang()
        tk.Label(card, text=tr("set_lang_note"), bg=SURFACE2, fg=TEXT_MUTED,
                 font=F_SMALL).pack(anchor=tk.W, padx=16, pady=(8, 10))

        # ---- 通用卡片：开机自启动 ----
        card = self._card(tr("set_general"))
        row = tk.Frame(card, bg=SURFACE2)
        row.pack(fill=tk.X, padx=14, pady=(0, 12))
        txt_box = tk.Frame(row, bg=SURFACE2)
        txt_box.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))
        tk.Label(txt_box, text=tr("set_autostart"), bg=SURFACE2, fg=TEXT,
                 font=F_UI_B).pack(anchor=tk.W)
        tk.Label(txt_box, text=tr("set_autostart_desc"), bg=SURFACE2,
                 fg=TEXT_MUTED, font=F_SMALL, wraplength=310,
                 justify=tk.LEFT).pack(anchor=tk.W, pady=(3, 0))
        self.toggle = ToggleSwitch(row, initial=get_autostart(),
                                   on_change=self._on_toggle)
        self.toggle.pack(side=tk.RIGHT, pady=2)

        # ---- 关于卡片 ----
        card = self._card(tr("set_about"))
        tk.Label(card, text=f"{APP_NAME}  v{APP_VERSION}", bg=SURFACE2,
                 fg=TEXT, font=("Bahnschrift", 12, "bold")).pack(anchor=tk.W, padx=14)
        tk.Label(card, text=tr("set_data_location"), bg=SURFACE2, fg=TEXT_MUTED,
                 font=F_SMALL).pack(anchor=tk.W, padx=14, pady=(8, 0))
        tk.Label(card, text=os.path.dirname(HISTORY_FILE), bg=SURFACE2,
                 fg=TEXT_SEC, font=("Consolas", 8), wraplength=410,
                 justify=tk.LEFT).pack(anchor=tk.W, padx=14, pady=(2, 12))

        # ---- 底部按钮 ----
        footer = tk.Frame(self.win, bg=BG)
        footer.pack(fill=tk.X, padx=16, pady=14)
        ttk.Button(footer, text=tr("btn_save"), style="Accent.TButton",
                   command=self._save).pack(side=tk.RIGHT)
        ttk.Button(footer, text=tr("btn_cancel"), style="Ghost.TButton",
                   command=self.win.destroy).pack(side=tk.RIGHT, padx=(0, 8))

        self.win.protocol("WM_DELETE_WINDOW", self.win.destroy)

    # ---- 内部 ----

    def _card(self, title):
        card = tk.Frame(self.win, bg=SURFACE2, highlightthickness=1,
                        highlightbackground=BORDER)
        card.pack(fill=tk.X, padx=16, pady=(14, 0))
        tk.Label(card, text=title, bg=SURFACE2, fg=TEXT_MUTED,
                 font=F_HEAD).pack(anchor=tk.W, padx=14, pady=(10, 8))
        return card

    def _pick_lang(self, code):
        self._lang_sel = code
        self._paint_lang()
        self.light.pulse(215.0, 0.3 if code == "zh" else 0.7, strength=0.8)

    def _lang_hover(self, code, entering):
        self._lang_hover_code = code if entering else None
        self._paint_lang()

    def _paint_lang(self):
        for code, lbl in self._lang_btns.items():
            if code == self._lang_sel:
                lbl.configure(bg=ACCENT, fg="#0c1420")
            elif code == self._lang_hover_code:
                lbl.configure(bg=SURFACE3, fg=TEXT)
            else:
                lbl.configure(bg=SURFACE3, fg=TEXT_SEC)

    def _on_toggle(self, value):
        self.light.surge(140.0 if value else 4.0, 0.6)

    def _save(self):
        lang = self._lang_sel
        autostart = self.toggle.on
        self.win.destroy()
        self.app.apply_settings(lang, autostart)


# ===========================================================================
# CLI
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
        preview = e.get("content", "")[:70] if etype == "text" else repr(e.get("filename", e.get("file_paths", "")))[:70]
        print(f"  {i + 1:>3}  {pin:<4}  [{etype}]  {ts}  {preview}")
    print(f"{'=' * 100}")


# ===========================================================================
# Main
# ===========================================================================

def main():
    store = ClipboardStore()
    apply_language(load_config().get("language", "zh"))

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

    # 默认：GUI + 监控（切换语言时销毁窗口并以新语言重建）
    monitor = ClipboardMonitor(store)
    monitor.start()
    try:
        restart = True
        while restart:
            apply_language(load_config().get("language", "zh"))
            gui = YouBoardApp(store, monitor)
            try:
                gui.run()
            except KeyboardInterrupt:
                break
            restart = gui.restart_flag
    finally:
        monitor.stop()


if __name__ == "__main__":
    main()
