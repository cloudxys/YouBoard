#!/bin/bash
# ===========================================================================
# YouBoard macOS 一键构建脚本（必须在 macOS 上运行）
# 产物：dist/YouBoard.app（可选：YouBoard_macOS_v3.3.2.dmg）
# ===========================================================================
set -e
cd "$(dirname "$0")"

echo "==> 安装/更新依赖（Python 3.10+）"
python3 -m pip install --user --upgrade PyQt6 pillow pyperclip \
    pyobjc-framework-Cocoa pyobjc-framework-Quartz cryptography pyinstaller qrcode

echo "==> 清理旧构建产物"
rm -rf build dist

echo "==> PyInstaller 构建 .app"
python3 -m PyInstaller YouBoard_Mac.spec --noconfirm

APP="dist/YouBoard.app"
echo "==> 构建完成：$APP"
echo "    主程序：$APP/Contents/MacOS/YouBoard"
echo "    数据目录：~/Library/Application Support/YouBoard"

if command -v hdiutil >/dev/null 2>&1; then
echo "==> 生成 DMG 镜像"
# 版本号唯一来源：youboard_version.py（不用手改这里）
VERSION="$(python3 -c 'import youboard_version; print(youboard_version.APP_VERSION)')"
DMG="YouBoard_macOS_v${VERSION}.dmg"
    rm -f "$DMG"
    hdiutil create -volname "YouBoard" -srcfolder "$APP" -ov -format UDZO "$DMG"
    echo "==> DMG：$DMG"
fi

echo "==> 全部完成 🎉"
