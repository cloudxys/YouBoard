/* YouBoard 剪贴板伴侣 —— 面板（popup）
 *
 * 两个页签：
 *   网页捕获   —— 存在浏览器本地（chrome.storage），条数可控
 *   YouBoard 历史 —— 从本机桌面版拉（127.0.0.1 本机桥），可搜索
 *
 * 单击条目 = 复制到剪贴板（桌面端的走桌面端，保证进系统剪贴板）
 * 双击条目 = 插入当前网页的输入框
 */
"use strict";

var state = { tab: "local", q: "", local: [], desktop: [], connected: false };

function $(id) { return document.getElementById(id); }

function send(msg) {
  return new Promise(function (resolve) {
    chrome.runtime.sendMessage(msg, function (resp) {
      resolve(resp || { ok: false });
    });
  });
}

function fmtTime(ts) {
  var d = new Date(Number(ts) || Date.now());
  function p(n) { return (n < 10 ? "0" : "") + n; }
  return p(d.getMonth() + 1) + "-" + p(d.getDate()) + " " +
    p(d.getHours()) + ":" + p(d.getMinutes());
}

function setStatus(text, cls) {
  var el = $("status");
  el.textContent = text;
  el.className = "status" + (cls ? " " + cls : "");
}

async function refreshBridge() {
  var resp = await send({ kind: "testBridge" });
  state.connected = !!(resp && resp.ok);
  if (state.connected) {
    setStatus("已连接桌面端", "ok");
  } else {
    setStatus("未连接桌面端", "bad");
  }
}

function itemNode(item, fromDesktop) {
  var li = document.createElement("li");
  li.className = "item";

  var row1 = document.createElement("div");
  row1.className = "row1";
  var left = document.createElement("span");
  left.className = "badge";
  left.textContent = fromDesktop
    ? ({ text: "文本", url: "网址", image: "图片", file: "文件" }[item.type] || item.type)
    : "网页";
  var right = document.createElement("span");
  right.textContent = fromDesktop ? fmtTime(item.ts) : (item.host || fmtTime(item.ts));
  row1.appendChild(left);
  row1.appendChild(right);
  li.appendChild(row1);

  var body = document.createElement("div");
  body.className = "body";
  if (item.type === "image") {
    var img = document.createElement("img");
    img.src = fromDesktop ? (item.thumb || item.content || "") : item.content;
    body.appendChild(img);
  } else {
    body.textContent = String(item.content || "").slice(0, 300) ||
      String(item.title || "") || "(空)";
  }
  li.appendChild(body);

  var acts = document.createElement("div");
  acts.className = "acts";
  var copyBtn = document.createElement("button");
  copyBtn.textContent = "复制";
  copyBtn.addEventListener("click", function (ev) {
    ev.stopPropagation();
    doCopy(item, fromDesktop);
  });
  acts.appendChild(copyBtn);

  if (!fromDesktop) {
    var insBtn = document.createElement("button");
    insBtn.textContent = "插入输入框";
    insBtn.addEventListener("click", function (ev) {
      ev.stopPropagation();
      insertIntoPage(item.content);
    });
    acts.appendChild(insBtn);

    var upBtn = document.createElement("button");
    upBtn.textContent = "存到 YouBoard";
    upBtn.addEventListener("click", async function (ev) {
      ev.stopPropagation();
      var r = await send({ kind: "bridgePush", item: item });
      upBtn.textContent = r && r.ok ? "已存到桌面端" : "桌面端未连接";
    });
    acts.appendChild(upBtn);

    var delBtn = document.createElement("button");
    delBtn.textContent = "删除";
    delBtn.addEventListener("click", async function (ev) {
      ev.stopPropagation();
      await send({ kind: "remove", key: item.key });
      loadLocal();
    });
    acts.appendChild(delBtn);
  }
  li.appendChild(acts);

  li.addEventListener("click", function () { doCopy(item, fromDesktop); });
  li.addEventListener("dblclick", function () {
    insertIntoPage(item.content);
  });
  return li;
}

async function insertIntoPage(text) {
  if (!text) return;
  var tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tabs || !tabs.length) return;
  chrome.tabs.sendMessage(tabs[0].id, { kind: "insert", text: text },
    function (resp) {
      if (!resp || !resp.ok) {
        setStatus("当前没有可输入的框", "bad");
      }
    });
}

async function doCopy(item, fromDesktop) {
  if (fromDesktop && item.hash && state.connected) {
    var r = await send({ kind: "bridgeCopy", hash: item.hash });
    if (r && r.ok) {
      setStatus("已复制到系统剪贴板", "ok");
      return;
    }
  }
  if (item.type === "image") {
    try {
      var blob = await (await fetch(item.content)).blob();
      await navigator.clipboard.write([
        new ClipboardItem({ [blob.type || "image/png"]: blob })
      ]);
      setStatus("图片已复制", "ok");
    } catch (e) {
      setStatus("图片复制失败", "bad");
    }
    return;
  }
  try {
    await navigator.clipboard.writeText(String(item.content || ""));
    setStatus("已复制", "ok");
  } catch (e) {
    setStatus("复制失败", "bad");
  }
}

function render() {
  var list = $("list");
  list.textContent = "";
  var items = state.tab === "local" ? state.local : state.desktop;
  var q = state.q.toLowerCase();
  if (q) {
    items = items.filter(function (x) {
      return String(x.content || "").toLowerCase().indexOf(q) >= 0 ||
        String(x.title || "").toLowerCase().indexOf(q) >= 0;
    });
  }
  items.slice(0, 100).forEach(function (it) {
    list.appendChild(itemNode(it, state.tab === "desktop"));
  });
  $("empty").style.display = items.length ? "none" : "block";
}

async function loadLocal() {
  var resp = await send({ kind: "list", q: "", limit: 200 });
  state.local = (resp && resp.items) || [];
  $("n-local").textContent = String(state.local.length);
  if (state.tab === "local") render();
}

async function loadDesktop() {
  if (!state.connected) {
    state.desktop = [];
    render();
    return;
  }
  var resp = await send({ kind: "bridgeHistory", q: "", limit: 60 });
  state.desktop = (resp && resp.items) || [];
  if (state.tab === "desktop") render();
}

function switchTab(tab) {
  state.tab = tab;
  $("tab-local").className = "tab" + (tab === "local" ? " active" : "");
  $("tab-desktop").className = "tab" + (tab === "desktop" ? " active" : "");
  if (tab === "desktop") loadDesktop();
  render();
}

// ---- 事件绑定 ----
$("tab-local").addEventListener("click", function () { switchTab("local"); });
$("tab-desktop").addEventListener("click", function () { switchTab("desktop"); });
$("q").addEventListener("input", function (e) {
  state.q = e.target.value || "";
  render();
  if (state.tab === "desktop" && state.q) loadDesktop();
});

$("btn-read").addEventListener("click", async function () {
  var has = await chrome.permissions.contains({ permissions: ["clipboardRead"] });
  if (!has) {
    var granted = await chrome.permissions.request({ permissions: ["clipboardRead"] });
    if (!granted) {
      setStatus("未授权读取剪贴板", "bad");
      return;
    }
  }
  try {
    var text = await navigator.clipboard.readText();
    if (!text) {
      setStatus("剪贴板是空的", "bad");
      return;
    }
    await send({
      kind: "add",
      item: { type: "text", content: text, source: "clipboard", title: "剪贴板",
              ts: Date.now() }
    });
    setStatus("已保存剪贴板内容", "ok");
    loadLocal();
  } catch (e) {
    setStatus("读取失败", "bad");
  }
});

$("btn-clear").addEventListener("click", async function () {
  await send({ kind: "clear" });
  loadLocal();
  setStatus("已清空网页捕获", "ok");
});

$("btn-options").addEventListener("click", function () {
  chrome.runtime.openOptionsPage();
});

(async function init() {
  await refreshBridge();
  await loadLocal();
  render();
})();
