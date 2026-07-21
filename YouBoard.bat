@echo off
title YouBoard - PyInstaller Build
echo ============================================
echo   YouBoard v1.2.0 打包脚本
echo ============================================
echo.

cd /d "%~dp0"

echo [1/2] 正在打包 YouBoard.exe ...
pyinstaller --noconsole --onefile --name YouBoard --icon=You.ico --add-data "You.ico;." --version-file=version_info.txt --hidden-import=pystray youboard.py --noconfirm

if %errorlevel% neq 0 (
    echo.
    echo [错误] 打包失败，请检查 Python 环境和依赖。
    pause
    exit /b 1
)

echo.
echo [2/2] 复制 EXE 到项目根目录 ...
copy /Y "dist\YouBoard.exe" "YouBoard.exe"

echo.
echo ============================================
echo   打包完成！输出: dist\YouBoard.exe
echo ============================================
pause
