#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""YouBoard 手机传输模块

局域网内通过二维码把剪贴板历史"搬到"手机浏览器：
- PC 端启动一个轻量 HTTP 服务（仅 Python 标准库，零体积增加）
- 弹出二维码，手机相机 / 微信扫码后直接在浏览器打开同步页
- 手机页可浏览 / 复制文本、网址，预览图片，下载文件
- 支持反向传输：手机上输入文字一键发回电脑（自动写入剪贴板与历史）

安全设计：
- 每次启动生成随机 token，二维码 URL 自带 token，请求全部校验
- 只监听局域网，不暴露公网；传输窗口关闭或应用退出即停止服务
"""

import hmac
import json
import mimetypes
import os
import re
import secrets
import socket
import threading
import urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    import qrcode
    HAS_QRCODE = True
except Exception:
    HAS_QRCODE = False

from youboard_core import IMAGES_DIR

PHONE_MODULE_VERSION = "2.6.0"
DEFAULT_PORT = 8765
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_IMG_RE = re.compile(r"^/api/img/([0-9a-f]{64})\.png$", re.I)
_FILE_RE = re.compile(r"^/api/file/([0-9a-f]{64})/(\d+)$")


_PROBE_TARGETS = ("8.8.8.8", "223.5.5.5", "114.114.114.114",
                  "1.1.1.1", "192.168.1.1", "10.0.0.1", "172.16.0.1")


def _probe_udp_ips():
    """通过 UDP 路由探测收集本机 IP（connect 不发包，瞬时完成、无 DNS）。"""
    ips = []
    seen = set()
    for dest in _PROBE_TARGETS:
        s = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect((dest, 9))
            ip = s.getsockname()[0]
            if ip and not ip.startswith("127.") and ip not in seen:
                seen.add(ip)
                ips.append(ip)
        except Exception:
            pass
        finally:
            if s is not None:
                try:
                    s.close()
                except Exception:
                    pass
    return ips


def _hostname_ips():
    """补充来源：本机主机名解析（可能触发 DNS，仅在后台线程调用）。"""
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except Exception:
        return []
    out = []
    for info in infos:
        try:
            ip = info[4][0]
        except Exception:
            continue
        if ip and not ip.startswith("127.") and ip not in out:
            out.append(ip)
    return out


def _ip_rank(ip):
    """排序：普通家用路由器常用的 192.168 最优先，其次 10.x，再 172.16-31。"""
    if ip.startswith("192.168."):
        return 0
    if ip.startswith("10."):
        return 1
    if re.match(r"^172\.(1[6-9]|2\d|3[01])\.", ip):
        return 2
    return 3


def get_lan_ips():
    """收集全部候选局域网 IP（私网优先）。可能包含虚拟网卡地址。"""
    ips = _probe_udp_ips()
    for ip in _hostname_ips():
        if ip not in ips:
            ips.append(ip)
    ips.sort(key=_ip_rank)
    return ips or ["127.0.0.1"]


def get_lan_ip():
    """快速取第一个候选 IP（仅 UDP 探测，不触发 DNS，可安全用于 GUI 线程）。"""
    ips = _probe_udp_ips()
    ips.sort(key=_ip_rank)
    return ips[0] if ips else "127.0.0.1"


def pick_free_port(base=DEFAULT_PORT):
    """从 base 起找一个可用端口（最多探测 50 个）。"""
    for port in range(base, base + 50):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("0.0.0.0", port))
            return port
        except OSError:
            pass
        finally:
            try:
                s.close()
            except Exception:
                pass
    return 0


def make_qr_pil(data, box=8, border=2):
    """生成二维码 PIL 图片；缺少 qrcode 组件时返回 None。"""
    if not HAS_QRCODE:
        return None
    try:
        qr = qrcode.QRCode(
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=box,
            border=border,
        )
        qr.add_data(data)
        qr.make(fit=True)
        img = qr.make_image(fill_color="#0e1626", back_color="#ffffff")
        return img.convert("RGB")
    except Exception:
        return None


def _norm_paths(entry):
    """兼容旧版单字符串路径，统一返回路径列表。"""
    paths = entry.get("file_paths", []) or []
    if isinstance(paths, str):
        paths = [paths]
    return [p for p in paths if isinstance(p, str)]


def _entry_to_public(entry, srv, etype):
    """把一条历史条目转成手机端 JSON（不含敏感全量内容）。"""
    item = {
        "hash": entry.get("hash", ""),
        "type": etype,
        "time": entry.get("timestamp", ""),
        "pinned": srv.is_pinned(entry.get("hash", "")),
    }
    if etype in ("text", "url"):
        content = entry.get("content", "") or ""
        item["content"] = content[:50000]
        item["length"] = len(content)
    elif etype == "image":
        h = entry.get("hash", "")
        item["width"] = entry.get("width", 0)
        item["height"] = entry.get("height", 0)
        item["file_size"] = entry.get("file_size", 0)
        item["url"] = f"/api/img/{h}.png"
    elif etype == "file":
        paths = _norm_paths(entry)
        sizes = entry.get("file_sizes", []) or []
        files = []
        for i, p in enumerate(paths):
            files.append({
                "name": os.path.basename(p) or p,
                "size": sizes[i] if i < len(sizes) else None,
                "exists": os.path.exists(p),
                "url": f"/api/file/{entry.get('hash', '')}/{i}",
            })
        item["file_count"] = entry.get("file_count", len(paths))
        item["files"] = files
    return item


class PhoneTransferServer:
    """局域网手机传输服务（线程内 HTTP 服务）。"""

    def __init__(self, store, on_receive_text=None, port=DEFAULT_PORT):
        self.store = store
        self.on_receive_text = on_receive_text
        self.port = port or DEFAULT_PORT
        self.token = secrets.token_hex(12)
        self._httpd = None
        self._thread = None
        self._clients = set()
        self._clients_lock = threading.Lock()
        self.last_error = None
        self._ready = threading.Event()
        self._stop_flag = threading.Event()

    # ---- 状态 ----

    @property
    def running(self):
        return self._httpd is not None

    @property
    def url(self):
        return f"http://{get_lan_ip()}:{self.port}/?t={self.token}"

    def client_count(self):
        with self._clients_lock:
            return len(self._clients)

    def rotate_token(self):
        """更换访问 token 并清空已连接设备记录（旧二维码立即失效，服务不中断）。"""
        self.token = secrets.token_hex(12)
        with self._clients_lock:
            self._clients.clear()

    def is_pinned(self, entry_hash):
        try:
            return self.store.is_pinned(entry_hash)
        except Exception:
            return False

    # ---- 生命周期 ----

    def start(self):
        """后台线程绑定端口并开始服务，调用方不会被阻塞。"""
        if self._httpd is not None or self._thread is not None:
            return True
        self._stop_flag.clear()
        self._ready.clear()
        self.last_error = None
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="YouBoardPhoneServer")
        self._thread.start()
        return True

    def wait_ready(self, timeout=3.0):
        """等待后台服务绑定完成；成功返回 True。"""
        self._ready.wait(timeout)
        return self._httpd is not None

    def _run(self):
        try:
            httpd = ThreadingHTTPServer(("0.0.0.0", self.port), _PhoneHandler)
        except OSError as e:
            self.last_error = str(e)
            self._ready.set()
            self._thread = None
            return
        httpd.daemon_threads = True
        httpd.phone_server = self
        if self._stop_flag.is_set():
            try:
                httpd.server_close()
            except Exception:
                pass
            self._thread = None
            return
        self._httpd = httpd
        self._ready.set()
        try:
            httpd.serve_forever()
        except Exception:
            pass
        finally:
            try:
                httpd.server_close()
            except Exception:
                pass
            self._httpd = None
            self._thread = None

    def stop(self):
        self._stop_flag.set()
        httpd = self._httpd
        self._httpd = None
        if httpd is not None:
            try:
                httpd.shutdown()
                httpd.server_close()
            except Exception:
                pass
        self._ready.clear()

    # ---- 请求处理辅助 ----

    def register_client(self, ip):
        if ip and ip != "127.0.0.1":
            with self._clients_lock:
                self._clients.add(ip)

    def auth_ok(self, token):
        return bool(token) and hmac.compare_digest(token, self.token)

    def history_json(self, limit=None, etype=None):
        all_entries = []
        for key in ("text", "image", "file", "url"):
            if etype and etype != "all" and etype != key:
                continue
            try:
                for e in self.store.get_by_type(key):
                    all_entries.append((key, e))
            except Exception:
                continue

        def _sort_key(pair):
            return pair[1].get("timestamp", "")
        all_entries.sort(key=_sort_key, reverse=True)

        # 无上限（limit 为空 / 0 / None 时返回全部），与电脑端一致
        if limit is None or (str(limit).isdigit() and int(limit) <= 0):
            selected = all_entries
        else:
            try:
                selected = all_entries[: int(limit)]
            except (ValueError, TypeError):
                selected = all_entries

        out = []
        for key, e in selected:
            try:
                out.append(_entry_to_public(e, self, key))
            except Exception:
                continue
        return out

    def find_file(self, entry_hash, idx):
        """按 hash 查找文件条目中的第 idx 个路径；找不到返回 None。"""
        for e in self.store.get_all():
            if e.get("hash") == entry_hash:
                paths = _norm_paths(e)
                try:
                    i = int(idx)
                except (ValueError, TypeError):
                    return None
                if 0 <= i < len(paths):
                    return paths[i]
                return None
        return None


_PAGE = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<meta name="robots" content="noindex">
<title>YouBoard · 手机传输</title>
<style>
:root{--bg:#0b1220;--card:#141d30;--card2:#18233a;--border:#223252;--text:#e7eefb;--muted:#8fa3c4;--accent:#4da3ff;--ok:#37c285;}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--text);font-family:-apple-system,"PingFang SC","Microsoft YaHei","Segoe UI",sans-serif;padding:14px 14px 130px;-webkit-tap-highlight-color:transparent}
header{display:flex;align-items:center;gap:10px;margin-bottom:6px}
.dot{width:10px;height:10px;border-radius:50%;background:var(--ok);box-shadow:0 0 8px var(--ok);flex:0 0 auto}
.dot.off{background:#ff6b6b;box-shadow:0 0 8px #ff6b6b}
h1{font-size:17px;font-weight:700;line-height:1.2}
.sub{font-size:12px;color:var(--muted);margin-top:2px}
.tabs{display:flex;gap:6px;overflow-x:auto;margin:12px 0;padding-bottom:2px;scrollbar-width:none}
.tabs::-webkit-scrollbar{display:none}
.chip{flex:0 0 auto;padding:6px 14px;border-radius:16px;background:var(--card);color:var(--muted);font-size:13px;border:1px solid var(--border);cursor:pointer}
.chip.on{background:var(--accent);color:#fff;border-color:var(--accent)}
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:12px;margin-bottom:10px}
.meta{display:flex;justify-content:space-between;align-items:center;font-size:11px;color:var(--muted);margin-bottom:7px}
.tag{padding:2px 8px;border-radius:8px;font-size:11px;flex:0 0 auto}
.tag.text{background:#24446e;color:#8cc7ff}.tag.image{background:#3e3a24;color:#ffd76e}
.tag.file{background:#3d2c46;color:#d9a7ff}.tag.url{background:#1e4a3c;color:#7ff0c0}
.content{font-size:14px;line-height:1.55;word-break:break-all;white-space:pre-wrap;cursor:pointer}
.content:active{opacity:.6}
.content.img{text-align:center;cursor:default}
.content.img img{max-width:100%;border-radius:8px;background:#fff;display:block;margin:0 auto}
.file-row{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:8px 0;border-top:1px solid var(--border)}
.file-row:first-child{border-top:none}
.file-row .nm{font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1 1 auto;min-width:0}
.file-row .sz{font-size:11px;color:var(--muted);flex:0 0 auto}
.file-row .st{font-size:11px;color:#ff6b6b;flex:0 0 auto}
a.dl{color:var(--accent);text-decoration:none;font-size:13px;flex:0 0 auto;padding:4px 10px;border:1px solid var(--accent);border-radius:8px}
a.dl:active{background:var(--accent);color:#fff}
.empty{color:var(--muted);text-align:center;padding:60px 0;font-size:13px}
.send{position:fixed;left:0;right:0;bottom:0;background:var(--bg);border-top:1px solid var(--border);padding:10px 14px;display:flex;gap:8px;align-items:flex-end}
.send textarea{flex:1;background:var(--card);border:1px solid var(--border);border-radius:10px;color:var(--text);padding:10px;font-size:14px;resize:none;height:44px;font-family:inherit;outline:none}
.send textarea:focus{border-color:var(--accent)}
.send button{background:var(--accent);border:none;color:#fff;border-radius:10px;padding:0 18px;font-size:14px;height:44px;flex:0 0 auto;cursor:pointer}
.send button:active{opacity:.8}
.toast{position:fixed;left:50%;top:16px;transform:translateX(-50%);background:#1e2a44;border:1px solid var(--accent);color:var(--text);padding:8px 16px;border-radius:8px;font-size:13px;opacity:0;transition:opacity .25s;z-index:9;max-width:82vw;text-align:center}
.toast.show{opacity:1}
</style>
</head>
<body>
<header>
  <span class="dot" id="dot"></span>
  <div><h1>YouBoard · 手机传输</h1><div class="sub" id="sub">...</div></div>
</header>
<div class="tabs" id="tabs"></div>
<div id="list"><div class="empty">...</div></div>
<div class="send">
  <textarea id="msg" placeholder="..."></textarea>
  <button id="sendBtn">...</button>
</div>
<div class="toast" id="toast"></div>
<script>
var TOKEN = new URLSearchParams(location.search).get('t') || '';
var ZH = (navigator.language || '').toLowerCase().indexOf('zh') === 0;
var T = {
  connecting: ZH ? '正在连接电脑…' : 'Connecting to PC…',
  online: ZH ? '已连接' : 'Connected',
  offline: ZH ? '连接断开' : 'Disconnected',
  loading: ZH ? '加载中…' : 'Loading…',
  empty: ZH ? '暂无记录' : 'No records',
  copied: ZH ? '已复制到手机' : 'Copied to phone',
  copyFail: ZH ? '复制失败' : 'Copy failed',
  sent: ZH ? '已发送到电脑' : 'Sent to PC',
  sendFail: ZH ? '发送失败' : 'Send failed',
  sendHint: ZH ? '发送文字到电脑…' : 'Send text to PC…',
  send: ZH ? '发送' : 'Send',
  all: ZH ? '全部' : 'All',
  text: ZH ? '文本' : 'Text',
  image: ZH ? '图片' : 'Image',
  file: ZH ? '文件' : 'Files',
  url: ZH ? '网址' : 'URLs',
  missing: ZH ? '已失效' : 'missing'
};
var TAGS = ['all', 'text', 'image', 'file', 'url'];
var CUR = 'all';
var ENTRIES = [];

function $(id){ return document.getElementById(id); }
function esc(s){
  return String(s == null ? '' : s).replace(/[&<>"']/g, function(c){
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
  });
}
function toast(msg){
  var el = $('toast');
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(el._t);
  el._t = setTimeout(function(){ el.classList.remove('show'); }, 1800);
}
function api(path, opts){
  opts = opts || {};
  var headers = opts.headers || {};
  headers['X-YouBoard-Token'] = TOKEN;
  return fetch(path, Object.assign({}, opts, {headers: headers}))
    .then(function(r){ return r.json().catch(function(){ return {ok:false}; }); });
}
function buildTabs(){
  var wrap = $('tabs');
  wrap.innerHTML = '';
  TAGS.forEach(function(t){
    var d = document.createElement('div');
    d.className = 'chip' + (t === CUR ? ' on' : '');
    d.textContent = T[t];
    d.onclick = function(){ CUR = t; buildTabs(); render(); };
    wrap.appendChild(d);
  });
}
function fmtTime(ts){
  if(!ts) return '';
  try{
    var d = new Date(ts);
    if(isNaN(d.getTime())) return ts;
    var p = function(n){ return (n < 10 ? '0' : '') + n; };
    return (d.getMonth()+1) + '-' + p(d.getDate()) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
  }catch(e){ return ts; }
}
function fmtSize(n){
  if(n == null || n < 0) return '';
  if(n < 1024) return n + ' B';
  if(n < 1048576) return (n/1024).toFixed(1) + ' KB';
  return (n/1048576).toFixed(1) + ' MB';
}
function card(e){
  var tagCls = {text:'text',image:'image',file:'file',url:'url'}[e.type] || 'text';
  var body = '';
  if(e.type === 'text' || e.type === 'url'){
    body = '<div class="content" onclick="copyText(this.dataset.v)" data-v="' + esc(e.content) + '">' + esc(e.content) + '</div>';
  } else if(e.type === 'image'){
    body = '<div class="content img"><img src="/api/img/' + esc(e.hash) + '.png?t=' + encodeURIComponent(TOKEN) + '" alt="image"></div>';
  } else if(e.type === 'file'){
    var rows = '';
    (e.files || []).forEach(function(f, i){
      var right = f.exists
        ? '<span class="sz">' + fmtSize(f.size) + '</span><a class="dl" href="/api/file/' + esc(e.hash) + '/' + i + '?t=' + encodeURIComponent(TOKEN) + '" download>↓</a>'
        : '<span class="st">' + esc(T.missing) + '</span>';
      rows += '<div class="file-row"><span class="nm" title="' + esc(f.name) + '">' + esc(f.name) + '</span>' + right + '</div>';
    });
    body = rows || '<div class="empty" style="padding:12px 0">-</div>';
  }
  return '<div class="card"><div class="meta"><span class="tag ' + tagCls + '">' + T[e.type] + '</span><span>' + esc(fmtTime(e.time)) + '</span></div>' + body + '</div>';
}
function render(){
  var list = $('list');
  var items = ENTRIES.filter(function(e){ return CUR === 'all' || e.type === CUR; });
  if(!items.length){
    list.innerHTML = '<div class="empty">' + T.empty + '</div>';
    return;
  }
  list.innerHTML = items.map(card).join('');
}
function copyText(txt){
  if(!txt) return;
  if(navigator.clipboard && navigator.clipboard.writeText){
    navigator.clipboard.writeText(txt).then(function(){ toast(T.copied); }, function(){ fallbackCopy(txt); });
  } else {
    fallbackCopy(txt);
  }
}
function fallbackCopy(txt){
  try{
    var ta = document.createElement('textarea');
    ta.value = txt;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    document.execCommand('copy');
    ta.remove();
    toast(T.copied);
  }catch(e){ toast(T.copyFail); }
}
function poll(){
  api('/api/history?limit=0')
    .then(function(d){
      if(!d || !d.ok) throw new Error('bad');
      ENTRIES = d.entries || [];
      $('dot').classList.remove('off');
      $('sub').textContent = T.online + ' · ' + (d.clients || 0) + ' · ' + ENTRIES.length + ' ' + T.all.toLowerCase();
      render();
    })
    .catch(function(){
      $('dot').classList.add('off');
      $('sub').textContent = T.offline;
    });
}
$('sendBtn').textContent = T.send;
$('msg').placeholder = T.sendHint;
$('sub').textContent = T.connecting;
buildTabs();
render();
poll();
setInterval(poll, 2500);
$('sendBtn').onclick = function(){
  var v = $('msg').value.trim();
  if(!v) return;
  api('/api/send', {method:'POST', body:JSON.stringify({text: v})})
    .then(function(d){
      if(d && d.ok){ toast(T.sent); $('msg').value = ''; }
      else toast(T.sendFail);
    })
    .catch(function(){ toast(T.sendFail); });
};
</script>
</body>
</html>
"""


class _PhoneHandler(BaseHTTPRequestHandler):
    """手机传输请求处理器。"""

    server_version = "YouBoardPhone/" + PHONE_MODULE_VERSION
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # 静默日志，避免刷屏
        pass

    # ---- 工具 ----

    def _srv(self):
        return getattr(self.server, "phone_server", None)

    def _token(self):
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        tok = qs.get("t", [""])[0] or ""
        if not tok:
            tok = self.headers.get("X-YouBoard-Token", "") or ""
        return tok

    def _authorized(self):
        srv = self._srv()
        if srv is None:
            return False
        if not srv.auth_ok(self._token()):
            return False
        srv.register_client(self.client_address[0])
        return True

    def _send_json(self, obj, code=200):
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(raw)
        except OSError:
            pass

    def _send_bytes(self, data, ctype, code=200, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(data)
        except OSError:
            pass

    def _send_file(self, path, download_name=None):
        if not path or not os.path.isfile(path):
            self._send_json({"ok": False, "err": "not_found"}, 404)
            return
        try:
            size = os.path.getsize(path)
            ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
            if download_name:
                try:
                    fn_ascii = download_name.encode("ascii", "ignore").decode("ascii") or "file"
                except Exception:
                    fn_ascii = "file"
                fn_utf8 = urllib.parse.quote(download_name)
                cdisp = (f"attachment; filename=\"{fn_ascii}\"; "
                         f"filename*=UTF-8''{fn_utf8}")
            else:
                cdisp = "inline"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition", cdisp)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(256 * 1024)
                    if not chunk:
                        break
                    try:
                        self.wfile.write(chunk)
                    except OSError:
                        break
        except (OSError, IOError):
            try:
                self._send_json({"ok": False, "err": "read_error"}, 500)
            except Exception:
                pass

    # ---- 路由 ----

    def do_GET(self):
        srv = self._srv()
        if srv is None:
            self._send_json({"ok": False, "err": "no_server"}, 503)
            return
        path = urllib.parse.urlparse(self.path).path

        if path in ("/favicon.ico",):
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if not self._authorized():
            self._send_json({"ok": False, "err": "auth"}, 401)
            return

        if path == "/":
            self._send_bytes(_PAGE.encode("utf-8"),
                             "text/html; charset=utf-8")
            return

        if path == "/api/ping":
            self._send_json({"ok": True, "version": PHONE_MODULE_VERSION,
                             "clients": srv.client_count()})
            return

        if path == "/api/history":
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            limit = qs.get("limit", ["0"])[0]
            etype = qs.get("type", ["all"])[0]
            try:
                limit_i = int(limit) if str(limit).isdigit() else 0
            except (ValueError, TypeError):
                limit_i = 0
            entries = srv.history_json(limit=limit_i, etype=etype)
            self._send_json({"ok": True, "clients": srv.client_count(),
                             "count": len(entries), "entries": entries})
            return

        m = _IMG_RE.match(path)
        if m:
            h = m.group(1).lower()
            img_path = os.path.join(IMAGES_DIR, h + ".png")
            if not _HEX64.match(h) or not os.path.normpath(img_path).startswith(
                    os.path.normpath(IMAGES_DIR)):
                self._send_json({"ok": False, "err": "bad_path"}, 400)
                return
            self._send_file(img_path)
            return

        m = _FILE_RE.match(path)
        if m:
            h, idx = m.group(1).lower(), m.group(2)
            fp = srv.find_file(h, idx)
            if fp:
                self._send_file(fp, download_name=os.path.basename(fp))
            else:
                self._send_json({"ok": False, "err": "not_found"}, 404)
            return

        self._send_json({"ok": False, "err": "not_found"}, 404)

    def do_POST(self):
        srv = self._srv()
        if srv is None:
            self._send_json({"ok": False, "err": "no_server"}, 503)
            return
        path = urllib.parse.urlparse(self.path).path
        if path != "/api/send":
            self._send_json({"ok": False, "err": "not_found"}, 404)
            return
        if not self._authorized():
            self._send_json({"ok": False, "err": "auth"}, 401)
            return
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length) if length > 0 else b""
            data = json.loads(raw.decode("utf-8") or "{}")
            text = str(data.get("text", "") or "").strip()
        except Exception:
            self._send_json({"ok": False, "err": "bad_json"}, 400)
            return
        if not text:
            self._send_json({"ok": False, "err": "empty"}, 400)
            return
        if srv.on_receive_text:
            try:
                srv.on_receive_text(text)
            except Exception:
                pass
        self._send_json({"ok": True, "len": len(text)})

    do_PUT = do_POST
