# YouBoard macOS 版（构建与使用说明）

这是 YouBoard 的 macOS 适配工程，由 Windows 版源码移植而来，界面、功能和
快捷键习惯尽量保持一致。

## 1. 前提条件（需要在 Mac 上操作）

- macOS 11（Big Sur）或更高版本，Intel / Apple Silicon 均可
- Python 3.10+（推荐 [python.org 官方安装包](https://www.python.org/downloads/)，
  自带 PyObjC 依赖支持）

> 注意：PyInstaller 不能跨系统打包。Windows 上无法生成 .app，
> 必须在 Mac 上执行本目录下的构建脚本。

## 2. 一键构建

把 `YouBoard_Mac` 整个文件夹拷贝到 Mac 上，打开「终端」进入该目录：

```bash
chmod +x build_mac.sh
./build_mac.sh
```

脚本会：安装依赖（PyQt6 / Pillow / pyperclip / PyObjC / PyInstaller）→
构建 `dist/YouBoard.app` → 生成 `YouBoard_macOS_v2.4.0.dmg`。

也可以手动执行：

```bash
python3 -m pip install --user PyQt6 pillow pyperclip \
    pyobjc-framework-Cocoa pyobjc-framework-Quartz pyinstaller
python3 -m PyInstaller YouBoard_Mac.spec --noconfirm
```

## 3. 首次运行的权限

- **全局快捷键**：YouBoard 用系统级按键监听（CGEventTap）实现 Alt+Q 呼出。
  首次使用请在「系统设置 → 隐私与安全性 → 辅助功能」中把
  终端（开发运行）或 YouBoard.app（打包版）加入白名单；
  未授权时全局热键不生效，其余功能不受影响。
- **开机自启动**：设置里勾选后写入
  `~/Library/LaunchAgents/com.youboard.app.plist`，如需撤销可直接删除该文件。

## 4. 数据与配置位置

- 打包版数据目录：`~/Library/Application Support/YouBoard`
  （剪贴板历史 `.youboard.json`、快照、配置、图片缓存等）
- 开发运行（`python3 youboard_qt.py`）：数据保存在脚本同目录

## 5. 与 Windows 版一致的实现方式

- 剪贴板监控：macOS 用 NSPasteboard 的 changeCount 轮询，文本 / 图片 / 文件
  均可自动收录，行为与 Windows 版一致
- 复制回剪贴板：文本 / 图片 / 文件均走 NSPasteboard（图片为 PNG，文件为
  file:// URL），粘贴到微信、浏览器、Finder 均正常
- 桌面小组件：最小值 90×60，位置与尺寸自动记忆，退出重开保持原样
- 标题栏：无边框自绘标题栏、最小化 / 最大化 / 关闭按钮、拖拽移动、边缘缩放
- 托盘：原生 QSystemTrayIcon，右键显示 / 隐私模式 / 退出
- 系统要求里的全局快捷键：`win` 键对应 Mac 的 Command（⌘）

## 6. 已知差异（Windows 专属能力）

- **应用内自动更新**：macOS 版检测到新版本后直接打开 GitHub Releases 页面，
  不会应用内替换可执行文件（避免破坏签名）
- **压缩包内部复制物化**（从 7-Zip / WinRAR 压缩包内复制文件并自动提取）：
  这是 Windows 剪贴板 OLE 专属格式，macOS 无此剪贴板格式，Finder 内复制文件
  走正常文件路径，不影响「文件」分类
- **Win11 托盘图标提升 / 注册表项**：仅在 Windows 生效，macOS 无对应概念

## 7. 需要 Mac 实测验证的点

- 全局热键（Alt+Q 及自定义组合）在未授权 / 已授权两种情况下的行为
- 高清屏（Retina）下无边框窗口的缩放手感
- 图片从浏览器 / 微信复制后是否正常归入「图片」分类
- 文件多选复制后归入「文件」分类及打开/定位行为
