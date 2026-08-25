@echo off
title YouBoard - PyInstaller Build
echo ============================================
echo   YouBoard v2.7.0 打包脚本 (PyQt6)
echo ============================================
echo.

cd /d "%~dp0"

echo [1/2] 正在打包 YouBoard.exe ...
pyinstaller --noconsole --onefile --name YouBoard --icon=YouBoard.ico --add-data "YouBoard.ico;." --version-file=version_info.txt --hidden-import=PyQt6 --hidden-import=PyQt6.QtWidgets --hidden-import=PyQt6.QtCore --hidden-import=PyQt6.QtGui --hidden-import=keyboard youboard_qt.py --noconfirm

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
