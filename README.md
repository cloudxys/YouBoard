# YouBoard - 剪贴板历史管理工具

一款轻量级 Windows 剪贴板管理工具，自动记录复制历史，支持文字、图片、文件、网址四大分类，随取随用。

## ✨ 功能特性

- **剪贴板监控** — 自动捕获复制内容，实时监控中状态指示灯闪烁
- **四大分类** — 文字 / 图片 / 文件 / 网址，独立 Tab 管理
- **网址智能识别** — 纯 URL 自动归入网址分类，混合内容双存不丢失
- **系统托盘** — 原生 QSystemTrayIcon 托盘（自定义 ICO 图标），右键快速操作
- **复制去重** — 应用内复制不产生重复记录
- **预览面板** — 文字/网址/图片实时预览，支持滚轮滚动
- **历史快照** — 记录删除/清空操作，支持一键回滚，新条目淡入动画
- **中英双语** — 设置中一键切换语言
- **开机自启动** — 设置中开关控制
- **快捷键** — Enter 复制、Del 删除、Space 置顶、Ctrl+A 全选、Ctrl+O 打开、F5 刷新
- **环境灯带** — 全宽 RGB 灯带动效，呼吸流转 + 按键波纹 + 操作浪涌（QPainter 30fps）
- **自定义背景** — 支持 PNG/JPG/BMP 静态 + GIF 动态背景，面板半透明通透显露
- **暗色/亮色主题** — 一键切换，毛玻璃半透明面板，设置更改后窗口状态保持

## 📥 下载安装

### 安装版（推荐）
下载 `YouBoard_Setup_v1.5.0.exe`，双击安装，自动创建快捷方式和卸载程序。

### 便携版
下载 `YouBoard.exe`，放到任意目录双击即可运行，无需安装。

👉 [前往 Releases 下载](https://github.com/cloudxys/YouBoard/releases)

## 🖥️ 系统要求

- Windows 10 / 11（64 位）
- 无需额外运行环境（EXE 已打包所有依赖）

## 🛠️ 开发者指南

### 环境准备

```bash
pip install PyQt6 pillow pyperclip pyinstaller
```

### 本地运行

```bash
python youboard_qt.py
```

### 打包 EXE

```bash
pyinstaller --noconsole --onefile --name YouBoard --icon=YouBoard.ico --add-data "YouBoard.ico;." --version-file=version_info.txt --hidden-import=PyQt6 --hidden-import=PyQt6.QtWidgets --hidden-import=PyQt6.QtCore --hidden-import=PyQt6.QtGui youboard_qt.py
```

或直接双击 `YouBoard.bat` 一键打包。

### 生成安装包

安装 [Inno Setup 7](https://jrsoftware.org/isdl.php) 后，打开 `youbord_setup.iss` 编译即可。

输出：`YouBoard_Setup_v1.5.0.exe`

## 📁 项目结构

```
YouBoard/
├── youboard_qt.py       # 主程序（PyQt6 GUI 界面）
├── youboard_core.py     # 核心逻辑（监控、存储、Win32 API）
├── YouBoard.ico         # 应用图标
├── version_info.txt     # EXE 版本信息（v1.5.0）
├── YouBoard.bat         # 一键打包脚本
├── youboard_setup.iss   # Inno Setup 安装脚本（v1.5.0）
├── youboard_config.json # 用户配置（自动生成）
└── .youboard.json       # 剪贴板历史数据（自动生成）
```

## 📜 更新日志

### YouBoard v1.5.0

- 🚀 **框架迁移：tkinter → PyQt6**，全面重写 UI 层
  - 原生 QSystemTrayIcon 系统托盘（替代 pystray）
  - QPropertyAnimation 窗口淡入动画
  - QSS 样式表驱动主题系统（暗色/亮色）
  - 毛玻璃半透明面板（~41% 不透明度），自定义背景图大面积通透显露
- 🎨 **UI 动效升级**
  - 环境灯带：QPainter 硬件加速渲染，30fps 呼吸流转 + 按键波纹 + 操作浪涌
  - 窗口淡入动画（opacity 渐变）
  - 呼吸状态圆点（真圆形，绿色闪烁）
  - 历史快照新条目淡入高亮动画（600ms 渐变）
- 🖼️ **自定义背景增强**
  - 支持 PNG/JPG/BMP 静态背景 + GIF 动态背景（QMovie 驱动）
  - 面板/表头/网格线全部半透明，背景图清晰可见
  - 防抖缩放，窗口调整时不卡顿
- 🔧 **UI 细节打磨**
  - 设置对话框完整适配暗色/亮色主题
  - 无竖条/无边框干净面板，透明间距分隔
  - 实时监控状态始终显示（不再误报"已停止"）
  - 设置更改后窗口状态保持（最大化/尺寸/位置不重置）
- ⚡ **性能优化**
  - Model/View 架构，DISPLAY_LIMIT 400 行虚拟渲染
  - 搜索防抖 160ms，缩略图异步加载（QThread）
  - 滚动期间灯带自动暂停，保证列表满帧
- 📦 打包体积优化：排除 tkinter/pystray，仅打包 PyQt6 必要模块

### v1.4.0
- 🖼️ 自定义背景：设置 → 背景 → 上传自己的图片作为界面壁纸
- 🎨 全新应用图标 YouBoard.ico
- 彻底修复首次启动任务栏/托盘显示 Python 羽毛图标的问题
- 安装包使用新图标，现代向导界面

### v1.2.0
- 🌐 新增网址独立分类（第 4 个 Tab）
- 🔔 新增 pystray 系统托盘（自定义图标）
- 📋 应用内复制不再产生重复记录
- 🖱️ 预览面板支持鼠标滚轮滚动

### v1.1.0
- 中英双语支持
- 开机自启动
- 剪贴板历史管理（文字/图片/文件）
- 历史快照与回滚

## 📄 License

MIT
