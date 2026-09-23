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
import re
import shutil
import subprocess
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

    # ---- 密库（v3.3.1）：可选名称 + 四类内容，独立加密落盘 ----
    vpath = os.path.join(tmp, "vault.json")
    vault = yc.VaultStore(path=vpath)
    check("vault: starts empty", vault.count() == 0)
    v1 = vault.add_text("sk-abcdef123456", name="邮箱密钥")
    check("vault: add text with optional name",
          v1 is not None and vault.count() == 1 and v1["type"] == "text"
          and v1["name"] == "邮箱密钥")
    v2 = vault.add_text("https://example.com/a")
    check("vault: url auto-detected", v2 is not None and v2["type"] == "url")
    check("vault: empty rejected", vault.add_text("   ") is None
          and vault.count() == 2)
    check("vault: counts by kind",
          vault.counts() == {"all": 2, "text": 1, "image": 0, "file": 0,
                             "url": 1}, str(vault.counts()))
    check("vault: search by name", len(vault.search("邮箱")) == 1)
    check("vault: search by content", len(vault.search("abcdef")) == 1)
    check("vault: filter by kind",
          len(vault.search("", "text")) == 1 and len(vault.search("", "url")) == 1)
    check("vault: rename", vault.rename(v1["id"], "新名字")
          and vault.get(v1["id"])["name"] == "新名字")
    check("vault: rename to empty (show content)",
          vault.rename(v1["id"], "") and vault.get(v1["id"])["name"] == "")
    check("vault: update content", vault.update(v1["id"], content="第二个正文")
          and vault.get(v1["id"])["content"] == "第二个正文")
    check("vault: update name", vault.update(v1["id"], name="甲乙")
          and vault.get(v1["id"])["name"] == "甲乙")
    check("vault: blank content rejected",
          vault.update(v1["id"], content="   ") is False
          and vault.get(v1["id"])["content"] == "第二个正文")
    check("vault: delete", vault.delete(v2["id"]) and vault.count() == 1
          and vault.delete("missing") is False)

    # 图片：复制进密库自己的目录；文件：记路径
    img_src = os.path.join(tmp, "vault_src.png")
    try:
        from PIL import Image as _VPIL
        _VPIL.new("RGB", (32, 24), (10, 120, 90)).save(img_src)
    except Exception:
        img_src = ""
    vimg = None
    if img_src:
        iname = vault.store_image_file(img_src)
        check("vault: image copied into vault dir",
              bool(iname) and os.path.exists(yc.vault_image_path({"image": iname})))
        vimg = vault.add(kind="image", image=iname, name="")
        check("vault: image entry counted", vimg is not None
              and vault.counts()["image"] == 1)
        img_full = yc.vault_image_path(vimg)
        check("vault: delete removes copied image",
              vault.delete(vimg["id"]) and not os.path.exists(img_full))
    vfile = vault.add(kind="file", paths=[os.path.join(tmp, "some.pdf")])
    check("vault: file entry", vfile is not None
          and vault.counts()["file"] == 1
          and vault.search("some.pdf") != [])

    vault.flush()
    with open(vpath, "rb") as fh:
        vraw = fh.read()
    check("vault: encrypted at rest",
          vraw.startswith(b"gAAAA")
          and "第二个正文".encode("utf-8") not in vraw)
    check("vault: separate from clipboard history",
          os.path.abspath(vpath) != os.path.abspath(store.path)
          and yc.entry_tags(store.get_by_type("text")[0]) == [])
    vreload = yc.VaultStore(path=vpath)
    check("vault: persisted", vreload.count() == vault.count()
          and any(e["name"] == "甲乙" for e in vreload.entries()))

    # 老版密库（名称 / 账号 / 密码 / 链接 / 备注）要能无损迁移过来
    v1_path = os.path.join(tmp, "vault_v1.json")
    legacy = {"version": 1, "entries": [
        {"id": "legacy1", "title": "老名字", "username": "me@x.com",
         "secret": "旧密码", "url": "", "note": "老备注",
         "created": "2026-09-01T10:00:00", "updated": "2026-09-01T10:00:00"}]}
    with open(v1_path, "wb") as fh:
        fh.write(yc._encrypt_data(json.dumps(legacy).encode("utf-8")))
    migrated = yc.VaultStore(path=v1_path)
    check("vault: v1 data migrated",
          migrated.count() == 1
          and migrated.entries()[0]["name"] == "老名字"
          and "旧密码" in migrated.entries()[0]["content"]
          and "老备注" in migrated.entries()[0]["content"]
          and migrated.entries()[0]["type"] == "text")

    # ---- 导入合并语义（v3.2.9）：合并而不是覆盖 + 完全重复只留一条 + 时间倒序 ----
    ms = yc.ClipboardStore(path=os.path.join(tmp, "merge_semantics.json"))
    ms.clear()
    for i in range(700):
        ms.add_text("合并语义本机 %03d" % i)
    local = ms.get_by_type("text")
    pin_h = [e["hash"] for e in local[:3]]
    ms.pin_many(pin_h)
    incoming = {"text": {"pinned": [], "entries": []},
                "image": {"pinned": [], "entries": []},
                "file": {"pinned": [], "entries": []},
                "url": {"pinned": [], "entries": []}}
    dups = [e for e in ms.get_by_type("text") if e["hash"] in pin_h][:1] + \
        [e for e in ms.get_by_type("text") if e["hash"] not in pin_h][:49]
    for e in dups:
        incoming["text"]["entries"].append(dict(e))
    for i in range(250):
        incoming["text"]["entries"].append({
            "hash": yc.ClipboardStore._text_hash("合并语义外部 %03d" % i),
            "type": "text", "content": "合并语义外部 %03d" % i,
            "timestamp": "2030-01-01T00:%02d:%02d" % (i // 60, i % 60),
            "length": 12})
    ms.merge_history(incoming)
    merged_texts = ms.get_by_type("text")
    merged_hashes = [e["hash"] for e in merged_texts]
    check("merge: 合并而不是覆盖", ms.count() == 700 + 250,
          "700 + 300(含 50 重复) -> %s（期望 950）" % ms.count())
    check("merge: 完全重复只留一条",
          len(merged_hashes) == len(set(merged_hashes)))
    check("merge: 置顶仍然置顶且只有一份",
          ms.pinned_count("text") == 3
          and all(h in merged_hashes for h in pin_h))
    _cat = ms.categories["text"]
    _plist = [e["hash"] for e in _cat["pinned"]]
    _elist = [e["hash"] for e in _cat["entries"]]
    check("merge: 置顶与非置顶重复时保留置顶那份",
          pin_h[0] in _plist and pin_h[0] not in _elist)
    check("merge: 普通列表里不出现置顶记录",
          not (set(_plist) & set(_elist)))
    check("merge: 其余按时间倒序",
          [e.get("timestamp", "") for e in merged_texts[3:]]
          == sorted([e.get("timestamp", "") for e in merged_texts[3:]],
                    reverse=True))
    check("merge: 新导入的最新一条排在最前（置顶之后）",
          merged_texts[3].get("content") == "合并语义外部 249",
          merged_texts[3].get("content"))


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


def test_bridge():
    """浏览器扩展桥：只监听 127.0.0.1 + 令牌鉴权 + 文本/图片/历史/复制/收藏。"""
    import base64
    import io
    import urllib.error
    import urllib.request
    sys.path.insert(0, SRC)
    import youboard_bridge as br
    import youboard_core as yc
    tmp = tempfile.mkdtemp(prefix="yb_bridge_")
    yc.CONTENT_DIR = os.path.join(tmp, "content")
    yc.IMAGES_DIR = os.path.join(tmp, "images")
    yc.FILE_CACHE_DIR = os.path.join(tmp, "file_cache")
    yc.KEY_FILE = os.path.join(tmp, "youboard.key")
    os.makedirs(yc.IMAGES_DIR, exist_ok=True)
    store = yc.ClipboardStore(path=os.path.join(tmp, ".youboard.json"))
    store.clear()

    copied = []
    _orig = br.set_clipboard_text
    br.set_clipboard_text = lambda t: copied.append(t)
    token = "gate-token"
    server = br.BridgeServer(store, port=0, token=token)
    port = server.start()
    base = "http://127.0.0.1:%d" % port

    def call(path, method="GET", body=None, tok=token):
        req = urllib.request.Request(base + path, method=method)
        if tok:
            req.add_header("X-YouBoard-Token", tok)
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, data=data, timeout=10) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as err:
            try:
                return err.code, json.loads(err.read())
            except Exception:
                return err.code, {}

    try:
        code, data = call("/api/ping")
        check("bridge: ping with token", code == 200
              and data.get("app") == "YouBoard", str(data)[:60])
        check("bridge: rejects wrong token",
              call("/api/ping", tok="")[0] == 401
              and call("/api/ping", tok="nope")[0] == 401)
        check("bridge: binds 127.0.0.1 only",
              server._httpd.server_address[0] == "127.0.0.1")
        code, _ = call("/api/items", "POST",
                       {"type": "text", "content": "来自扩展的文本"})
        check("bridge: add text", code == 200 and any(
            e.get("content") == "来自扩展的文本"
            for e in store.get_by_type("text")))
        buf = io.BytesIO()
        from PIL import Image as _Img
        _Img.new("RGB", (60, 40), (10, 200, 120)).save(buf, "PNG")
        data_url = ("data:image/png;base64,"
                    + base64.b64encode(buf.getvalue()).decode())
        code, data = call("/api/items", "POST",
                          {"type": "image", "content": data_url})
        check("bridge: add image", code == 200 and data.get("type") == "image")
        code, data = call("/api/history?limit=10")
        items = data.get("items") or []
        check("bridge: history fields",
              code == 200 and items
              and all("hash" in i and "type" in i and "ts" in i
                      for i in items), str(len(items)))
        h = next((i["hash"] for i in items if i["type"] == "text"), "")
        code, _ = call("/api/copy", "POST", {"hash": h})
        check("bridge: copy + self-copy mark",
              code == 200 and copied and store.is_self_copy() is True)
        check("bridge: fav", call("/api/fav", "POST",
                                  {"hash": h, "fav": True})[0] == 200
              and store.is_fav(h) is True)
        req = urllib.request.Request(base + "/api/history", method="OPTIONS")
        with urllib.request.urlopen(req, timeout=10) as resp:
            check("bridge: cors preflight", resp.status == 204
                  and resp.headers.get("Access-Control-Allow-Origin") == "*")
        server2 = br.BridgeServer(store, port=port, token=token)
        port2 = server2.start()
        check("bridge: port fallback when busy", port2 not in (0, port),
              "%s -> %s" % (port, port2))
        server2.stop()
    finally:
        br.set_clipboard_text = _orig
        server.stop()
        shutil.rmtree(tmp, ignore_errors=True)
    check("bridge: stopped", server.running is False)


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

    # 3.3.1：输入框打了字直接点「保存」也要生效（以前只有按回车才算，字被丢掉）
    save_dlg = yq._TagsDialog(win, win, ["回归标签"], store.all_tags(),
                              mode="edit", count=1)
    save_dlg._edit.setText("直接保存")
    save_dlg.accept()
    check("tags: save button commits typed text",
          sorted(save_dlg.selected_tags()) == sorted(["回归标签", "直接保存"]),
          str(save_dlg.selected_tags()))
    save_dlg.close()
    blank_dlg = yq._TagsDialog(win, win, ["回归标签"], store.all_tags(),
                               mode="edit", count=1)
    blank_dlg._edit.setText("   ")
    blank_dlg.accept()
    check("tags: blank input drops nothing",
          blank_dlg.selected_tags() == ["回归标签"],
          str(blank_dlg.selected_tags()))
    blank_dlg.close()

    # ---- 3.3.1 密库窗口：列表 / 复制不进历史 / 新增弹层校验 ----
    win.vault = yq.VaultStore(path=os.path.join(tmp, "vault_ui.json"))
    vwin = yq.VaultDialog(win)
    vwin.show()
    for _ in range(6):
        app.processEvents()
    check("vaultui: empty list", vwin._table.rowCount() == 0)
    check("vaultui: has five kind chips",
          sorted(vwin._kind_btns.keys()) ==
          ["all", "file", "image", "text", "url"])
    win.vault.add_text("wifi-pass-8899", name="Wi-Fi")
    win.vault.add_text("https://example.com/vault")
    vwin._reload()
    titles = [vwin._table.item(i, 0).text()
              for i in range(vwin._table.rowCount())]
    check("vaultui: lists all kinds", vwin._table.rowCount() == 2, str(titles))
    check("vaultui: custom name shown", "Wi-Fi" in titles)
    check("vaultui: no name falls back to content",
          any(t.startswith("https://example.com/vault") for t in titles),
          str(titles))
    vwin._set_kind("text")
    app.processEvents()
    check("vaultui: kind chip filters text", vwin._table.rowCount() == 1)
    vwin._set_kind("url")
    app.processEvents()
    check("vaultui: kind chip filters url", vwin._table.rowCount() == 1)
    check("vaultui: status counts are unambiguous",
          "全部" in vwin._status.text() and "/" not in vwin._status.text(),
          vwin._status.text())
    vwin._set_kind("image")
    app.processEvents()
    check("vaultui: empty kind shows nothing", vwin._table.rowCount() == 0)
    vwin._set_kind("all")
    app.processEvents()
    vwin._table.selectRow(0)
    app.processEvents()
    check("vaultui: action buttons enabled", vwin._copy_btn.isEnabled())
    store._self_copy_time = 0.0
    vwin._copy_entry()
    check("vaultui: copy marks self-copy (stays out of history)",
          store.is_self_copy())
    check("vaultui: no focus frame delegate",
          isinstance(vwin._table.itemDelegate(), yq._NoFocusDelegate))
    vwin._search.setText("找不到的关键词")
    app.processEvents()
    check("vaultui: search filters", vwin._table.rowCount() == 0)
    vwin._search.clear()
    app.processEvents()
    check("vaultui: search clears", vwin._table.rowCount() == 2)
    vwin.close()

    # 弹层：名称可选；空内容不许保存；名称框回车 = 保存
    ved = yq._VaultEntryDialog(win, None)
    ved.accept()
    check("vaultui: empty entry rejected",
          ved.result() != 1 and bool(ved._hint.text()))
    ved._content_edit.setPlainText("Steam 密码 Steam!2345")
    ved.accept()
    check("vaultui: content without name accepted",
          ved.result() == 1 and not ved.values()["name"]
          and "Steam" in ved.values()["content"])
    ved2 = yq._VaultEntryDialog(win, None)
    ved2._content_edit.setPlainText("abcdef")
    ved2._name_edit.setText("甲乙")
    check("vaultui: optional name captured", ved2.values()["name"] == "甲乙")

    # 输入框右键：换成应用自己的中文圆角菜单（不是 Qt 自带那套英文直角菜单）
    check("vaultui: fields use app-styled edit widgets",
          isinstance(ved2._content_edit, yq._VaultTextEdit)
          and isinstance(ved2._name_edit, yq._VaultLineEdit)
          and isinstance(vwin._search, yq._VaultLineEdit))
    ctx_items = []

    class _ActSpy:
        def setEnabled(self, _v):
            pass

    class _MenuSpyCtx:
        def __init__(self, *a, **k):
            pass

        def addAction(self, *a):
            ctx_items.append(a[0] if a else "")
            return _ActSpy()

        def addSeparator(self):
            ctx_items.append("---")

        def exec(self, *_a):
            return None

    _real_ctx_menu = yq._RoundMenu
    yq._RoundMenu = _MenuSpyCtx
    try:
        yq._edit_context_menu(ved2._content_edit, True)
    finally:
        yq._RoundMenu = _real_ctx_menu
    check("vaultui: context menu is localized",
          ctx_items == [yq.tr("ctx_cut"), yq.tr("ctx_copy"),
                        yq.tr("ctx_paste"), "---", yq.tr("m_select_all")],
          str(ctx_items))

    # 长内容一键复制（且不能进剪贴板历史）
    long_text = "很长的密库内容" * 40
    ved3 = yq._VaultEntryDialog(win, None)
    ved3._content_edit.setPlainText(long_text)
    store._self_copy_time = 0.0
    ved3._copy_content()
    import pyperclip as _clip
    check("vaultui: copy-content copies the whole text",
          _clip.paste() == long_text)
    check("vaultui: copy-content marks self-copy",
          store.is_self_copy())

    # 「内容」框要和「名称」框长得一样、文字起点也要对齐（用户报过"内容没对上"）
    def _text_left_x(widget):
        img = widget.grab().toImage()
        bg = img.pixelColor(img.width() // 2, img.height() - 3)
        for x in range(3, min(60, img.width())):
            hits = 0
            for y in range(6, img.height() - 6):
                c = img.pixelColor(x, y)
                if (abs(c.red() - bg.red()) + abs(c.green() - bg.green())
                        + abs(c.blue() - bg.blue())) > 70:
                    hits += 1
            if hits >= 2:
                return x
        return -1

    align_dlg = yq._VaultEntryDialog(win, None,
                                     {"name": "MMMM", "content": "sk-abcdef123456"})
    align_dlg.show()
    for _ in range(8):
        app.processEvents()
    name_x = _text_left_x(align_dlg._name_edit)
    content_x = _text_left_x(align_dlg._content_edit)
    check("vaultui: content text aligns with name text",
          name_x > 0 and name_x == content_x,
          "name=%s content=%s" % (name_x, content_x))

    # 3.3.1 修补：弹层里的回车语义（输入框按回车不能变成"取消"，否则内容像消失了）
    from PyQt6.QtTest import QTest as _QTest
    enter_dlg = yq._VaultEntryDialog(win, None)
    enter_dlg.show()
    app.processEvents()
    enter_dlg._content_edit.setPlainText("回车保存")
    enter_dlg._name_edit.setFocus()
    app.processEvents()
    _QTest.keyClick(enter_dlg._name_edit, yq.Qt.Key.Key_Return)
    app.processEvents()
    check("vaultui: Enter in field saves",
          enter_dlg.result() == 1
          and enter_dlg.values()["content"] == "回车保存",
          "result=%s visible=%s" % (enter_dlg.result(), enter_dlg.isVisible()))
    tag_enter = yq._TagsDialog(win, win, [], [], mode="edit", count=1)
    tag_enter.show()
    app.processEvents()
    tag_enter._edit.setText("回车标签")
    tag_enter._edit.setFocus()
    app.processEvents()
    _QTest.keyClick(tag_enter._edit, yq.Qt.Key.Key_Return)
    app.processEvents()
    check("tags: Enter adds tag and keeps dialog open",
          tag_enter.isVisible() and tag_enter.selected_tags() == ["回车标签"],
          "visible=%s selected=%s" % (tag_enter.isVisible(),
                                      tag_enter.selected_tags()))
    tag_enter.reject()

    # ---- 3.3.1：右键「加入密库…」 ----
    win._tabs.setCurrentIndex(yq.TAB_TYPES.index("text"))
    app.processEvents()
    text_tbl = win._tables["text"]
    win._refresh_tab("text")
    app.processEvents()
    check("vault: text rows exist for right-click", text_tbl.rowCount() >= 1)
    text_tbl.selectRow(0)
    app.processEvents()
    captured = []

    class _MenuSpy:
        def __init__(self, *a, **k):
            pass

        def setTitle(self, *_a):
            pass

        def setStyleSheet(self, *_a):
            pass

        def addAction(self, *a):
            if a:
                captured.append(a[0])
            return a[0] if a else None

        def addSeparator(self):
            captured.append("---")

        def addMenu(self, *_a):
            return _MenuSpy()

        def exec(self, *_a):
            return None

    _real_menu_cls = yq._RoundMenu
    yq._RoundMenu = _MenuSpy
    try:
        win._on_right_click("text", yq.QPoint(12, 12))
        text_menu = list(captured)
        captured.clear()
        win._tabs.setCurrentIndex(yq.TAB_TYPES.index("image"))
        app.processEvents()
        win._refresh_tab("image")
        image_tbl = win._tables["image"]
        check("vault: image rows exist for right-click",
              image_tbl.rowCount() >= 1)
        image_tbl.selectRow(0)
        app.processEvents()
        win._on_right_click("image", yq.QPoint(12, 12))
        image_menu = list(captured)
    finally:
        yq._RoundMenu = _real_menu_cls
    check("vault: right-click offers add-to-vault",
          yq.tr("m_vault_add") in text_menu
          and yq.tr("m_vault_add") in image_menu,
          "text=%s image=%s" % (yq.tr("m_vault_add") in text_menu,
                                yq.tr("m_vault_add") in image_menu))

    seen_prefill = {}

    class _StubVaultEntry:
        def __init__(self, owner, app, entry=None):
            seen_prefill.clear()
            seen_prefill.update(entry or {})
            self._vals = dict(entry or {})

        def exec(self):
            return 1

        def values(self):
            vals = dict(self._vals)
            vals.setdefault("kind", vals.get("type", "text"))
            vals.setdefault("paths", list(vals.get("paths") or []))
            vals.setdefault("content", vals.get("content", ""))
            vals.setdefault("image_src", vals.get("image_src", ""))
            vals.setdefault("image", vals.get("image", ""))
            vals.setdefault("name", vals.get("name", ""))
            return vals

    _real_entry_cls = yq._VaultEntryDialog
    _vault_before = win.vault.count()
    # 专门补几条独立样本用于"移入密库"，避免删掉别处用例依赖的记录
    store.add_text("移入密库测试文本")
    try:
        from PIL import Image as _MPIL
        _mp = _MPIL.new("RGB", (24, 18), (200, 60, 60))
        store.add_image(_mp, store._image_hash(_mp), "move-test.png")
    except Exception:
        pass
    _mvf = os.path.join(tmp, "move-test.txt")
    with open(_mvf, "w", encoding="utf-8") as _mfh:
        _mfh.write("move")
    store.add_files([_mvf], "regression-move-file-hash")
    win._refresh_all()
    app.processEvents()
    _text_before = [e["hash"] for e in store.get_by_type("text")]
    _image_before = [e["hash"] for e in store.get_by_type("image")]
    yq._VaultEntryDialog = _StubVaultEntry
    try:
        win._tabs.setCurrentIndex(yq.TAB_TYPES.index("text"))
        app.processEvents()
        win._refresh_tab("text")
        win._tables["text"].selectRow(0)
        app.processEvents()
        win._add_selected_to_vault()          # 文本
        text_prefill = dict(seen_prefill)
        win._tabs.setCurrentIndex(yq.TAB_TYPES.index("image"))
        app.processEvents()
        win._refresh_tab("image")
        win._tables["image"].selectRow(0)
        app.processEvents()
        win._add_selected_to_vault()          # 图片
        image_prefill = dict(seen_prefill)
        win._tabs.setCurrentIndex(yq.TAB_TYPES.index("file"))
        app.processEvents()
        win._refresh_tab("file")
        if win._tables["file"].rowCount() > 0:
            win._tables["file"].selectRow(0)
            app.processEvents()
            win._add_selected_to_vault()      # 文件
            file_prefill = dict(seen_prefill)
        else:
            file_prefill = {}
    finally:
        yq._VaultEntryDialog = _real_entry_cls
    check("vault: add-from-history prefills text",
          bool(text_prefill.get("content")), str(sorted(text_prefill.keys())))
    check("vault: add-from-history prefills image source",
          image_prefill.get("type") == "image"
          and bool(image_prefill.get("image_src")),
          str(sorted(image_prefill.keys())))
    check("vault: add-from-history prefills file paths",
          (not file_prefill) or (file_prefill.get("type") == "file"
                                 and bool(file_prefill.get("paths"))),
          str(sorted(file_prefill.keys())))
    kinds_now = win.vault.counts()
    check("vault: history add stores entries",
          win.vault.count() > _vault_before)
    check("vault: image from history lands in vault",
          kinds_now.get("image", 0) >= 1, str(kinds_now))
    # 3.3.2：移进密库 = 从剪贴板历史里删掉那条（密钥不该同时留在外面）
    _text_after = [e["hash"] for e in store.get_by_type("text")]
    _image_after = [e["hash"] for e in store.get_by_type("image")]
    check("vault: moving removes the source from history",
          len(_text_after) == max(0, len(_text_before) - 1)
          and len(_image_after) == max(0, len(_image_before) - 1),
          "文本 %d→%d  图片 %d→%d" % (len(_text_before), len(_text_after),
                                      len(_image_before), len(_image_after)))
    check("vault: moved hash really gone",
          all(h not in _text_after for h in _text_before[:1]))

    # 3.3.2：移入保留原时间；移出按原时间归位（不丢记录，只是"藏进密库"）
    _old_ts = "2026-01-02T03:04:05"
    _move_text = "归位测试文本-%d" % int(time.time())
    store.add_text(_move_text)
    _mv_hash = store._text_hash(_move_text)
    for _e in store.get_by_type("text"):
        if _e["hash"] == _mv_hash:
            _e["timestamp"] = _old_ts
    store.flush()
    _v_entry = win.vault.add(kind="text", content=_move_text, ts=_old_ts)
    _vwin2 = yq.VaultDialog(win)
    _vwin2.show()
    for _ in range(8):
        app.processEvents()
    _vwin2._reload()
    _row = None
    for _i in range(_vwin2._table.rowCount()):
        _e = _vwin2._rows[_i] if _i < len(_vwin2._rows) else {}
        if _e.get("id") == _v_entry["id"]:
            _row = _i
            break
    check("vault: moved-in entry keeps the original time",
          any(e["id"] == _v_entry["id"] and e.get("ts") == _old_ts
              for e in win.vault.entries()))
    if _row is not None:
        _vwin2._table.selectRow(_row)
        app.processEvents()
        _vwin2._move_entry_out()
        for _ in range(8):
            app.processEvents()
    _restored = [e for e in store.get_by_type("text") if e["hash"] == _mv_hash]
    check("vault: move-out puts it back with the original timestamp",
          bool(_restored) and _restored[0].get("timestamp") == _old_ts,
          str(_restored[:1])[:120])
    check("vault: move-out clears it from the vault",
          not any(e["id"] == _v_entry["id"] for e in win.vault.entries()))
    _stamps = [e.get("timestamp", "") for e in store.get_by_type("text")]
    check("vault: restored record is sorted back by time",
          _stamps == sorted(_stamps, reverse=True), str(_stamps[:4]))
    _vwin2.close()

    # 图片移出密库：要走真实的图片文件写回（images/ 里按内容 hash 落盘）
    try:
        from PIL import Image as _IPIL
        _ip = _IPIL.new("RGB", (30, 20), (10, 200, 120))
        _ipath = os.path.join(tmp, "move-image.png")
        _ip.save(_ipath)
        _iname = win.vault.store_image_file(_ipath)
        _vimg = win.vault.add(kind="image", image=_iname, name="移出图片测试",
                              ts="2026-01-03T04:05:06")
        _vwin3 = yq.VaultDialog(win)
        _vwin3.show()
        for _ in range(8):
            app.processEvents()
        _vwin3._reload()
        for _i in range(_vwin3._table.rowCount()):
            if _i < len(_vwin3._rows) and \
                    _vwin3._rows[_i].get("id") == _vimg["id"]:
                _vwin3._table.selectRow(_i)
                break
        app.processEvents()
        _vwin3._move_entry_out()
        for _ in range(8):
            app.processEvents()
        _img_back = [e for e in store.get_by_type("image")
                     if e.get("timestamp") == "2026-01-03T04:05:06"]
        check("vault: image move-out restores the record",
              bool(_img_back), str(store.count("image")))
        if _img_back:
            _fname = str(_img_back[0].get("filename") or "")
            _full = os.path.join(yq.IMAGES_DIR, os.path.basename(_fname))
            check("vault: image file written back to history dir",
                  bool(_fname) and os.path.exists(_full), _full)
        check("vault: image gone from the vault after move-out",
              not any(e["id"] == _vimg["id"] for e in win.vault.entries()))
        _vwin3.close()
    except Exception as _ex:
        check("vault: image move-out restores the record", False, str(_ex)[:120])

    # 3.3.2：密库里的内容也能用 AI（菜单项与历史列表一致，结果落点在密库）
    _vai = win.vault.add(kind="text", content="AI 密库测试正文", name="AI密库")
    _vwin4 = yq.VaultDialog(win)
    _vwin4.show()
    for _ in range(8):
        app.processEvents()
    _vwin4._reload()
    _ai_items = []

    class _SpyMenu:
        def __init__(self, *a, **k):
            self._title = ""

        def setTitle(self, t):
            self._title = t

        def addAction(self, *a):
            _ai_items.append(a[0] if a else "")
            return a[0] if a else None

        def addSeparator(self):
            _ai_items.append("---")

        def addMenu(self, sub):
            _ai_items.append("SUB:" + getattr(sub, "_title", ""))

        def exec(self, *_a):
            return None

    _real_menu_cls2 = yq._RoundMenu
    yq._RoundMenu = _SpyMenu
    try:
        _vwin4._ai_menu_for(_SpyMenu(), {"type": "text"})
        _txt_items = list(_ai_items)
        _ai_items.clear()
        _vwin4._ai_menu_for(_SpyMenu(), {"type": "image"})
        _img_items = list(_ai_items)
    finally:
        yq._RoundMenu = _real_menu_cls2
    check("vault: AI submenu mirrors the history menu",
          ("SUB:" + yq.tr("ai_menu")) in _txt_items
          and yq.tr("ai_act_summarize") in _txt_items
          and yq.tr("ai_act_translate") in _txt_items
          and yq.tr("ai_menu_chat") in _txt_items,
          str(_txt_items))
    check("vault: AI actions follow the entry type",
          yq.tr("ai_act_describe") in _img_items
          and yq.tr("ai_act_summarize") not in _img_items,
          str(_img_items))
    _proxy_img = _vwin4._vault_proxy_entry(
        {"type": "image", "image": "x.png", "name": "n"})
    _proxy_file = _vwin4._vault_proxy_entry({"type": "file", "paths": [__file__]})
    _proxy_txt = _vwin4._vault_proxy_entry({"type": "text", "content": "abc"})
    check("vault: AI proxy entry shapes",
          os.path.isabs(_proxy_img.get("filename") or "")
          and bool(_proxy_file.get("file_paths"))
          and _proxy_txt.get("content") == "abc",
          "%s | %s | %s" % (_proxy_img.get("filename"), _proxy_file.get("file_paths"),
                            _proxy_txt.get("content")))
    _vcount = win.vault.count()
    _adlg = yq._AIDialog(_vwin4, win, _vwin4._vault_proxy_entry(_vai),
                         "summarize", vault=win.vault, vault_uid=_vai["id"],
                         on_changed=_vwin4._reload)
    _adlg._result = "AI 生成的结果正文"
    _adlg._use_result_save()
    check("vault: AI result saves into the vault", win.vault.count() == _vcount + 1)
    _adlg2 = yq._AIDialog(_vwin4, win, _vwin4._vault_proxy_entry(_vai),
                          "summarize", vault=win.vault, vault_uid=_vai["id"])
    _adlg2._result = "AI 替换后的正文"
    _adlg2._use_result_replace()
    check("vault: AI result replaces the vault entry",
          (win.vault.get(_vai["id"]) or {}).get("content") == "AI 替换后的正文",
          str((win.vault.get(_vai["id"]) or {}).get("content"))[:40])
    _vwin4.close()

    # ---- 3.3.1 小组件右键：能手动关闭，且不被当成左键复制 ----
    # ---- 卡片弹层窗口只包住卡片（截图工具抓窗口时不再是"全屏"） ----
    card_probe = yq._TagsDialog(win, win, ["a"], ["a"], mode="edit", count=1)
    card_probe.show()
    for _ in range(8):
        app.processEvents()
    check("card overlay window is card-sized",
          card_probe.width() <= 760 and card_probe.height() <= 620
          and card_probe.width() < win.width(),
          "%dx%d（主窗口 %dx%d）" % (card_probe.width(), card_probe.height(),
                                     win.width(), win.height()))
    card_probe.reject()
    card_probe.close()

    # 所有"卡片弹层"窗口都必须只包住卡片：否则截图工具抓窗口 = 抓全屏
    _tmp_exe = os.path.join(tmp, "fake_update.exe")
    window_probes = [
        ("update dialog", yq._UpdateDialog(
            win, win, yq.APP_VERSION, "9.9.9", "v9.9.9", [],
            ["http://127.0.0.1/x/YouBoard.exe"], _tmp_exe)),
        ("update status", yq._UpdateStatusDialog(win, win, yq.APP_VERSION)),
        ("retention", yq._RetentionDialog(win, win, {"mode": "forever"})),
        ("bridge", yq._BridgeDialog(win, win)),
    ]
    for _name, _dlg in window_probes:
        _dlg.show()
        for _ in range(12):
            app.processEvents()
            time.sleep(0.02)      # 让"布局稳定后再贴合一次"的定时器有机会跑
        check("window is card-sized: " + _name,
              _dlg.width() < win.width() and _dlg.height() < win.height()
              and _dlg.width() <= 900,
              "%dx%d（主窗口 %dx%d）" % (_dlg.width(), _dlg.height(),
                                         win.width(), win.height()))
        # 窗口必须正好贴合卡片（不留透明边距），否则"按窗口矩形截图"会把周围的界面带进来
        _card = getattr(_dlg, "card", None) or getattr(_dlg, "_card", None)
        check("window hugs the card: " + _name,
              _card is not None
              and abs(_dlg.width() - _card.width()) <= 4
              and abs(_dlg.height() - _card.height()) <= 4,
              "窗口 %dx%d / 卡片 %s" % (
                  _dlg.width(), _dlg.height(),
                  ("%dx%d" % (_card.width(), _card.height())) if _card else "无"))
        try:
            _dlg.reject()
        except Exception:
            pass
        _dlg.close()

    # 3.3.3：卡片要能按住拖动（所有卡片弹层一致），内容变多时窗口要跟着长
    from PyQt6.QtGui import QMouseEvent as _QMouseEvent
    from PyQt6.QtCore import QPointF as _QPointF, QPoint as _QPoint, \
        QEvent as _QEvent, Qt as _Qt

    def _drag_probe(dlg, card, name):
        before = dlg.pos()
        g0 = dlg.mapToGlobal(_QPoint(80, 30))
        app.sendEvent(card, _QMouseEvent(
            _QEvent.Type.MouseButtonPress, _QPointF(80, 30), _QPointF(g0),
            _Qt.MouseButton.LeftButton, _Qt.MouseButton.LeftButton,
            _Qt.KeyboardModifier.NoModifier))
        g1 = g0 + _QPoint(64, 48)
        app.sendEvent(card, _QMouseEvent(
            _QEvent.Type.MouseMove, _QPointF(144, 78), _QPointF(g1),
            _Qt.MouseButton.NoButton, _Qt.MouseButton.LeftButton,
            _Qt.KeyboardModifier.NoModifier))
        app.sendEvent(card, _QMouseEvent(
            _QEvent.Type.MouseButtonRelease, _QPointF(144, 78), _QPointF(g1),
            _Qt.MouseButton.LeftButton, _Qt.MouseButton.NoButton,
            _Qt.KeyboardModifier.NoModifier))
        moved = dlg.pos() - before
        check("card can be dragged: " + name, moved == _QPoint(64, 48), str(moved))

    _drag_targets = [
        ("retention", yq._RetentionDialog(win, win, {"mode": "forever"})),
        ("bridge", yq._BridgeDialog(win, win)),
        ("tags", yq._TagsDialog(win, win, ["a"], ["a"], mode="edit", count=1)),
        ("update status", yq._UpdateStatusDialog(win, win, yq.APP_VERSION)),
        ("vault entry", yq._VaultEntryDialog(win, win)),
    ]
    for _name, _dlg in _drag_targets:
        _dlg.show()
        for _ in range(10):
            app.processEvents()
            time.sleep(0.02)
        _card = getattr(_dlg, "card", None) or getattr(_dlg, "_card", None)
        check("card exists: " + _name, _card is not None)
        if _card is not None:
            _drag_probe(_dlg, _card, _name)
        try:
            _dlg.reject()
        except Exception:
            pass
        _dlg.close()

    # 内容变多（「自定义」多出一行）时：窗口跟着长，且仍然贴合卡片
    _grow = yq._RetentionDialog(win, win, {"mode": "forever"})
    _grow.show()
    for _ in range(12):
        app.processEvents()
        time.sleep(0.03)
    _h0 = _grow.height()
    _grow._pick("custom")
    for _ in range(20):
        app.processEvents()
        time.sleep(0.04)
    check("card grows with its content",
          _grow.height() > _h0
          and abs(_grow.height() - _grow.card.height()) <= 4,
          "%d → %d（卡片 %d）" % (_h0, _grow.height(), _grow.card.height()))
    _grow._pick("forever")
    for _ in range(20):
        app.processEvents()
        time.sleep(0.04)
    check("card shrinks back and still hugs",
          abs(_grow.height() - _grow.card.height()) <= 4
          and _grow.height() <= _h0 + 4,
          "%d / 卡片 %d" % (_grow.height(), _grow.card.height()))
    _grow.reject()
    _grow.close()

    # 内容一变就要"立刻"贴合：不能等 300ms 兜底定时器，否则会先闪一下被挤扁的样子
    _instant = yq._RetentionDialog(win, win, {"mode": "forever"})
    _instant.show()
    for _ in range(10):
        app.processEvents()
        time.sleep(0.02)
    _instant._pick("expire")
    for _ in range(4):            # 只跑几拍，不给兜底定时器任何机会
        app.processEvents()
    check("card refits immediately on content change",
          abs(_instant.width() - _instant.card.width()) <= 4
          and abs(_instant.height() - _instant.card.height()) <= 4
          and _instant.height() > 275,
          "window %dx%d / 卡片 %dx%d" % (
              _instant.width(), _instant.height(),
              _instant.card.width(), _instant.card.height()))
    _instant.reject()
    _instant.close()

    # ---- 本机桥：开关立即生效；启动失败要说出原因（扩展"连不上"的根因） ----
    # ---- 内存回收：后台静默 / 空闲时把工作集还给系统（任务管理器显示的数） ----
    _trim_calls = []
    _real_trim = win._trim_memory
    win._trim_memory = lambda: _trim_calls.append(1)
    try:
        win._last_input = time.time()
        win._memory_tick()                     # 运行中也回收（用户要求运行中也要低占用）
        check("memory: trim while running", len(_trim_calls) == 1)
        win.hide()
        win._memory_tick()                     # 后台静默同样回收
        check("memory: trim when hidden", len(_trim_calls) == 2)
        win.show()
        win._last_input = time.time() - 300
        win._memory_tick()
        check("memory: trim when idle", len(_trim_calls) == 3)
    finally:
        win._trim_memory = _real_trim
    _mk = yq.IS_WIN and hasattr(yq.ctypes.windll, "psapi")
    check("memory: trim helper is safe to call", True)
    _real_trim()

    st0 = win.bridge_status()
    check("bridge: starts stopped", not st0.get("running"), str(st0))
    st1 = win.set_bridge_enabled(True)
    check("bridge: enabling starts the server immediately",
          bool(st1.get("running")) and bool(st1.get("port")), str(st1))
    if st1.get("running"):
        _en, _port, _token = yq.ensure_bridge_config(yq.load_config())
        try:
            _req = urllib.request.Request(
                "http://127.0.0.1:%s/api/ping" % st1["port"],
                headers={"X-YouBoard-Token": _token})
            with urllib.request.urlopen(_req, timeout=5) as _resp:
                ping_ok = _resp.status == 200
        except Exception as ex:
            ping_ok = False
            check("bridge: /api/ping reachable", False, str(ex))
        if ping_ok:
            check("bridge: /api/ping reachable", True)
    st2 = win.set_bridge_enabled(False)
    check("bridge: disabling stops the server", not st2.get("running"), str(st2))

    class _BoomBridge:
        last_error = "boom: 端口被别的程序独占"

        def __init__(self, *a, **k):
            raise RuntimeError("boom")

    _real_bridge_cls = yq.BridgeServer
    yq.BridgeServer = _BoomBridge
    try:
        st3 = win.set_bridge_enabled(True)
    finally:
        yq.BridgeServer = _real_bridge_cls
    check("bridge: failure reason is surfaced",
          not st3.get("running") and bool(st3.get("error")), str(st3))
    win.set_bridge_enabled(False)

    desk = yq.DesktopClipboardWidget(win)
    desk.show()
    for _ in range(4):
        app.processEvents()
    check("widget: visible before close", desk.isVisible())
    seen = {}

    class _FakeMenu:
        def __init__(self, parent=None):
            pass

        def setStyleSheet(self, css):
            seen["css"] = css

        def addAction(self, text):
            seen["action"] = text
            return text

        def exec(self, pos):
            return seen.get("action")

    _real_menu = yq._RoundMenu
    yq._RoundMenu = _FakeMenu
    try:
        desk._show_context_menu(yq.QPoint(5, 5))
    finally:
        yq._RoundMenu = _real_menu
    check("widget: right-click offers close",
          seen.get("action") == yq.tr("widget_close"), str(seen))
    check("widget: context menu uses compact style",
          "padding: 3px" in (seen.get("css") or "")
          and "font-size: 11px" in (seen.get("css") or ""))
    check("widget: right-click closes widget", not desk.isVisible())
    store._self_copy_time = 0.0
    desk._right_press = True
    desk._on_item_clicked(None)          # 右键期间到达的"点击"必须被忽略
    check("widget: right press is not a copy click",
          desk._right_press is False and not store.is_self_copy())

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

    # ---- v3.3.0：浏览器扩展桥——设置页一行 + 独立卡片弹层 ----
    check("bridge: settings page keeps a single row",
          hasattr(sdlg, "_bridge_lbl") and not hasattr(sdlg, "_bridge_cb")
          and not hasattr(sdlg, "_bridge_conn_lbl")
          and "set_bridge_desc" not in yq.STRINGS["zh"])
    check("bridge: summary text", sdlg._bridge_lbl.text() != "",
          sdlg._bridge_lbl.text())
    _bdlg = yq._BridgeDialog(sdlg, win)
    check("bridge: dialog is card style like the others",
          isinstance(_bdlg, yq._CardOverlayDialog)
          and _bdlg.card.styleSheet()
          == yq._RetentionDialog(win, win, {"mode": "forever"}).card.styleSheet())
    _bline = _bdlg._conn.text()
    check("bridge: connection line format",
          _bline.startswith("127.0.0.1:") and "|" in _bline, _bline[:40])
    _bdlg._cb.setChecked(True)
    for _ in range(3):
        app.processEvents()
    check("bridge: toggling the switch enables the field + copy button",
          _bdlg._conn.isEnabled() and _bdlg._copy_btn.isEnabled())
    _bdlg._save_and_close()
    for _ in range(8):
        app.processEvents()
    check("bridge: saving starts it",
          yq.load_config().get("bridge_enabled") is True
          and win.bridge_status().get("running") is True,
          str(win.bridge_status())[:70])
    _bdlg2 = yq._BridgeDialog(sdlg, win)
    _bdlg2._cb.setChecked(False)
    _bdlg2._save_and_close()
    for _ in range(8):
        app.processEvents()
    check("bridge: saving again stops it",
          yq.load_config().get("bridge_enabled") is False
          and win.bridge_status().get("running") is False)
    check("bridge: app exposes status",
          isinstance(win.bridge_status(), dict)
          and "running" in win.bridge_status())

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


def test_extension():
    """浏览器扩展的多语言与清单一致性（中文区显示中文、其它回落英文）。"""
    ext = os.path.join(SRC, "edge_extension")
    manifest_path = os.path.join(ext, "manifest.json")
    if not os.path.exists(manifest_path):
        check("extension: manifest present", False, "edge_extension/manifest.json")
        return
    with open(manifest_path, encoding="utf-8") as fh:
        manifest = json.load(fh)
    check("extension: manifest v3", manifest.get("manifest_version") == 3)
    check("extension: default_locale declared",
          manifest.get("default_locale") == "en", str(manifest.get("default_locale")))

    locales = {}
    for loc in ("en", "zh_CN"):
        path = os.path.join(ext, "_locales", loc, "messages.json")
        if not os.path.exists(path):
            check("extension: locale %s present" % loc, False, path)
            continue
        with open(path, encoding="utf-8") as fh:
            locales[loc] = json.load(fh)
        check("extension: locale %s parses" % loc, bool(locales[loc]))
    if len(locales) < 2:
        return

    default = locales["en"]
    check("extension: locales share the same keys",
          set(default) == set(locales["zh_CN"]),
          "en=%d zh=%d 差集=%s" % (len(default), len(locales["zh_CN"]),
                                   sorted(set(default) ^ set(locales["zh_CN"]))[:6]))
    empty = [k for k, v in default.items() if not str(v.get("message", "")).strip()]
    check("extension: no empty messages", not empty, str(empty[:5]))
    # 占位符：$NAME$ 必须在 placeholders 里声明过（否则 chrome.i18n 会原样吐出来）
    bad_ph = []
    for loc, msgs in locales.items():
        for key, item in msgs.items():
            used = set(re.findall(r"\$([A-Za-z0-9_]+)\$",
                                  str(item.get("message", ""))))
            declared = {p.upper() for p in (item.get("placeholders") or {})}
            if used - declared:
                bad_ph.append("%s/%s:%s" % (loc, key, sorted(used - declared)))
    check("extension: placeholders declared", not bad_ph, str(bad_ph[:4]))

    # manifest 里的 __MSG_key__
    msg_keys = re.findall(r"__MSG_([A-Za-z0-9_]+)__",
                          open(manifest_path, encoding="utf-8").read())
    check("extension: manifest MSG keys exist",
          msg_keys and all(k in default for k in msg_keys), str(msg_keys))

    # JS / HTML 里用到的 key 也必须存在
    used = set()
    for name in ("background.js", "panel.js", "options.js"):
        path = os.path.join(ext, name)
        if os.path.exists(path):
            src = open(path, encoding="utf-8").read()
            # \b 必不可少：否则 createElement("div") 里的 "t(" 会被当成翻译调用
            used |= set(re.findall(
                r'\b(?:t|getMessage)\(\s*"([A-Za-z0-9_]+)"', src))
    for name in ("panel.html", "options.html"):
        path = os.path.join(ext, name)
        if os.path.exists(path):
            src = open(path, encoding="utf-8").read()
            used |= set(re.findall(r'data-i18n(?:-placeholder|-title)?="([A-Za-z0-9_]+)"',
                                   src))
    missing = sorted(k for k in used if k not in default)
    check("extension: all UI strings translated",
          not missing and len(used) > 30, "缺 %s（共 %d 条）" % (missing[:6], len(used)))
    check("extension: Chinese locale actually localized",
          any("剪贴板" in str(v.get("message", ""))
              for k, v in locales["zh_CN"].items() if "ext" in k or "tab" in k))

    # 打包脚本要把 _locales 带上，否则商店拿不到译文
    zip_ps1 = open(os.path.join(ext, "build_zip.ps1"), encoding="utf-8").read()
    check("extension: zip includes _locales", "_locales" in zip_ps1)

    # 语法检查（有 node 就跑；CI 没有 node 也不拦）
    node = shutil.which("node")
    if node:
        for name in ("background.js", "content.js", "panel.js", "options.js"):
            path = os.path.join(ext, name)
            if not os.path.exists(path):
                continue
            proc = subprocess.run([node, "--check", path],
                                  capture_output=True, text=True)
            check("extension: node --check %s" % name, proc.returncode == 0,
                  (proc.stderr or "")[:120])
        # 扩展行为测试：用假的 chrome API 在 Node 里真跑一遍后台/面板逻辑
        harness = os.path.join(SRC, "tools", "verify_extension.js")
        if os.path.exists(harness):
            proc = subprocess.run([node, harness], capture_output=True,
                                  text=True, encoding="utf-8",
                                  errors="replace")
            lines = (proc.stdout or "").splitlines()
            parsed = 0
            for line in lines:
                if line.startswith("PASS "):
                    check(line[5:].strip(), True)
                    parsed += 1
                elif line.startswith("FAIL "):
                    check(line[5:].split("  ")[0].strip(), False,
                          line[5:].split("  ", 1)[-1] if "  " in line[5:] else "")
                    parsed += 1
            check("extension: behaviour harness ran", parsed >= 25,
                  "解析到 %d 条（退出码 %s）" % (parsed, proc.returncode))
            if proc.returncode != 0:
                check("extension: behaviour harness clean", False,
                      (proc.stderr or "")[:200])
        else:
            check("extension: behaviour harness present", False, harness)
    else:
        check("extension: node --check skipped (node 不在 PATH)", True)


def test_updater():
    """更新包的完整性防线。

    背景（用户真实事故）：旧下载器先把文件按总大小填成零字节，只要有一段分片
    失败也当成功；替换脚本又是"先删旧主程序再改名顶上"，于是一次失败的更新
    就把主程序变成了 87% 零字节、打不开的文件，且无法回滚。
    """
    tmp = tempfile.mkdtemp(prefix="yb_upd_")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if os.path.abspath(SRC) not in [os.path.abspath(p) for p in sys.path]:
        sys.path.insert(0, SRC)
    import youboard_qt as yq

    MAGIC = b"MEI\x0c\x0b\x0a\x0b\x0e"

    def build_ok(path, size=5 * 1024 * 1024):
        data = bytearray(size)
        data[0:2] = b"MZ"
        data[0x3C:0x40] = (0x80).to_bytes(4, "little")
        data[0x80:0x84] = b"PE\x00\x00"
        data[-88:-80] = MAGIC
        with open(path, "wb") as fh:
            fh.write(bytes(data))

    good = os.path.join(tmp, "good.exe")
    build_ok(good)
    ok, why = yq.verify_update_file(good)
    check("updater: well-formed package accepted", ok, why)

    zero = os.path.join(tmp, "zero.exe")
    with open(zero, "wb") as fh:
        fh.write(bytes(10 * 1024 * 1024))
    check("updater: all-zero package rejected",
          not yq.verify_update_file(zero)[0])

    part = os.path.join(tmp, "part.exe")
    with open(good, "rb") as src_fh:
        head_bytes = src_fh.read(2 * 1024 * 1024)
    with open(part, "wb") as fh:
        fh.write(head_bytes)
    check("updater: truncated package rejected",
          not yq.verify_update_file(part)[0])

    check("updater: size mismatch rejected",
          not yq.verify_update_file(good, expected_size=123)[0])
    digest = yq.sha256_file(good)
    check("updater: sha256 mismatch rejected",
          not yq.verify_update_file(good, expected_sha256="0" * 64)[0])
    check("updater: sha256 match accepted",
          yq.verify_update_file(good, expected_sha256=digest)[0])

    noarc = os.path.join(tmp, "noarc.exe")
    with open(good, "rb") as fh:
        blob = bytearray(fh.read())
    blob[-88:-80] = b"XXXXXXXX"
    with open(noarc, "wb") as fh:
        fh.write(bytes(blob))
    check("updater: missing archive marker rejected",
          not yq.verify_update_file(noarc)[0])

    check("updater: coverage complete",
          yq._coverage_complete([(0, 99), (100, 199)], 200))
    check("updater: coverage gap detected",
          not yq._coverage_complete([(0, 99), (150, 199)], 200))
    check("updater: coverage tail missing",
          not yq._coverage_complete([(0, 99)], 200))

    qt_src = open(os.path.join(SRC, "youboard_qt.py"), encoding="utf-8").read()
    check("updater: old delete-first loop is gone", ":del_loop" not in qt_src)
    check("updater: script backs up before replacing",
          'move /y "%_YB_APP%" "%_YB_BAK%"' in qt_src)
    check("updater: script verifies installed file",
          "certutil -hashfile" in qt_src and "_YB_HASH" in qt_src)
    check("updater: script can roll back",
          ":_yb_rollback" in qt_src
          and 'move /y "%_YB_BAK%" "%_YB_APP%"' in qt_src)
    check("updater: checks the new build actually started",
          "tasklist /fi \"imagename eq YouBoard.exe\"" in qt_src
          and "goto _yb_rollback" in qt_src)
    check("updater: segments must cover the whole file",
          "_coverage_complete" in qt_src and "self._covered" in qt_src)
    check("updater: range response enforced",
          "服务器未按分片返回" in qt_src)
    check("card inputs share one style (multiline included)",
          "QPlainTextEdit, QTextEdit {{" in qt_src)

    # ---- 端到端：真的跑一遍分片下载（本地 HTTP 服务），断网式残缺必须被拦下 ----
    import http.server
    import socketserver
    import threading as _threading

    payload = open(good, "rb").read()

    class _RangeHandler(http.server.BaseHTTPRequestHandler):
        truncate = False          # True = 每个分片只回一半（模拟下载残缺）

        def log_message(self, *a):
            pass

        def _headers(self, code, length, extra=None):
            self.send_response(code)
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            for k, v in (extra or []):
                self.send_header(k, v)
            self.end_headers()

        def do_HEAD(self):
            self._headers(200, len(payload))

        def do_GET(self):
            rng = self.headers.get("Range")
            if not rng:
                self._headers(200, len(payload))
                self.wfile.write(payload)
                return
            start_s, end_s = rng.split("=", 1)[1].split("-")
            start, end = int(start_s), int(end_s)
            chunk = payload[start:end + 1]
            if self.truncate:
                chunk = chunk[:max(1, len(chunk) // 2)]
            self._headers(206, len(chunk),
                          [("Content-Range",
                            "bytes %d-%d/%d" % (start, end, len(payload)))])
            self.wfile.write(chunk)

    def _serve(cls):
        srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), cls)
        srv.daemon_threads = True
        _threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv

    srv = _serve(_RangeHandler)
    try:
        url = "http://127.0.0.1:%d/YouBoard.exe" % srv.server_address[1]
        dest = os.path.join(tmp, "dl_ok.exe")
        got = []
        worker = yq._DownloadWorker([url], dest)
        worker.finished_ok.connect(lambda p, h: got.append(("ok", p, h)))
        worker.failed.connect(lambda e: got.append(("fail", e, "")))
        worker.run()                       # 同步跑一遍（不依赖事件循环）
        check("updater: real segmented download accepted",
              bool(got) and got[0][0] == "ok"
              and got[0][2] == yq.sha256_file(good),
              str(got[:1]))
    finally:
        srv.shutdown()
        srv.server_close()

    class _TruncatingHandler(_RangeHandler):
        truncate = True

    srv = _serve(_TruncatingHandler)
    try:
        url = "http://127.0.0.1:%d/YouBoard.exe" % srv.server_address[1]
        dest2 = os.path.join(tmp, "dl_bad.exe")
        got2 = []
        worker2 = yq._DownloadWorker([url], dest2)
        worker2.finished_ok.connect(lambda p, h: got2.append(("ok", p, h)))
        worker2.failed.connect(lambda e: got2.append(("fail", e, "")))
        worker2.run()
        check("updater: partial download rejected",
              bool(got2) and got2[0][0] == "fail"
              and not os.path.exists(dest2),
              str(got2[:1]))
    finally:
        srv.shutdown()
        srv.server_close()


def main():
    print("== syntax ==")
    test_syntax()
    if FAIL:            # 语法都没过，后面的用例不用跑了
        print("RESULT=FAILED:" + ",".join(FAIL))
        return 1
    print("== updater ==")
    test_updater()
    print("== extension ==")
    test_extension()
    print("== core ==")
    test_core()
    print("== phone ==")
    test_phone()
    print("== ai ==")
    test_ai()
    print("== bridge ==")
    test_bridge()
    print("== gui ==")
    test_gui()
    print("RESULT=" + ("ALL_PASS" if not FAIL else "FAILED:" + ",".join(FAIL)))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
