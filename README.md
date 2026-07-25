# YouBoard - 剪贴板历史管理工具

一款轻量级 Windows 剪贴板管理工具，自动记录复制历史，支持文字、图片、文件、网址四大分类，随取随用。

## ✨ 功能特性

- **剪贴板监控** — 自动捕获复制内容，Ctrl+C 后实时显示，双重保障（Win32 事件 + 轮询）
- **全局快捷键** — 默认 Alt+Q 呼出/隐藏窗口，设置中可自定义（支持 alt/ctrl/shift/win + 字母/F键），基于底层键盘钩子，可靠拦截全局按键
- **快捷键冲突检测** — 录入时自动检测系统保留快捷键（Win+L/E/D/V 等 30+ 组合）及其他应用占用，红色警告提示
- **四大分类** — 文字 / 图片 / 文件 / 网址，独立 Tab 管理
- **网址智能识别** — 纯 URL 自动归入网址分类，混合内容双存不丢失
- **系统托盘** — 原生 QSystemTrayIcon 托盘（自定义 ICO 图标），右键快速操作
- **复制去重** — 应用内复制不产生重复记录
- **图片预览实时缩放** — 图片跟随预览框大小等比缩放（放大/缩小），拖拽分割条即时响应
- **历史快照** — 记录删除/清空操作，支持一键回滚，新条目淡入动画
- **中英双语** — 设置中一键切换语言，检查更新等对话框完整适配
- **开机自启动** — 设置中开关控制
- **自动更新** — 设置 → 关于 → 检查更新，自动从 GitHub Releases 下载新版本
- **快捷键** — Enter 复制、Del 删除、Space 置顶、Ctrl+A 全选、Ctrl+O 打开、F5 刷新
- **环境灯带** — 全宽 RGB 灯带动效，呼吸流转 + 按键波纹 + 操作浪涌（QPainter 30fps）
- **自定义背景** — 支持 PNG/JPG/BMP 静态 + GIF 动态背景，面板半透明通透显露
- **暗色/亮色主题** — 一键切换，毛玻璃半透明面板，设置更改后窗口状态保持

## 📥 下载安装

### 安装版（推荐）
下载 `YouBoard_Setup_v1.6.0.exe`，双击安装，自动创建快捷方式和卸载程序。
覆盖安装时自动保留所有用户数据（剪贴板历史、配置、背景图、快捷键设置）。

### 便携版
下载 `YouBoard.exe`，放到任意目录双击即可运行，无需安装。

👉 [前往 Releases 下载](https://github.com/cloudxys/YouBoard/releases)

## 🖥️ 系统要求

- Windows 10 / 11（64 位）
- 无需额外运行环境（EXE 已打包所有依赖）

## 🛠️ 开发者指南

### 环境准备

```bash
pip install PyQt6 pillow pyperclip keyboard pyinstaller
```

### 本地运行

```bash
python youboard_qt.py
```

### 打包 EXE

```bash
pyinstaller --noconsole --onefile --name YouBoard --icon=YouBoard.ico --add-data "YouBoard.ico;." --version-file=version_info.txt --hidden-import=PyQt6 --hidden-import=PyQt6.QtWidgets --hidden-import=PyQt6.QtCore --hidden-import=PyQt6.QtGui --hidden-import=keyboard youboard_qt.py
```

或直接双击 `YouBoard.bat` 一键打包。

### 生成安装包

安装 [Inno Setup 7](https://jrsoftware.org/isdl.php) 后，打开 `youboard_setup.iss` 编译即可。

输出：`YouBoard_Setup_v1.6.0.exe`

## 📁 项目结构

```
YouBoard/
├── youboard_qt.py       # 主程序（PyQt6 GUI 界面）
├── youboard_core.py     # 核心逻辑（监控、存储、Win32 API）
├── YouBoard.ico         # 应用图标
├── version_info.txt     # EXE 版本信息（v1.6.0）
├── YouBoard.bat         # 一键打包脚本
├── YouBoard.spec        # PyInstaller 配置
├── youboard_setup.iss   # Inno Setup 安装脚本（v1.6.0）
├── youboard_config.json # 用户配置（自动生成）
└── .youboard.json       # 剪贴板历史数据（自动生成）
```

## 📜 更新日志

### YouBoard v1.6.0

- ⚡ **剪贴板实时监控增强**
  - 双重保障机制：Win32 AddClipboardFormatListener 事件驱动 + GUI 层直接轮询
  - Ctrl+C 后内容立即显示在列表中，不再遗漏
  - 监控线程异常时自动降级为轮询模式，确保永不丢失
- ⌨️ **全局快捷键（keyboard 库重写）**
  - 基于底层键盘钩子（WH_KEYBOARD_LL），可靠拦截全局按键
  - 支持 Win+键组合（如 Win+F8），不再受 Windows Shell 拦截影响
  - 设置中可自定义组合键（支持 alt/ctrl/shift/win + 字母/数字/F1-F12）
  - 录入时自动检测系统保留快捷键及其他应用占用，红色"⚠已占用"警告
  - 修改快捷键后立即生效，无需重启
- 🖼️ **图片预览实时缩放**
  - 图片跟随预览框大小等比缩放（可放大可缩小，比例不变）
  - 基于 QPixmap 缓存 + GPU 加速缩放，拖拽分割条/调整窗口大小时即时响应
- 🌐 **中英文完整适配**
  - 检查更新对话框、版本提示等全部支持中英文切换
- 🪟 **窗口状态记忆**
  - 设置更改（语言/主题/背景）后窗口位置、大小、最大化状态保持不变
- 📦 **安装包增强**
  - 覆盖安装自动保留用户数据（历史、配置、背景图）
  - 安装目录生成 uninstall.exe 卸载程序
  - 安装/卸载图标使用自定义 YouBoard.ico
- 🎨 **UI 打磨**
  - 去除所有面板边框和分割线竖条，干净通透
  - 呼吸圆点改为真圆形（border-radius 实现）
  - 历史快照去除黑白交替行，统一圆角卡片风格
  - 面板透明度优化至 ~41%，自定义背景图大面积清晰显露
- 🔄 **自动更新**
  - 设置 → 关于 → 检查更新，查询 GitHub Releases 最新版本
  - 自动下载 + 替换 EXE + 重启，全程无需手动操作

### v1.5.0
- 🚀 框架迁移 tkinter → PyQt6，全面重写 UI 层
- 🎨 环境灯带 QPainter 30fps 渲染 + 窗口淡入动画
- 🖼️ 自定义背景增强（GIF 动态 + 半透明面板）
- ⚡ Model/View 架构 + 搜索防抖 + 缩略图异步加载

### v1.4.0
- 🖼️ 自定义背景：设置 → 背景 → 上传自己的图片作为界面壁纸
- 🎨 全新应用图标 YouBoard.ico
- 彻底修复首次启动任务栏/托盘显示 Python 羽毛图标的问题

### v1.2.0
- 🌐 新增网址独立分类（第 4 个 Tab）
- 🔔 新增 pystray 系统托盘（自定义图标）
- 📋 应用内复制不再产生重复记录

### v1.1.0
- 中英双语支持
- 开机自启动
- 剪贴板历史管理（文字/图片/文件）
- 历史快照与回滚

## 📄 License

MIT
