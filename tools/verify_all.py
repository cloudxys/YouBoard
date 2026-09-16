#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""YouBoard 全量隔离验证：语法 / 核心存储 / 手机接口 / 界面。

全部在临时目录与项目副本上运行，不读写用户真实数据；
副本里的配置会关掉提示音，避免测试时真的发声。

用法（仓库根目录或任意位置都行）：
    python tools/verify_all.py
失败时退出码为 1（CI 里作为发版门禁：不通过就不打包）。
"""
import io
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

# 项目根目录 = 本文件所在目录的上一层，换机器 / CI 上都不用改
SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAIL = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("  " + extra if extra else ""))
    if not cond:
        FAIL.append(name)


def test_syntax():
    import py_compile
    for f in ("youboard_core.py", "youboard_phone.py", "youboard_qt.py"):
        try:
            py_compile.compile(os.path.join(SRC, f), doraise=True)
            check("syntax " + f, True)
        except Exception as ex:
            # 语法错误就当场报失败（而不是抛异常），CI 里一眼能看出是哪一步拦下的
            check("syntax " + f, False, str(ex)[:160])
    # 弹层统一用圆角菜单；设置窗口不再画系统大小手柄（白点）
    qt = open(os.path.join(SRC, "youboard_qt.py"), encoding="utf-8").read()
    check("no plain popup QMenu", qt.count("menu = QMenu(self)") == 0)
    check("round menus used", qt.count("menu = _RoundMenu(self)") >= 4)
    check("no size grip", "setSizeGripEnabled(True)" not in qt)
    check("global paste hook wired", "_apply_paste_sound_hook" in qt
          and "_on_global_paste" in qt)
    check("copy sound on capture wired",
          'self._play_sound("copy")' in qt)
    check("no system sound fallback",
          "_SOUND_KIND_ALIAS" not in qt and "QApplication.beep()" not in qt)


def test_core():
    sys.path.insert(0, SRC)
    import youboard_core as yc
    tmp = tempfile.mkdtemp(prefix="yb_core_")
    yc.CONTENT_DIR = os.path.join(tmp, "content")
    yc.IMAGES_DIR = os.path.join(tmp, "images")
    yc.FILE_CACHE_DIR = os.path.join(tmp, "file_cache")
    yc.KEY_FILE = os.path.join(tmp, "youboard.key")
    os.makedirs(yc.IMAGES_DIR, exist_ok=True)
    hist = os.path.join(tmp, ".youboard.json")
    store = yc.ClipboardStore(path=hist)
    store.add_text("hello world")
    check("core small inline",
          store.get_by_type("text")[0].get("content") == "hello world")
    big = ("A" * 300000) + "NEEDLE_MARKER"
    store.add_text(big)
    be = [e for e in store.get_by_type("text") if e.get("content_ref")]
    check("core big externalized", len(be) == 1)
    check("core full text", store.get_text(be[0]) == big if be else False)
    check("core search deep", len(store.search("needle_marker", None)) == 1)
    store.flush()
    check("core history small", os.path.getsize(hist) < 100 * 1024)
    legacy = {"version": 2, "categories": {
        "text": {"pinned": [], "entries": [
            {"hash": "a" * 64, "type": "text", "content": "B" * 400000,
             "timestamp": "2026-01-01T00:00:00", "length": 400000}]},
        "image": {"pinned": [], "entries": []},
        "file": {"pinned": [], "entries": []},
        "url": {"pinned": [], "entries": []}}}
    with open(hist, "wb") as f:
        f.write(yc._encrypt_data(json.dumps(legacy).encode("utf-8")))
    before = os.path.getsize(hist)
    store2 = yc.ClipboardStore(path=hist)
    check("core migration", os.path.getsize(hist) < before
          and any(e.get("content_ref") for e in store2.get_by_type("text")))
    other = os.path.join(tmp, "other")
    os.makedirs(os.path.join(other, "images"), exist_ok=True)
    shutil.copy2(yc.KEY_FILE, os.path.join(other, "youboard.key"))
    with open(os.path.join(other, ".youboard.json"), "wb") as f:
        f.write(yc._encrypt_data(json.dumps(legacy).encode("utf-8")))
    check("core inspect install",
          bool(yc.inspect_installation(other)) and
          yc.inspect_installation(other).get("readable"))
    import youboard_phone as yp
    yp.PHONE_INCOMING_DIR = os.path.join(tmp, "file_cache", "phone")
    check("upload safe name", yp._safe_upload_name("../../evil.txt") == "evil.txt")
    buf = io.BytesIO()
    from PIL import Image as _I
    _I.new("RGB", (8, 8), (255, 0, 0)).save(buf, format="PNG")
    check("upload decode", yp._decode_upload_image(buf.getvalue()) is not None)
    check("upload decode junk", yp._decode_upload_image(b"xx") is None)


def test_phone():
    import youboard_core as yc
    import youboard_phone as yp
    tmp = tempfile.mkdtemp(prefix="yb_phone_")
    yc.CONTENT_DIR = os.path.join(tmp, "content")
    yc.IMAGES_DIR = os.path.join(tmp, "images")
    yc.FILE_CACHE_DIR = os.path.join(tmp, "file_cache")
    yc.KEY_FILE = os.path.join(tmp, "youboard.key")
    os.makedirs(yc.IMAGES_DIR, exist_ok=True)
    yp.IMAGES_DIR = yc.IMAGES_DIR
    yp.PHONE_INCOMING_DIR = os.path.join(yc.FILE_CACHE_DIR, "phone")
    store = yc.ClipboardStore(path=os.path.join(tmp, ".youboard.json"))
    store.add_text("pc text")
    got = {"image": [], "file": [], "text": []}
    srv = yp.PhoneTransferServer(
        store, on_receive_text=lambda t: got["text"].append(t),
        port=yp.pick_free_port(8765),
        on_receive_image=lambda i, n: got["image"].append((i.size, n)),
        on_receive_file=lambda p, n: got["file"].append((p, n)))
    check("phone server", srv.start() and srv.wait_ready(5.0))
    base = "http://127.0.0.1:%d" % srv.port
    tok = srv.token

    def post(path, data, headers, timeout=10):
        req = urllib.request.Request(base + path, data=data, headers=headers,
                                     method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, json.loads(r.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read().decode("utf-8") or "{}")
            except Exception:
                return e.code, {}

    with urllib.request.urlopen(base + "/?t=" + tok, timeout=5) as r:
        page = r.read().decode("utf-8", "replace")
    check("page served", "YouBoard" in page)
    check("page clipboard btn on top",
          page.index('id="clipBtn"') < page.index('id="list"'))
    check("page actions bar", 'class="actions"' in page)
    check("page img retry fn", "function imgFail(" in page and "data-src" in page)
    check("page no lazy img", 'loading="lazy"' not in page)
    check("page paste auto send", "addEventListener('paste'" in page)
    check("page render skip unchanged", "LAST_SIG" in page)

    from PIL import Image as _I
    buf = io.BytesIO()
    _I.new("RGB", (12, 10), (0, 128, 255)).save(buf, format="PNG")
    code, body = post("/api/upload", buf.getvalue(),
                      {"X-YouBoard-Token": tok,
                       "X-File-Name": urllib.parse.quote("手机图.png"),
                       "Content-Type": "image/png"})
    check("phone image upload", code == 200 and body.get("ok") is True, str(body))
    code, _ = post("/api/upload", b"file-bytes",
                   {"X-YouBoard-Token": tok, "X-File-Name": "r.txt"})
    check("phone file upload", code == 200 and got["file"])
    code, _ = post("/api/upload", b"x", {"X-File-Name": "a.txt"})
    check("phone auth", code == 401)
    old = yp.UPLOAD_MAX_BYTES
    yp.UPLOAD_MAX_BYTES = 8
    code, _ = post("/api/upload", b"y" * 64, {"X-YouBoard-Token": tok,
                                              "X-File-Name": "big.bin"})
    yp.UPLOAD_MAX_BYTES = old
    check("phone oversize", code == 413)
    code, body = post("/api/upload", b"not-image",
                      {"X-YouBoard-Token": tok, "X-File-Name": "f.png",
                       "Content-Type": "image/png"})
    check("phone bad image", code == 400 and body.get("err") == "bad_image")
    code, _ = post("/api/send", json.dumps({"text": "hi"}).encode(),
                   {"X-YouBoard-Token": tok, "Content-Type": "application/json"})
    check("phone text send", code == 200 and got["text"] == ["hi"])
    pic = _I.new("RGB", (10, 10), (9, 9, 9))
    store.add_image(pic, store._image_hash(pic), "t.png")
    img_entry = store.get_by_type("image")[0]
    store.flush()
    with urllib.request.urlopen(
            base + "/api/img/%s.png?t=%s" % (img_entry["hash"], tok),
            timeout=5) as r:
        check("phone image 200", r.status == 200 and len(r.read()) > 0)
        check("phone image cacheable", "max-age" in r.headers.get("Cache-Control", ""))
    srv.stop()
    time.sleep(0.2)


def test_gui():
    tmp = tempfile.mkdtemp(prefix="yb_gui_")
    dst = os.path.join(tmp, "YouBoard")
    shutil.copytree(SRC, dst, ignore=shutil.ignore_patterns(
        "__pycache__", "build", "dist", "*.exe", ".git", "jieshao",
        "YouBoard_Setup*"))
    cfg_path = os.path.join(dst, "youboard_config.json")
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        cfg["bg_image"] = ""
        cfg["bg_history"] = []
        cfg["snd_copy"] = False      # 测试期间不要真的发声
        cfg["snd_paste"] = False
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False)
    except Exception:
        pass
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    for name in [m for m in sys.modules if m.startswith("youboard")]:
        del sys.modules[name]
    sys.path = [p for p in sys.path if os.path.abspath(p) != os.path.abspath(SRC)]
    sys.path.insert(0, dst)
    import youboard_qt as yq
    from PyQt6.QtGui import QImage

    app = yq.QApplication(sys.argv)
    store = yq.ClipboardStore()
    win = yq.YouBoardApp(store, None)
    win.resize(1200, 760)
    win.show()
    for _ in range(30):
        app.processEvents()
        time.sleep(0.02)
    win._initial_refresh()
    for _ in range(10):
        app.processEvents()

    check("gui five tabs", win._tabs.count() == 5)
    check("gui default all", win._active_type == "all")

    def first_text_x(img, y0, y1, x0, x1, tol=40):
        def rgb(x, y):
            c = img.pixelColor(x, y)
            return (c.red(), c.green(), c.blue())
        refs = [rgb(x, y) for y in range(y0, y1)
                for x in range(x0, min(x1, x0 + 6))]
        if not refs:
            return None
        bg = tuple(sum(c[i] for c in refs) // len(refs) for i in range(3))
        for x in range(x0, x1):
            hits = sum(1 for y in range(y0, y1)
                       if sum(abs(rgb(x, y)[i] - bg[i]) for i in range(3)) > tol)
            if hits >= 2:
                return x
        return None

    for tab in yq.TAB_TYPES:
        win._tabs.setCurrentIndex(yq.TAB_TYPES.index(tab))
        app.processEvents()
        table = win._tables[tab]
        flex = win._flex_cols[tab]
        if tab in ("all", "file"):
            # 「全部」和「文件」列首会画圆角标记（类型 / 细分），标记会占掉一段宽度，
            # 这里先摘掉标记，专门量"正文起点是否和表头对齐"这条不变量
            for i in range(min(4, table.rowCount())):
                cell = table.item(i, flex)
                if cell is not None:
                    cell.setData(yq._InlineImageDelegate.BADGE_ROLE, None)
            app.processEvents()
        col_x = table.horizontalHeader().sectionViewportPosition(flex)
        pix = table.grab()
        img = pix.toImage().convertToFormat(QImage.Format.Format_RGB32)
        hh = table.horizontalHeader().height()
        row_h = table.rowHeight(0) if table.rowCount() else 20
        header_x = first_text_x(img, 2, hh - 2, col_x, col_x + 300)
        row_xs = [first_text_x(img, hh + i * row_h + 3,
                               hh + (i + 1) * row_h - 3, col_x, col_x + 300)
                  for i in range(min(4, table.rowCount()))]
        check("align %s" % tab, bool(row_xs) and all(x == header_x for x in row_xs),
              "header=%s rows=%s" % (header_x, row_xs))

    win._tabs.setCurrentIndex(0)
    app.processEvents()
    all_table = win._tables["all"]
    check("all badge",
          all_table.item(0, 3).data(yq._InlineImageDelegate.BADGE_ROLE)
          in ("文本", "图片", "文件", "网址"))
    check("all no bracket", not all_table.item(0, 3).text().startswith("["))

    # 快速面板已移除；Win+V 改为开关主窗口
    check("quick panel removed",
          not hasattr(yq, "QuickPanel")
          and not hasattr(win, "_open_quick_panel")
          and not hasattr(yq, "_QuickItemDelegate"))
    src = open(os.path.join(SRC, "youboard_qt.py"), encoding="utf-8").read()
    check("winv opens main window",
          '_WinVHook(self._on_hotkey_threadsafe)' in src
          and 'self._on_hotkey_threadsafe,\n                        suppress=True)' in src)
    vis0 = win.isVisible()
    win._on_hotkey_threadsafe()
    for _ in range(30):
        app.processEvents()
        time.sleep(0.02)
    check("winv/hotkey toggles main window", win.isVisible() != vis0,
          "before=%s after=%s" % (vis0, win.isVisible()))
    win._on_hotkey_threadsafe()
    for _ in range(30):
        app.processEvents()
        time.sleep(0.02)
    check("hotkey toggles back", win.isVisible() == vis0)

    dlg = yq.SettingsDialog(win)
    check("settings sound cbs", set(dlg._snd_cbs) == {"copy", "paste"})
    check("settings default sound is builtin",
          dlg._snd_src["copy"] in (yq.SOUND_SRC_BUILTIN, yq.SOUND_SRC_CUSTOM),
          dlg._snd_src["copy"])
    check("builtin wavs bundled",
          bool(yq.builtin_sound_path("copy")) and bool(yq.builtin_sound_path("paste")))
    # 关闭粘贴提示音时不应挂着全局监听
    dlg._snd_cbs["paste"].setChecked(False)
    win._apply_paste_sound_hook()
    check("paste hook off when disabled",
          getattr(win, "_paste_hook_name", None) is None)
    check("sound plays builtin", yq.play_notify_sound("copy", yq.SOUND_SRC_BUILTIN))
    check("sound custom missing falls back to builtin",
          yq.play_notify_sound("copy", yq.SOUND_SRC_CUSTOM, ""))
    check("sound legacy system source falls back",
          yq.play_notify_sound("copy", "system"))
    shot = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "shot_final.png")
    try:
        win._tabs.setCurrentIndex(0)
        app.processEvents()
        win.grab().save(shot)
        check("gui screenshot", True)
    except Exception as ex:
        check("gui screenshot", False, str(ex))
    for w in (dlg, win):
        try:
            w.close()
        except Exception:
            pass


def main():
    print("== syntax ==")
    test_syntax()
    if FAIL:            # 语法都没过，后面的用例不用跑了
        print("RESULT=FAILED:" + ",".join(FAIL))
        return 1
    print("== core ==")
    test_core()
    print("== phone ==")
    test_phone()
    print("== gui ==")
    test_gui()
    print("RESULT=" + ("ALL_PASS" if not FAIL else "FAILED:" + ",".join(FAIL)))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
