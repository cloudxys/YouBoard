# YouBoard - 剪贴板历史管理工具

一款轻量级 Windows 剪贴板管理工具，自动记录复制历史，支持文字、图片、文件、网址四大分类，随取随用。

## ✨ 功能特性

- **剪贴板监控** — 自动捕获复制内容，无需手动保存
- **四大分类** — 文字 / 图片 / 文件 / 网址，独立 Tab 管理
- **网址智能识别** — 纯 URL 自动归入网址分类，混合内容双存不丢失
- **系统托盘** — 关闭窗口最小化到托盘（自定义 ICO 图标），右键快速操作
- **复制去重** — 应用内复制不产生重复记录
- **预览面板** — 文字/网址/图片实时预览，支持滚轮滚动
- **历史快照** — 记录删除/清空操作，支持一键回滚
- **中英双语** — 设置中一键切换语言
- **开机自启动** — 设置中开关控制
- **快捷键** — Enter 复制、Del 删除、Ctrl+A 全选、Ctrl+O 打开

## 📥 下载安装

### 安装版（推荐）
下载 `YouBoard_Setup_v1.2.0.exe`，双击安装，自动创建快捷方式和卸载程序。

### 便携版
下载 `YouBoard.exe`，放到任意目录双击即可运行，无需安装。

👉 [前往 Releases 下载](https://github.com/cloudxys/YouBoard/releases)

## 🖥️ 系统要求

- Windows 10 / 11（64 位）
- 无需额外运行环境（EXE 已打包所有依赖）

## 🛠️ 开发者指南

### 环境准备

```bash
pip install pyinstaller pystray pillow pyperclip numpy
```

### 本地运行

```bash
python youboard.py
```

### 打包 EXE

```bash
pyinstaller --noconsole --onefile --name YouBoard --icon=You.ico --add-data "You.ico;." --version-file=version_info.txt --hidden-import=pystray youboard.py
```

或直接双击 `YouBoard.bat` 一键打包。

### 生成安装包

安装 [Inno Setup 7](https://jrsoftware.org/isdl.php) 后执行：

```powershell
& "C:\Program Files\Inno Setup 7\ISCC.exe" youboard_setup.iss
```

输出：`YouBoard_Setup_v1.2.0.exe`

## 📁 项目结构

```
YouBoard/
├── youboard.py          # 主程序（GUI 界面）
├── youboard_core.py     # 核心逻辑（监控、存储、托盘）
├── You.ico              # 应用图标
├── version_info.txt     # EXE 版本信息
├── YouBoard.bat         # 一键打包脚本
├── youboard_setup.iss   # Inno Setup 安装脚本
└── .youboard.json       # 运行时配置（自动生成）
```

## 📜 更新日志

### v1.2.0
- 🌐 新增网址独立分类（第 4 个 Tab）
- 🔔 新增 pystray 系统托盘（自定义图标）
- 🎨 任务栏/托盘/窗口图标统一为 You.ico
- 📋 应用内复制不再产生重复记录
- 🖱️ 预览面板支持鼠标滚轮滚动
- 🔗 文字预览中 URL 高亮可点击

### v1.1.0
- 中英双语支持
- 开机自启动
- 剪贴板历史管理（文字/图片/文件）
- 历史快照与回滚

## 📄 License

MIT
