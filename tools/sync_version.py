#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把 youboard_version.py 里的版本号同步到需要它的地方。

用法（在仓库根目录）：
    python tools/sync_version.py              # 只生成构建用文件
    python tools/sync_version.py --docs       # 顺带更新 README / build_mac.sh 里的文件名
    python tools/sync_version.py --print      # 只打印版本号（CI 里取版本用）

生成物（不要手改）：
    version_info.txt      → EXE 版本资源
    version_defines.iss   → Inno Setup 的 MyAppVersion（youboard_setup.iss #include 它）
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import youboard_version  # noqa: E402  （必须在插入路径之后导入）

VERSION = youboard_version.APP_VERSION
APP_NAME = getattr(youboard_version, "APP_NAME", "YouBoard")


def write_bytes(path, data: bytes):
    """按原文件的 BOM / 行尾风格写回，避免把 .iss / .md 的编码搞坏。"""
    old = b""
    if os.path.exists(path):
        old = open(path, "rb").read()
    bom = b"\xef\xbb\xbf" if old.startswith(b"\xef\xbb\xbf") else b""
    crlf = b"\r\n" in old
    body = data
    # 传进来的 data 可能已经带 BOM（解码时 BOM 会作为 \ufeff 留在字符串里），
    # 先剥掉，最后只补一个，避免出现双 BOM（Inno 会因此报"不在任何段落里"）
    while body.startswith(b"\xef\xbb\xbf"):
        body = body[3:]
    if crlf:
        body = body.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    open(path, "wb").write(bom + body)


def gen_version_info():
    """PyInstaller 的版本资源（结构照抄原先手写的 version_info.txt）。"""
    v4 = VERSION + ".0"
    text = f"""# UTF-8
# {APP_NAME} 版本信息（打包进 EXE 的 Windows 文件属性）
# 由 tools/sync_version.py 生成，请勿手改；版本号唯一来源是 youboard_version.py
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({VERSION.replace('.', ', ')}, 0),
    prodvers=({VERSION.replace('.', ', ')}, 0),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        u'080404b0',
        [StringStruct(u'CompanyName', u'{APP_NAME}'),
         StringStruct(u'FileDescription', u'{APP_NAME} \\u526a\\u8d34\\u677f\\u5386\\u53f2\\u7ba1\\u7406\\u5668'),
         StringStruct(u'FileVersion', u'{v4}'),
         StringStruct(u'InternalName', u'{APP_NAME}'),
         StringStruct(u'LegalCopyright', u'{APP_NAME}'),
         StringStruct(u'OriginalFilename', u'{APP_NAME}.exe'),
         StringStruct(u'ProductName', u'{APP_NAME}'),
         StringStruct(u'ProductVersion', u'{v4}')])
    ]),
    VarFileInfo([VarStruct(u'Translation', [2052, 1200])])
  ]
)
"""
    write_bytes(os.path.join(ROOT, "version_info.txt"), text.encode("utf-8"))
    return "version_info.txt"


def gen_version_defines():
    """Inno Setup 只认自己的预处理语法，这里生成一个只含 #define 的小文件。"""
    text = (
        "; 由 tools/sync_version.py 生成，请勿手改\n"
        f'#define MyAppVersion "{VERSION}"\n'
        f'#define MyAppName "{APP_NAME}"\n'
    )
    path = os.path.join(ROOT, "version_defines.iss")
    with open(path, "wb") as f:      # ASCII 内容，不需要 BOM
        f.write(text.encode("utf-8"))
    return "version_defines.iss"


DOC_TARGETS = ("README.md", "README_MAC.md", "build_mac.sh")


def sync_docs():
    """把文档里"下载/产物文件名"的版本号换成当前版本（不动历史更新日志）。"""
    pats = [
        (re.compile(r"YouBoard_Setup_v\d+\.\d+\.\d+"), f"YouBoard_Setup_v{VERSION}"),
        (re.compile(r"YouBoard_macOS_arm64_v\d+\.\d+\.\d+"),
         f"YouBoard_macOS_arm64_v{VERSION}"),
        (re.compile(r"YouBoard_macOS_x86_64_v\d+\.\d+\.\d+"),
         f"YouBoard_macOS_x86_64_v{VERSION}"),
        (re.compile(r"YouBoard_macOS_v\d+\.\d+\.\d+"),
         f"YouBoard_macOS_v{VERSION}"),
    ]
    touched = []
    for name in DOC_TARGETS:
        path = os.path.join(ROOT, name)
        if not os.path.exists(path):
            continue
        old = open(path, "rb").read()
        text = old.decode("utf-8")
        new = text
        for pat, rep in pats:
            new = pat.sub(rep, new)
        if new != text:
            write_bytes(path, new.encode("utf-8"))
            touched.append(name)
    # 安装脚本头部注释里也不留版本号（版本只存在 version_defines.iss 一处）
    iss_path = os.path.join(ROOT, "youboard_setup.iss")
    if os.path.exists(iss_path):
        old = open(iss_path, "rb").read().decode("utf-8")
        new = re.sub(r"; YouBoard v\d+\.\d+\.\d+ Inno Setup 安装脚本",
                     "; YouBoard Inno Setup 安装脚本", old)
        if new != old:
            write_bytes(iss_path, new.encode("utf-8"))
            touched.append("youboard_setup.iss")
    return touched


def main():
    args = sys.argv[1:]
    if "--print" in args:
        print(VERSION)
        return
    made = [gen_version_info(), gen_version_defines()]
    if "--docs" in args:
        made += sync_docs()
    print("版本号: %s" % VERSION)
    print("已同步: " + ", ".join(made))


if __name__ == "__main__":
    main()
