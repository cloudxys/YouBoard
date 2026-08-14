# -*- mode: python ; coding: utf-8 -*-


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
    hiddenimports=['PyQt6', 'PyQt6.QtWidgets', 'PyQt6.QtCore', 'PyQt6.QtGui', 'keyboard'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['numpy'],
    noarchive=False,
    optimize=0,
)

# --- Slim the bundle: drop heavy binaries the app never uses ---
from PyInstaller.building.datastruct import TOC
_DROP = (
    'opengl32sw',          # Qt software OpenGL fallback (widgets use raster)
    '_avif', '_webp',      # Pillow AVIF / WebP codecs (only PNG/JPEG/GIF/BMP used)
    'qwebp', 'qtiff', 'qicns', 'qtga', 'qwbmp',   # unused Qt imageformat plugins
    'qt6network.dll', 'qt6svg.dll', 'qt6pdf.dll', # unused Qt modules
)
a.binaries = TOC([x for x in a.binaries
                  if not any(d in x[0].lower() for d in _DROP)])
# Qt 自带翻译只保留简体中文（英文为源语言；其余语种用不到）
a.binaries = TOC([x for x in a.binaries
                  if not x[0].lower().endswith('.qm')
                  or 'zh_cn' in x[0].lower()])

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='YouBoard',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=['Qt6*.dll', 'Qt6*.pyd', 'python3*.dll', 'vcruntime*.dll', 'msvcp*.dll'],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version='version_info.txt',
    icon=['YouBoard.ico'],
)
