# -*- mode: python ; coding: utf-8 -*-
"""YouBoard macOS 打包配置（PyInstaller，需在 macOS 上执行）。

用法（macOS 终端）：
    python3 -m pip install --user PyQt6 pillow pyperclip \
        pyobjc-framework-Cocoa pyobjc-framework-Quartz pyinstaller
    python3 -m PyInstaller YouBoard_Mac.spec --noconfirm
产物：dist/YouBoard.app
"""


a = Analysis(
    ['youboard_qt.py'],
    pathex=[],
    binaries=[],
    datas=[('YouBoard.ico', '.'),
           ('res/zuixiao.ico', 'res'),
           ('res/zuida.ico', 'res'),
           ('res/zuidahuifu.ico', 'res'),
           ('res/guanbi.ico', 'res'),
           ('res/shezhi.ico', 'res'),
           ('res/wenben.ico', 'res'),
           ('res/tupian.ico', 'res'),
           ('res/wenjian.ico', 'res'),
           ('res/wangzhi.ico', 'res'),
           ('res/sousuo.ico', 'res'),
           ('res/anse.ico', 'res'),
           ('res/liangse.ico', 'res'),
           ('res/jinggao.ico', 'res')],
    hiddenimports=['PyQt6', 'PyQt6.QtWidgets', 'PyQt6.QtCore', 'PyQt6.QtGui',
                   'AppKit', 'Quartz', 'Foundation', 'objc'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['numpy'],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='YouBoard',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['YouBoard.icns'],
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='YouBoard',
)

app = BUNDLE(
    coll,
    name='YouBoard.app',
    icon='YouBoard.icns',
    bundle_identifier='com.youboard.app',
    info_plist={
        'CFBundleDisplayName': 'YouBoard',
        'CFBundleName': 'YouBoard',
        'CFBundleShortVersionString': '2.3.0',
        'CFBundleVersion': '2.3.0',
        'NSHighResolutionCapable': True,
        'LSMinimumSystemVersion': '11.0',
        'NSHumanReadableCopyright': 'YouBoard - Clipboard History Manager',
    },
)
