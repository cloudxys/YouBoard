# -*- coding: utf-8 -*-
"""YouBoard 版本号的唯一来源。

以后改版本**只改这里**，然后在仓库根目录跑一次：

    python tools/sync_version.py --docs

它会同步这几处（都是自动生成，别手改）：
    version_info.txt          EXE 的版本资源（PyInstaller 用）
    version_defines.iss       安装包的 MyAppVersion（Inno Setup #include 用）
    README / README_MAC       下载说明里的安装包文件名
    build_mac.sh              DMG 文件名

程序本身（youboard_qt.py）、macOS 的 CFBundleVersion、CI 产物名
都是直接读这个模块的 APP_VERSION，不需要手工同步。
"""

APP_VERSION = "3.3.3"
APP_NAME = "YouBoard"
