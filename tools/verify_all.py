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

# CI（尤其 Windows runner）的默认输出编码不是 UTF-8，中文用例名会抛
# UnicodeEncodeError 把整个流程带崩；这里强制 UTF-8，编不出来的字符降级替换。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 项目根目录 = 本文件所在目录的上一层，换机器 / CI 上都不用改
SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAIL = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("  " + extra if extra else ""))
    if not cond:
        FAIL.append(name)


def test_syntax():
    import py_compile
    for f in ("youboard_core.py", "youboard_phone.py", "youboard_ai.py",
              "youboard_qt.py"):
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
    # AI 就地处理：模块接线 + Key 必须加密存
    ai_src = open(os.path.join(SRC, "youboard_ai.py"), encoding="utf-8").read()
    check("ai module wired",
          "from youboard_ai import" in qt and "_ai_menu_for" in qt
          and "class _AIDialog" in qt and "prepare_request" in qt)
    check("ai key protected", "protect_secret" in ai_src
          and "unprotect_secret" in ai_src)
    check("ai streaming worker", "class _AIWorker" in qt
          and "stream_chat" in ai_src)
    check("ai only openai-compatible", "chat/completions" in ai_src)


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

    # ---- 3.2.7 标签 / 收藏（纯本地） ----
    tag_store = yc.ClipboardStore(path=os.path.join(tmp, "tags.json"))
    tag_store.clear()
    tag_store.add_text("标签功能回归样本一")
    tag_store.add_text("标签功能回归样本二")
    rows = tag_store.get_by_type("text")
    th1, th2 = rows[0]["hash"], rows[1]["hash"]
    check("tags: normalize", yc.normalize_tags([" 工作 ", "#工作", ""]) == ["工作"])
    check("tags: set + read", tag_store.set_tags(th1, ["工作", "重要"])
          and yc.entry_tags([e for e in tag_store.get_by_type("text")
                             if e["hash"] == th1][0]) == ["工作", "重要"])
    check("tags: counts", tag_store.tag_counts() == [("工作", 1), ("重要", 1)],
          str(tag_store.tag_counts()))
    check("tags: search hits tag", len(tag_store.search("重要", "text")) == 1)
    check("tags: add/remove", tag_store.add_tags([th2], ["工作"]) == 1
          and len(tag_store.entries_with_tag("工作")) == 2
          and tag_store.remove_tags([th2], ["工作"]) == 1
          and len(tag_store.entries_with_tag("工作")) == 1)
    check("tags: fav toggle", tag_store.toggle_fav(th1) is True
          and tag_store.is_fav(th1) and tag_store.fav_count() == 1
          and tag_store.toggle_fav(th1) is False)
    check("tags: set_fav_many", tag_store.set_fav_many([th1, th2], True) == 2
          and tag_store.fav_count() == 2)
    tag_store.add_text("标签功能回归样本一")      # 重复复制：标签 / 收藏不能丢
    again = [e for e in tag_store.get_by_type("text") if e["hash"] == th1]
    check("tags: kept on recopy",
          len(again) == 1 and yc.entry_tags(again[0]) == ["工作", "重要"]
          and bool(again[0].get("fav")))
    tag_store.flush()
    reloaded = yc.ClipboardStore(path=os.path.join(tmp, "tags.json"))
    check("tags: persisted",
          yc.entry_tags([e for e in reloaded.get_by_type("text")
                         if e["hash"] == th1][0]) == ["工作", "重要"])
    tag_store.merge_history({"text": {"pinned": [], "entries": [
        {"hash": th1, "type": "text", "content": "标签功能回归样本一",
         "timestamp": "2030-01-01T00:00:00", "length": 9}]},
        "image": {"pinned": [], "entries": []},
        "file": {"pinned": [], "entries": []},
        "url": {"pinned": [], "entries": []}})
    merged = [e for e in tag_store.get_by_type("text") if e["hash"] == th1][0]
    check("tags: survive cloud merge",
          yc.entry_tags(merged) == ["工作", "重要"] and bool(merged.get("fav")))
    check("tags: legacy entry has none",
          all(yc.entry_tags(e) == [] for e in store2.get_by_type("text")))


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


def test_ai():
    """AI 就地处理：提示词 / 截断 / Key 加密 / 流式 / 报错 / 兼容回退（全离线）。"""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    sys.path.insert(0, SRC)
    tmp = tempfile.mkdtemp(prefix="yb_ai_")
    import youboard_core as yc
    import youboard_ai as ai
    yc.CONFIG_FILE = os.path.join(tmp, "youboard_config.json")

    req = ai.prepare_request("summarize", "会议记录：下周一评审")
    check("ai prompt built", len(req["messages"]) == 2
          and "会议记录：下周一评审" in req["messages"][1]["content"]
          and req["truncated"] is False)
    long_text = "甲" * (ai.AI_MAX_INPUT_CHARS + 10)
    req2 = ai.prepare_request("extract", long_text)
    check("ai truncates long input", req2["truncated"] is True
          and req2["sent_chars"] == ai.AI_MAX_INPUT_CHARS
          and req2["total_chars"] == len(long_text))
    check("ai translate target", "英文" in ai.prepare_request(
        "translate", "这是一段中文")["messages"][1]["content"]
        and "简体中文" in ai.prepare_request(
            "translate", "this is english")["messages"][1]["content"])
    check("ai custom prompt", "改成英文" in ai.prepare_request(
        "custom", "abc", custom_prompt="改成英文")["messages"][1]["content"])
    settings = ai.default_ai_settings()
    settings["api_key"] = "sk-secret-123"
    stored = ai.save_ai_settings(settings)
    raw = json.load(open(yc.CONFIG_FILE, encoding="utf-8"))
    check("ai key encrypted at rest",
          stored["api_key"].startswith(("dpapi:", "b64:"))
          and "sk-secret-123" not in json.dumps(raw))
    check("ai key round trip",
          ai.load_ai_settings()["api_key"] == "sk-secret-123"
          and ai.load_ai_settings()["api_key_saved"] is True)
    keep = ai.load_ai_settings()
    keep["api_key"] = ""
    ai.save_ai_settings(keep)
    check("ai empty key keeps stored one",
          ai.load_ai_settings()["api_key"] == "sk-secret-123")
    keep["api_key_saved"] = False
    ai.save_ai_settings(keep)
    check("ai explicit clear", ai.load_ai_settings()["api_key"] == "")

    class _Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _send(self, code, body):
            raw = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
            except Exception:
                payload = {}
            path = self.path
            if "/unauthorized" in path:
                return self._send(401, '{"error":{"message":"bad key"}}')
            if "/compat" in path and "max_tokens" in payload:
                return self._send(400, '{"error":{"message":"Unsupported '
                                       'parameter: max_tokens"}}')
            if payload.get("stream"):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for chunk in ("你好", "，世界"):
                    self.wfile.write(("data: %s\n\n" % json.dumps(
                        {"choices": [{"delta": {"content": chunk}}]})
                    ).encode("utf-8"))
                    self.wfile.flush()
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
                return
            self._send(200, json.dumps(
                {"choices": [{"message": {"content": "pong"}}]}))

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d/v1" % port

    def _client(path="", **over):
        cfg = ai.default_ai_settings()
        cfg.update({"base_url": base + path, "model": "test-model",
                    "api_key": "sk-test", "retries": 0, "timeout": 20})
        cfg.update(over)
        return ai.AIClient(cfg)

    got = []
    text = _client().stream_chat([{"role": "user", "content": "hi"}],
                                 on_delta=got.append)
    check("ai streaming", text == "你好，世界" and got == ["你好", "，世界"],
          str(got))
    check("ai non-stream chat", _client().chat(
        [{"role": "user", "content": "ping"}]) == "pong")
    try:
        _client("/compat").chat([{"role": "user", "content": "x"}])
        compat_ok = True
    except ai.AIError:
        compat_ok = False
    check("ai compat fallback (max_completion_tokens)", compat_ok)
    try:
        _client("/unauthorized").chat([{"role": "user", "content": "x"}])
        auth_kind = "no-error"
    except ai.AIError as err:
        auth_kind = err.kind
    check("ai 401 -> auth error", auth_kind == "auth", auth_kind)
    check("ai config hints",
          ai.AIClient({"base_url": "", "model": ""}).config_problem()
          == "need_base_url"
          and ai.AIClient({"base_url": base, "model": "m"}).config_problem()
          == "need_key")
    # v3.2.8：内置服务商预设（按各家官方文档核对）
    check("ai presets",
          ai.PROVIDERS["deepseek"]["base_url"] == "https://api.deepseek.com"
          and ai.PROVIDERS["deepseek"]["model"] == "deepseek-flash"
          and ai.PROVIDERS["dashscope"]["base_url"].endswith(
              "/compatible-mode/v1")
          and ai.PROVIDERS["zhipu"]["base_url"].endswith("/api/paas/v4")
          and ai.PROVIDERS["openai"]["base_url"].endswith("/v1")
          and ai.PROVIDERS["ollama"]["base_url"].startswith(
              "http://localhost"))
    # v3.2.8：自由对话的消息组装（上下文 + 多轮 + 轮数上限）
    ctx = ai.prepare_chat_context("剪贴板里的原始内容")
    check("ai chat context", len(ctx["messages"]) == 2
          and "剪贴板里的原始内容" in ctx["messages"][1]["content"])
    hist = []
    for i in range(10):
        hist += [{"role": "user", "content": "u%d" % i},
                 {"role": "assistant", "content": "a%d" % i}]
    msgs = ai.build_chat_messages(ctx["messages"], hist, "now")
    check("ai chat trims history",
          len(msgs) == 2 + 2 * ai.AI_CHAT_MAX_TURNS + 1
          and msgs[-1]["content"] == "now"
          and msgs[-2]["role"] == "assistant", str(len(msgs)))
    server.shutdown()

    # 用 AI 结果"替换本条"：正文换掉但标签 / 收藏 / 位置都在
    hist = os.path.join(tmp, ".youboard.json")
    store = yc.ClipboardStore(path=hist)
    store.clear()
    store.add_text("原始内容")
    old_hash = store.get_by_type("text")[0]["hash"]
    store.set_tags(old_hash, ["工作"])
    store.set_fav(old_hash, True)
    check("ai replace text", store.replace_text(old_hash, "AI 结果") is True)
    entry = store.get_by_type("text")[0]
    check("ai replace keeps meta",
          entry["content"] == "AI 结果" and yc.entry_tags(entry) == ["工作"]
          and bool(entry.get("fav")) and entry["hash"] != old_hash)
    check("ai replace no duplicate", len(store.get_by_type("text")) == 1)
    check("ai replace guards",
          store.replace_text("nope", "x") is False
          and store.replace_text(entry["hash"], "   ") is False)
    shutil.rmtree(tmp, ignore_errors=True)


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
    # CI 上没有用户历史（.youboard.json 不进仓库），这里自造样本数据，
    # 否则列表是空的，"列对齐 / 类型标记"这些断言无从测起。
    if store.count() == 0:
        store.add_text("YouBoard 回归测试文本\n第二行内容，用来检查内容列与表头对齐。")
        store.add_url("https://github.com/cloudxys/YouBoard")
        _f = os.path.join(tmp, "回归测试文件.txt")
        with open(_f, "w", encoding="utf-8") as fh:
            fh.write("regression sample")
        store.add_files([_f], "regression-file-hash")
        try:
            from PIL import Image as _PILImage
            _pic = _PILImage.new("RGB", (64, 48), (30, 120, 200))
            store.add_image(_pic, store._image_hash(_pic), "sample.png")
        except Exception:
            pass
        store.flush()
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
        # 列首的内联标记（类型 / 细分圆角标记、置顶胶囊、收藏星标）都会占掉一段宽度，
        # 这里统一先摘掉，专门量"正文起点是否和表头对齐"这条不变量
        # （不摘的话，用户历史里正好有置顶 / 收藏记录时会把正文顶右、误报不对齐）
        for i in range(min(4, table.rowCount())):
            cell = table.item(i, flex)
            if cell is None:
                continue
            for role in (yq._InlineImageDelegate.BADGE_ROLE,
                         yq._InlineImageDelegate.PIN_ROLE,
                         yq._InlineImageDelegate.FAV_ROLE):
                cell.setData(role, None)
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

    # ---- 3.2.7 标签 / 收藏：筛选胶囊、星标、弹层 ----
    fav_target = None
    for i in range(all_table.rowCount()):
        h = win._iid_to_hash["all"].get(i)
        e = win._entry_index.get(h)
        if e and e.get("type") == "text":
            fav_target = e
            break
    if fav_target is None:
        # 用户历史里万一没有文本记录，就地补一条，保证这段断言有对象
        store.add_text("标签回归文本样本")
        win._refresh_all()
        fav_target = store.get_by_type("text")[0]
    check("tags: sample entry available", fav_target is not None)
    store.set_tags(fav_target["hash"], ["回归标签"])
    store.set_fav(fav_target["hash"], True)
    # 用户自己的历史里可能本来就有收藏，所以按"基线 + 本次新增"来断言
    fav_expected = store.fav_count()
    win._refresh_all()
    for _ in range(6):
        app.processEvents()
    fav_chip = win._fav_chips.get("all")
    check("tags: filter row built", fav_chip is not None
          and "回归标签" in [t for t, _c in
                             win._tag_chips["all"].values()],
          str(list(win._tag_chips["all"].keys())))
    check("tags: fav chip count", fav_chip is not None
          and fav_chip.text().endswith(str(fav_expected)),
          "%s (want %s)" % (fav_chip.text() if fav_chip else "", fav_expected))
    rows_all = all_table.rowCount()
    win._toggle_fav_filter()
    for _ in range(6):
        app.processEvents()
    check("tags: fav filter works", 0 < all_table.rowCount() < rows_all
          or all_table.rowCount() == 1,
          "before=%s after=%s" % (rows_all, all_table.rowCount()))
    check("tags: star flag on fav rows",
          all(all_table.item(i, 3).data(yq._InlineImageDelegate.FAV_ROLE)
              for i in range(all_table.rowCount())))
    win._toggle_fav_filter()
    win._toggle_tag_filter("回归标签")
    for _ in range(6):
        app.processEvents()
    check("tags: tag filter works", all_table.rowCount() >= 1
          and all_table.rowCount() <= rows_all,
          "rows=%s" % all_table.rowCount())
    win._toggle_tag_filter("回归标签")
    pin_dlg = yq._TagsDialog(win, win, ["回归标签"], store.all_tags(),
                             mode="edit", count=1)
    check("tags: dialog seeded", pin_dlg.selected_tags() == ["回归标签"],
          str(pin_dlg.selected_tags()))
    pin_dlg._edit.setText("第二个标签")
    pin_dlg._add_typed()
    check("tags: dialog accepts typed",
          sorted(pin_dlg.selected_tags()) == sorted(["回归标签", "第二个标签"]))
    pin_dlg.close()
    batch_dlg = yq._TagsDialog(win, win, ["回归标签"], store.all_tags(),
                               mode="add", count=2)
    batch_dlg._toggle("回归标签")
    check("tags: add mode keeps existing out",
          batch_dlg.selected_tags() == [])
    batch_dlg.close()
    check("tags: star drawn vectorially",
          not yq._star_pixmap(13).isNull()
          and yq._star_pixmap(13, filled=False).isNull() is False)

    # ---- 分类 / 标签胶囊行：窗口拉宽后不能被摊开（行尾留白要显式 stretch） ----
    def chip_gaps(btns):
        xs = sorted((c.geometry().x(), c.geometry().right())
                    for c in btns if c.isVisible())
        return [xs[i + 1][0] - xs[i][1] - 1 for i in range(len(xs) - 1)]

    _mp4 = os.path.join(tmp, "regression_clip.mp4")
    with open(_mp4, "wb") as fh:
        fh.write(b"\x00" * 128)
    store.add_files([_mp4], "regression-video-hash")
    store.set_tags(fav_target["hash"], ["回归标签", "第二个"])
    win._refresh_all()
    file_idx = yq.TAB_TYPES.index("file")
    widths_seen = {}
    for width in (1180, 1500):
        win.resize(width, 780)
        for _ in range(10):
            app.processEvents()
        win._tabs.setCurrentIndex(file_idx)
        for _ in range(10):
            app.processEvents()
        widths_seen[width] = chip_gaps(list(win._file_kind_btns.values()))
    kind_gaps = widths_seen[1500]
    check("file kind chips stay packed",
          len(kind_gaps) >= 2 and all(g == 6 for g in kind_gaps)
          and kind_gaps == widths_seen[1180], str(widths_seen))
    win._tabs.setCurrentIndex(yq.TAB_TYPES.index("text"))
    for _ in range(10):
        app.processEvents()
    fav_and_tags = [win._fav_chips["text"]] + [c for _t, c in
                                               win._tag_chips["text"].values()]
    tag_gaps = chip_gaps(fav_and_tags)
    check("tag filter chips stay packed",
          len(tag_gaps) >= 2 and all(g == 6 for g in tag_gaps), str(tag_gaps))
    win.resize(1200, 760)      # 还原窗口尺寸，后面的用例不受影响
    for _ in range(6):
        app.processEvents()
    store.set_fav(fav_target["hash"], False)
    store.set_tags(fav_target["hash"], [])
    win._refresh_all()
    for _ in range(4):
        app.processEvents()

    # ---- AI 就地处理：弹层流式显示 + 结果三种落点（假客户端，不联网） ----
    copied_texts = []
    _orig_clip = yq.set_clipboard_text
    yq.set_clipboard_text = lambda t: copied_texts.append(t)

    class _FakeAI:
        def __init__(self, chunks=("总结：", "下周一十点"), problem=""):
            self.chunks = list(chunks)
            self.problem = problem
            self.cancelled = False
            self.calls = []

        def config_problem(self):
            return self.problem

        def is_ready(self):
            return not self.problem

        def cancel(self):
            self.cancelled = True

        def stream_chat(self, messages, on_delta=None, max_tokens=None):
            self.calls.append(messages)
            out = []
            for chunk in self.chunks:
                if self.cancelled:
                    break
                out.append(chunk)
                if on_delta is not None:
                    on_delta(chunk)
                time.sleep(0.02)
            return "".join(out)

    def _wait_ai(dlg, timeout=8.0):
        t0 = time.time()
        while dlg._worker is not None and time.time() - t0 < timeout:
            app.processEvents()
            time.sleep(0.02)
        for _ in range(6):
            app.processEvents()

    win._tabs.setCurrentIndex(yq.TAB_TYPES.index("text"))
    for _ in range(6):
        app.processEvents()
    ai_table = win._tables["text"]
    ai_entry = None
    for i in range(ai_table.rowCount()):
        cand = win._entry_index.get(win._iid_to_hash["text"].get(i))
        if cand and cand.get("type") == "text":
            ai_entry = cand
            ai_table.selectRow(i)
            break
    check("ai: text entry available", ai_entry is not None)
    for _ in range(6):
        app.processEvents()
    fake = _FakeAI()
    adlg = yq._AIDialog(win, win, ai_entry, "summarize", client=fake)
    adlg.show()
    for _ in range(4):
        app.processEvents()
    _wait_ai(adlg)
    check("ai: dialog streams text",
          adlg._out.toPlainText() == "总结：下周一十点",
          repr(adlg._out.toPlainText()))
    check("ai: only selected entry sent",
          fake.calls and ai_entry["content"] in fake.calls[0][1]["content"])
    check("ai: dialog style matches retention dialog",
          adlg.card.styleSheet()
          == yq._RetentionDialog(win, win, {"mode": "forever"}).card.styleSheet())
    copied_texts.clear()
    adlg._use_result_copy()
    for _ in range(4):
        app.processEvents()
    check("ai: copy result uses clipboard",
          copied_texts == ["总结：下周一十点"], str(copied_texts))
    check("ai: copy marks self copy", store.is_self_copy() is True)
    yq.set_clipboard_text = _orig_clip
    before_count = store.count()
    adlg2 = yq._AIDialog(win, win, ai_entry, "summarize",
                         client=_FakeAI(chunks=("新记录", "内容")))
    adlg2.show()
    for _ in range(4):
        app.processEvents()
    _wait_ai(adlg2)
    adlg2._use_result_save()
    for _ in range(6):
        app.processEvents()
    check("ai: save as new entry", store.count() == before_count + 1,
          "%s -> %s" % (before_count, store.count()))
    old_hash = ai_entry["hash"]
    count_before = store.count()
    adlg3 = yq._AIDialog(win, win, ai_entry, "rewrite",
                         client=_FakeAI(chunks=("改写", "结果")))
    adlg3.show()
    for _ in range(4):
        app.processEvents()
    _wait_ai(adlg3)
    adlg3._use_result_replace()
    for _ in range(6):
        app.processEvents()
    check("ai: replace keeps count", store.count() == count_before)
    check("ai: replace wrote content",
          any(e["content"] == "改写结果" for e in store.get_by_type("text")))
    check("ai: replace dropped old hash",
          not any(e["hash"] == old_hash for e in store.get_by_type("text")))
    nc = _FakeAI(problem="need_key")
    adlg4 = yq._AIDialog(win, win, ai_entry, "summarize", client=nc)
    adlg4.show()
    for _ in range(4):
        app.processEvents()
    check("ai: unconfigured shows hint", nc.calls == []
          and "API Key" in adlg4._status.text(), adlg4._status.text()[:30])
    adlg4.close()
    check("ai: submenu for text/url but not for unknown types",
          win._ai_menu_for(yq._RoundMenu(win), ai_entry) is not None
          and win._ai_menu_for(yq._RoundMenu(win),
                               {"hash": "h", "type": "weird"}) is None)
    sdlg = yq.SettingsDialog(win)
    check("ai: settings page row only", sdlg._ai_lbl.text() != ""
          and not hasattr(sdlg, "_ai_provider"))
    # 用内置默认预设构造，避免用户自己存过 AI 配置后这里断言不到默认值
    cfg_dlg = yq._AISettingsDialog(sdlg, win, yq.default_ai_settings())
    check("ai: config dialog is card style",
          isinstance(cfg_dlg, yq._CardOverlayDialog)
          and cfg_dlg.card.styleSheet()
          == yq._RetentionDialog(win, win,
                                 {"mode": "forever"}).card.styleSheet())
    check("ai: provider pills", len(cfg_dlg._prov_btns) == 6)
    check("ai: settings defaults",
          cfg_dlg._base.text() == "https://api.deepseek.com"
          and cfg_dlg._model.text() == "deepseek-flash",
          "%s / %s" % (cfg_dlg._base.text(), cfg_dlg._model.text()))
    # 模型行：左边真实 id、右边可改的显示名；显示名绝不参与请求
    check("ai: model row has real id + editable label",
          cfg_dlg._model_label.text() == "DeepSeek-V4.1-Flash"
          and cfg_dlg._model.text() == "deepseek-flash",
          "%s | %s" % (cfg_dlg._model.text(), cfg_dlg._model_label.text()))
    _lab_settings = yq.default_ai_settings()
    _lab_settings["model_label"] = "我的小助手"
    check("ai: display name is never sent",
          yq.AIClient(_lab_settings).build_payload(
              [{"role": "user", "content": "x"}])["model"] == "deepseek-flash"
          and yq.model_display_name(_lab_settings) == "我的小助手")
    _lab_settings["model_label"] = ""
    check("ai: display name falls back to id",
          yq.model_display_name(_lab_settings) == "deepseek-flash")
    check("ai: settings key hidden",
          cfg_dlg._key.echoMode() == yq.QLineEdit.EchoMode.Password)
    cfg_dlg._pick_provider("ollama")
    for _ in range(4):
        app.processEvents()
    check("ai: provider switch fills defaults",
          cfg_dlg._base.text() == "http://localhost:11434/v1"
          and cfg_dlg._model.text() == "qwen3.5",
          "%s / %s" % (cfg_dlg._base.text(), cfg_dlg._model.text()))
    check("ai: spin arrows flattened (no square block)",
          "up-button" in cfg_dlg.card.styleSheet()
          and "background: transparent" in cfg_dlg.card.styleSheet())
    # 字段标签按内容自适应，不能被裁（固定宽度会把「温度（越低越稳）」切掉）
    clipped = []
    for _r in range(cfg_dlg._fields.rowCount()):
        _item = cfg_dlg._fields.itemAtPosition(_r, 0)
        _lbl = _item.widget() if _item is not None else None
        if _lbl is not None and _lbl.width() < _lbl.sizeHint().width():
            clipped.append((_lbl.text(), _lbl.width(), _lbl.sizeHint().width()))
    check("ai: field labels not clipped", not clipped, str(clipped))
    _labels = []
    for _r in range(cfg_dlg._fields.rowCount()):
        _item = cfg_dlg._fields.itemAtPosition(_r, 0)
        if _item is not None and _item.widget() is not None:
            _labels.append(_item.widget().text())
    check("ai: all five field labels present", len(_labels) == 5,
          str(_labels))
    cfg_dlg.close()
    sdlg.close()
    check("ai: hotkey default", yq._ACTION_HOTKEY_DEFAULTS.get("hk_ai")
          == "ctrl+i")

    # ---- v3.2.8：AI 自由对话（多轮）+ 导出 TXT ----
    chat_client = _FakeAI(chunks=("答", "复"))
    chat_dlg = yq._AIDialog(win, win, ai_entry, "summarize",
                            client=chat_client, chat=True)
    chat_dlg.show()
    for _ in range(4):
        app.processEvents()
    check("ai chat: input row shown", chat_dlg._input_row.isVisible())
    check("ai chat: waits for the user", chat_client.calls == [])
    check("ai chat: submenu entry", yq.tr("ai_menu_chat") != ""
          and hasattr(win, "_run_ai_chat"))
    chat_dlg._input.setText("帮我把时间点列出来")
    chat_dlg._send_chat()
    for _ in range(4):
        app.processEvents()
    _wait_ai(chat_dlg)
    chat_text = chat_dlg._out.toPlainText()
    check("ai chat: transcript shows Q and A",
          "帮我把时间点列出来" in chat_text and "答复" in chat_text,
          chat_text[:40].replace("\n", " / "))
    check("ai chat: entry sent as context",
          bool(chat_client.calls)
          and ai_entry["content"] in chat_client.calls[0][1]["content"])
    chat_dlg._input.setText("再短一点")
    chat_dlg._send_chat()
    for _ in range(4):
        app.processEvents()
    _wait_ai(chat_dlg)
    check("ai chat: second turn carries history",
          [m["role"] for m in chat_client.calls[-1]][-3:]
          == ["user", "assistant", "user"],
          str([m["role"] for m in chat_client.calls[-1]]))
    chat_dlg._restart()
    for _ in range(4):
        app.processEvents()
    _wait_ai(chat_dlg)
    check("ai chat: regenerate reuses question",
          chat_client.calls[-1][-1]["content"] == "再短一点")
    chat_file = os.path.join(tmp, "ai_chat_export.txt")
    _orig_save = yq.QFileDialog.getSaveFileName
    _orig_card_fn = yq._info_card
    yq.QFileDialog.getSaveFileName = staticmethod(
        lambda *a, **k: (chat_file, "txt"))
    yq._info_card = lambda *a, **k: None
    try:
        chat_dlg._export_btn.setEnabled(True)
        chat_dlg._export_txt()
        for _ in range(3):
            app.processEvents()
    finally:
        yq.QFileDialog.getSaveFileName = _orig_save
        yq._info_card = _orig_card_fn
    exported = ""
    if os.path.exists(chat_file):
        exported = open(chat_file, encoding="utf-8").read()
    check("ai chat: export txt wrote conversation",
          "你：" in exported and "再短一点" in exported, exported[:40])
    chat_dlg.close()
    for _ in range(3):
        app.processEvents()

    # 生成过程中再按回车不能变成「停止」（输入框保持可用、按钮不吃键盘焦点）
    from PyQt6.QtCore import Qt as _Qt8
    check("ai chat: stop/send ignore keyboard focus",
          chat_dlg._stop_btn.focusPolicy() == _Qt8.FocusPolicy.NoFocus
          and chat_dlg._send_btn.focusPolicy() == _Qt8.FocusPolicy.NoFocus
          and chat_dlg._input.isEnabled())
    # 卡片弹层不再"始终置顶"（切到别的程序时不会孤零零飘在别人窗口上）
    _ret_probe = yq._RetentionDialog(win, None, {"mode": "forever"})
    check("overlay: not always-on-top",
          not bool(_ret_probe.windowFlags()
                   & _Qt8.WindowType.WindowStaysOnTopHint))
    win.hide()
    for _ in range(4):
        app.processEvents()
    _ret_probe._sync_to_host()
    _geo = _ret_probe.geometry()
    _scr = yq.QApplication.primaryScreen().availableGeometry()
    check("overlay: centred when host hidden",
          _geo.width() <= 700 and _geo.height() <= 620
          and _scr.contains(_geo.center()), str(_geo))
    _ret_probe.close()
    win.show()
    for _ in range(6):
        app.processEvents()

    # ---- v3.2.9：图片 / 文件分类的 AI + 点 ✕ 收进托盘开关 ----
    import youboard_ai as _yai9
    _imgs = store.get_by_type("image")
    _files = store.get_by_type("file")
    check("ai: image entry available", bool(_imgs))
    check("ai: file entry available", bool(_files))
    if _imgs:
        _imenu = win._ai_menu_for(yq._RoundMenu(win), _imgs[0])
        _ilabels = [a.text() for a in _imenu.actions() if a.text()]
        check("ai: image menu actions",
              _ilabels[:3] == [yq.tr("ai_act_describe"), yq.tr("ai_act_ocr"),
                               yq.tr("ai_act_img_translate")], str(_ilabels))
        _ipath = os.path.join(dst, _imgs[0].get("filename", ""))
        _ireq = yq.prepare_image_request("describe", _ipath, lang="zh")
        _icontent = _ireq["messages"][1]["content"]
        check("ai: image request is multimodal",
              isinstance(_icontent, list) and len(_icontent) == 2
              and _icontent[1]["type"] == "image_url"
              and _icontent[1]["image_url"]["url"].startswith(
                  "data:image/jpeg;base64,"))
        check("ai: image downscaled before sending",
              _ireq["image"]["size"][0] <= _yai9.AI_IMAGE_MAX_SIDE
              and _ireq["image"]["size"][1] <= _yai9.AI_IMAGE_MAX_SIDE,
              str(_ireq["image"]["size"]))
    if _files:
        _fmenu = win._ai_menu_for(yq._RoundMenu(win), _files[0])
        _flabels = [a.text() for a in _fmenu.actions() if a.text()]
        check("ai: file menu actions",
              _flabels[:2] == [yq.tr("ai_act_file_summary"),
                               yq.tr("ai_act_file_suggest")], str(_flabels))
        _ftxt = yq.file_entries_text(_files[0])
        check("ai: file list text", "个文件" in _ftxt and "- " in _ftxt,
              _ftxt[:50].replace("\n", " | "))
    # 点 ✕ 收进托盘（默认关 = 直接退出）
    check("app: quit-on-last-window disabled (tray app)",
          app.quitOnLastWindowClosed() is False)
    _ccfg = yq.load_config()
    _ccfg["close_to_tray"] = True
    yq.save_config(_ccfg)
    check("close-to-tray: switch read from config",
          win._close_to_tray_enabled() is True)
    _cdlg = yq.SettingsDialog(win)
    check("close-to-tray: switch sits in settings",
          _cdlg._close_tray_cb is not None
          and _cdlg._close_tray_cb.isChecked() is True)
    check("close-to-tray: row shows the current value",
          _cdlg._close_tray_state is not None
          and _cdlg._close_tray_state.text() == yq.tr("set_close_tray_on")
          and "set_close_tray_desc" not in yq.STRINGS["zh"]
          and "set_close_tray_sub" not in yq.STRINGS["zh"])
    check("close-to-tray: label not bold / same size as siblings",
          "font-weight: 600" not in _cdlg._close_tray_lbl.styleSheet()
          and "font-size" not in _cdlg._close_tray_lbl.styleSheet(),
          _cdlg._close_tray_lbl.styleSheet())
    _cdlg._close_tray_cb.setChecked(False)
    for _ in range(3):
        app.processEvents()
    check("close-to-tray: label follows the switch",
          _cdlg._close_tray_state.text() == yq.tr("set_close_tray_off"),
          _cdlg._close_tray_state.text())
    _cdlg.close()
    _vis_before = win.isVisible()
    win.close()
    for _ in range(6):
        app.processEvents()
    check("close-to-tray: X hides instead of quitting",
          _vis_before and not win.isVisible())
    win.show()
    for _ in range(6):
        app.processEvents()
    _ccfg["close_to_tray"] = False
    yq.save_config(_ccfg)
    check("close-to-tray: turning it off restores direct exit",
          win._close_to_tray_enabled() is False)

    # ---- v3.2.8：设置窗口尺寸 / 光标兜底 / 弹层抬小组件 ----
    _avail = yq.QApplication.primaryScreen().availableGeometry()
    s_probe = yq.SettingsDialog(win)
    check("settings: size sane",
          s_probe.width() <= max(360, _avail.width() - 120)
          and s_probe.height() <= max(360, _avail.height() - 120)
          and s_probe.minimumWidth() <= 440,
          "%sx%s min=%s" % (s_probe.width(), s_probe.height(),
                            s_probe.minimumWidth()))
    s_probe.show()
    for _ in range(4):
        app.processEvents()
    _btn = next(b for b in s_probe.findChildren(yq.QPushButton)
                if b.isEnabled())
    s_probe._live_cursor_refresh(_btn)
    cursor_on = (yq.QApplication.overrideCursor() is not None
                 and s_probe._cursor_override_on is True)
    s_probe._live_cursor_refresh(yq.QLabel("x", s_probe))
    cursor_off = s_probe._cursor_override_on is False
    s_probe._live_cursor_refresh(_btn)
    s_probe.hide()
    for _ in range(3):
        app.processEvents()
    check("cursor: hand override toggles with hover", cursor_on and cursor_off)
    check("cursor: override cleared when settings hides",
          s_probe._cursor_override_on is False
          and yq.QApplication.overrideCursor() is None)
    if getattr(win, "_desk_widget", None) is not None:
        from PyQt6.QtGui import QCursor as _QC
        from PyQt6.QtCore import Qt as _Qt
        win._desk_widget.setCursor(_QC(_Qt.CursorShape.SizeVerCursor))
        yq._refresh_cursor_under_mouse(win._desk_widget)
        check("cursor: widget keeps resize cursor",
              win._desk_widget.cursor().shape()
              == _Qt.CursorShape.SizeVerCursor)
        win._desk_widget.unsetCursor()
        raised = []
        _desk = win._desk_widget
        _orig_raise = _desk.raise_
        _desk.raise_ = lambda: raised.append(1)
        try:
            yq._AISettingsDialog(sdlg, win,
                                 yq.default_ai_settings())._sync_to_host()
        finally:
            _desk.raise_ = _orig_raise
        check("overlay raises desktop widget", bool(raised))

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
    # 截图落到临时目录：不要把测试产生的图片留在仓库里
    shot = os.path.join(tempfile.gettempdir(), "youboard_verify_shot.png")
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
    print("== ai ==")
    test_ai()
    print("== gui ==")
    test_gui()
    print("RESULT=" + ("ALL_PASS" if not FAIL else "FAILED:" + ",".join(FAIL)))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
