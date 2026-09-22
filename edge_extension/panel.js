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

/* ---- 多语言（浏览器界面是中文就中文，其它语言回落到英文） ---- */
function t(key, subs) {
  try {
    var msg = chrome.i18n.getMessage(key, subs);
    return msg || key;
  } catch (e) {
    return key;
  }
}

function applyI18n(root) {
  var scope = root || document;
  scope.querySelectorAll("[data-i18n]").forEach(function (el) {
    el.textContent = t(el.getAttribute("data-i18n"));
  });
  scope.querySelectorAll("[data-i18n-placeholder]").forEach(function (el) {
    el.setAttribute("placeholder", t(el.getAttribute("data-i18n-placeholder")));
  });
  scope.querySelectorAll("[data-i18n-title]").forEach(function (el) {
    el.setAttribute("title", t(el.getAttribute("data-i18n-title")));
  });
  try {
    document.documentElement.lang = chrome.i18n.getUILanguage() || "en";
  } catch (e) {
    /* 忽略 */
  }
}

applyI18n();

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
  // 桌面端历史给的是 ISO 字符串（"2026-09-22T15:20:41"），浏览器本地记录给的是毫秒数；
  // 以前一律 Number(ts)，ISO 字符串会变成 NaN → 全部显示成"现在"，看着像时间全错。
  var d = null;
  if (typeof ts === "number" && isFinite(ts)) {
    d = new Date(ts);
  } else if (typeof ts === "string" && ts) {
    var asNum = Number(ts);
    d = new Date(isFinite(asNum) && asNum > 1000000000 ? asNum : ts);
  }
  if (!d || isNaN(d.getTime())) d = new Date();
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
    setStatus(t("statusConnected"), "ok");
  } else {
    setStatus(t("statusDisconnected"), "bad");
  }
}

function itemNode(item, fromDesktop) {
  var li = document.createElement("li");
  li.className = "item";

  var row1 = document.createElement("div");
  row1.className = "row1";
  var left = document.createElement("span");
  left.className = "badge";
  var badgeKey = { text: "badgeText", url: "badgeUrl",
                   image: "badgeImage", file: "badgeFile" }[item.type];
  left.textContent = fromDesktop
    ? (badgeKey ? t(badgeKey) : String(item.type || ""))
    : t("badgeWeb");
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
      String(item.title || "") || t("emptyItem");
  }
  li.appendChild(body);

  var acts = document.createElement("div");
  acts.className = "acts";
  var copyBtn = document.createElement("button");
  copyBtn.textContent = t("btnCopy");
  copyBtn.addEventListener("click", function (ev) {
    ev.stopPropagation();
    doCopy(item, fromDesktop);
  });
  acts.appendChild(copyBtn);

  if (!fromDesktop) {
    var insBtn = document.createElement("button");
    insBtn.textContent = t("btnInsert");
    insBtn.addEventListener("click", function (ev) {
      ev.stopPropagation();
      insertIntoPage(item.content);
    });
    acts.appendChild(insBtn);

    var upBtn = document.createElement("button");
    upBtn.textContent = t("btnSaveToYouBoard");
    upBtn.addEventListener("click", async function (ev) {
      ev.stopPropagation();
      var r = await send({ kind: "bridgePush", item: item });
      upBtn.textContent = r && r.ok ? t("savedToDesktop") : t("desktopOffline");
    });
    acts.appendChild(upBtn);

    var delBtn = document.createElement("button");
    delBtn.textContent = t("btnDelete");
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
        setStatus(t("noInputOnPage"), "bad");
      }
    });
}

async function doCopy(item, fromDesktop) {
  if (fromDesktop && item.hash && state.connected) {
    var r = await send({ kind: "bridgeCopy", hash: item.hash });
    if (r && r.ok) {
      setStatus(t("copiedToSystem"), "ok");
      return;
    }
  }
  if (item.type === "image") {
    try {
      var blob = await (await fetch(item.content)).blob();
      await navigator.clipboard.write([
        new ClipboardItem({ [blob.type || "image/png"]: blob })
      ]);
      setStatus(t("imageCopied"), "ok");
    } catch (e) {
      setStatus(t("imageCopyFailed"), "bad");
    }
    return;
  }
  try {
    await navigator.clipboard.writeText(String(item.content || ""));
    setStatus(t("copied"), "ok");
  } catch (e) {
    setStatus(t("copyFailed"), "bad");
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
      setStatus(t("noClipboardPermission"), "bad");
      return;
    }
  }
  try {
    var text = await navigator.clipboard.readText();
    if (!text) {
      setStatus(t("clipboardEmpty"), "bad");
      return;
    }
    await send({
      kind: "add",
      item: { type: "text", content: text, source: "clipboard",
              title: t("clipboardTitle"),
              ts: Date.now() }
    });
    setStatus(t("clipboardSaved"), "ok");
    loadLocal();
  } catch (e) {
    setStatus(t("readFailed"), "bad");
  }
});

$("btn-clear").addEventListener("click", async function () {
  await send({ kind: "clear" });
  loadLocal();
  setStatus(t("captureCleared"), "ok");
});

$("btn-options").addEventListener("click", function () {
  chrome.runtime.openOptionsPage();
});

(async function init() {
  await refreshBridge();
  await loadLocal();
  render();
})();
