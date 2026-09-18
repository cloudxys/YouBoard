/* YouBoard 剪贴板伴侣 —— 后台（MV3 service worker）
 *
 * 职责：
 *   - 接收页面传来的复制内容，去重后存本地（chrome.storage.local）
 *   - 若已连接桌面版 YouBoard（127.0.0.1 本机桥），一并送过去
 *   - 右键菜单：存选中文字 / 存本页链接 / 存这张图片
 *   - 给面板提供查询、复制（走桌面端到系统剪贴板）、删除等操作
 *
 * 隐私：不请求任何远程服务器；只与本机 127.0.0.1 上的 YouBoard 通信。
 */
"use strict";

var DEFAULTS = {
  bridgeUrl: "http://127.0.0.1:8765",
  bridgeToken: "",
  captureEnabled: true,
  syncToDesktop: true,
  maxItems: 200,
  ignoreHosts: ""
};

function getSettings() {
  return new Promise(function (resolve) {
    chrome.storage.local.get(DEFAULTS, function (got) {
      resolve(Object.assign({}, DEFAULTS, got || {}));
    });
  });
}

function setBadge(text) {
  try {
    chrome.action.setBadgeText({ text: text || "" });
    if (text) {
      chrome.action.setBadgeBackgroundColor({ color: "#2fb3a0" });
    }
  } catch (e) {
    /* 忽略 */
  }
}

function loadItems() {
  return new Promise(function (resolve) {
    chrome.storage.local.get({ yb_items: [] }, function (got) {
      resolve(Array.isArray(got.yb_items) ? got.yb_items : []);
    });
  });
}

function saveItems(items) {
  return new Promise(function (resolve) {
    chrome.storage.local.set({ yb_items: items }, resolve);
  });
}

function hostOf(url) {
  try {
    return new URL(url).hostname || "";
  } catch (e) {
    return "";
  }
}

function ignored(host, settings) {
  if (!host) return false;
  var list = String(settings.ignoreHosts || "")
    .split(/[\n,;]+/)
    .map(function (s) { return s.trim().toLowerCase(); })
    .filter(Boolean);
  for (var i = 0; i < list.length; i++) {
    if (host === list[i] || host.endsWith("." + list[i])) return true;
  }
  return false;
}

function keyOf(item) {
  if (item.hash) return item.hash;
  if (item.type === "image") {
    return "img" + String(item.content || "").length +
      String(item.content || "").slice(-64);
  }
  return "t" + String(item.content || "").slice(0, 400);
}

async function addItem(item, opts) {
  opts = opts || {};
  var settings = await getSettings();
  if (settings.captureEnabled === false && !opts.force) return null;
  var host = hostOf(item.source);
  if (ignored(host, settings)) return null;

  var saved = {
    key: keyOf(item),
    type: item.type || "text",
    content: item.content || "",
    html: item.html || "",
    source: item.source || "",
    title: item.title || "",
    host: host,
    ts: item.ts || Date.now(),
    fav: false
  };
  var items = await loadItems();
  items = items.filter(function (x) { return x.key !== saved.key; });
  items.unshift(saved);
  var max = Math.max(20, Math.min(1000, parseInt(settings.maxItems, 10) || 200));
  if (items.length > max) items = items.slice(0, max);
  await saveItems(items);

  if (settings.syncToDesktop !== false && settings.bridgeToken) {
    pushToDesktop(saved, settings).catch(function () { /* 桌面端没开就算了 */ });
  }
  return saved;
}

// ---- 本机桥（桌面版 YouBoard，仅 127.0.0.1） ----
function bridgeHeaders(settings) {
  return {
    "Content-Type": "application/json",
    "X-YouBoard-Token": settings.bridgeToken || ""
  };
}

async function bridgeFetch(path, options, settings) {
  settings = settings || (await getSettings());
  var base = String(settings.bridgeUrl || DEFAULTS.bridgeUrl).replace(/\/+$/, "");
  var resp = await fetch(base + path, Object.assign({
    headers: bridgeHeaders(settings),
    cache: "no-store"
  }, options || {}));
  if (!resp.ok) throw new Error("HTTP " + resp.status);
  return resp.json();
}

async function pushToDesktop(item, settings) {
  settings = settings || (await getSettings());
  var body = {
    type: item.type === "image" ? "image"
      : (item.type === "url" ? "url" : "text"),
    content: item.content,
    source: item.source,
    title: item.title
  };
  return bridgeFetch("/api/items", {
    method: "POST",
    body: JSON.stringify(body)
  }, settings);
}

async function testBridge() {
  try {
    var data = await bridgeFetch("/api/ping", {});
    setBadge("✓");
    return { ok: true, info: data };
  } catch (err) {
    setBadge("");
    return { ok: false, error: String((err && err.message) || err) };
  }
}

// ---- 右键菜单 ----
function buildMenus() {
  try {
    chrome.contextMenus.removeAll(function () {
      chrome.contextMenus.create({
        id: "yb-save-selection",
        title: "存到 YouBoard（选中内容）",
        contexts: ["selection"]
      });
      chrome.contextMenus.create({
        id: "yb-save-page",
        title: "存到 YouBoard（本页链接）",
        contexts: ["page"]
      });
      chrome.contextMenus.create({
        id: "yb-save-image",
        title: "存到 YouBoard（这张图片）",
        contexts: ["image"]
      });
    });
  } catch (e) {
    /* 忽略 */
  }
}

function saveImageFromUrl(url, src, title) {
  fetch(url).then(function (r) { return r.blob(); }).then(function (blob) {
    if (blob.size > 8 * 1024 * 1024) return;
    var reader = new FileReader();
    reader.onload = function () {
      addItem({
        type: "image", content: String(reader.result || ""),
        mime: blob.type || "image/png",
        source: src, title: title, ts: Date.now()
      }, { force: true });
    };
    reader.readAsDataURL(blob);
  }).catch(function () { /* 跨域图片拿不到就算了 */ });
}

function buildItemFromUrl(srcUrl, src, title, selectionText) {
  return {
    type: /^https?:\/\/\S+$/i.test(String(selectionText || "").trim())
      ? "url" : "text",
    content: selectionText,
    source: srcUrl,
    title: title,
    ts: Date.now()
  };
}

chrome.runtime.onInstalled.addListener(buildMenus);
chrome.runtime.onStartup.addListener(buildMenus);

chrome.contextMenus.onClicked.addListener(function (info, tab) {
  var src = (tab && tab.url) || "";
  var title = (tab && tab.title) || "";
  if (info.menuItemId === "yb-save-selection" && info.selectionText) {
    addItem(buildItemFromUrl(src, src, title, info.selectionText), { force: true });
  } else if (info.menuItemId === "yb-save-page" && src) {
    addItem({
      type: "url", content: src, source: src, title: title, ts: Date.now()
    }, { force: true });
  } else if (info.menuItemId === "yb-save-image" && info.srcUrl) {
    saveImageFromUrl(info.srcUrl, src, title);
  }
});

// ---- 与面板 / 选项页通信 ----
chrome.runtime.onMessage.addListener(function (msg, _sender, sendResponse) {
  if (!msg || !msg.kind) return;
  (async function () {
    try {
      if (msg.kind === "captured") {
        await addItem(msg.item, {});
        sendResponse({ ok: true });
      } else if (msg.kind === "list") {
        var items = await loadItems();
        if (msg.q) {
          var q = String(msg.q).toLowerCase();
          items = items.filter(function (x) {
            return String(x.content || "").toLowerCase().indexOf(q) >= 0 ||
              String(x.title || "").toLowerCase().indexOf(q) >= 0 ||
              String(x.host || "").toLowerCase().indexOf(q) >= 0;
          });
        }
        sendResponse({ ok: true, items: items.slice(0, msg.limit || 100) });
      } else if (msg.kind === "add") {
        var saved = await addItem(msg.item, { force: true });
        sendResponse({ ok: !!saved, item: saved });
      } else if (msg.kind === "remove") {
        var list = await loadItems();
        list = list.filter(function (x) { return x.key !== msg.key; });
        await saveItems(list);
        sendResponse({ ok: true });
      } else if (msg.kind === "clear") {
        await saveItems([]);
        sendResponse({ ok: true });
      } else if (msg.kind === "fav") {
        var l2 = await loadItems();
        l2.forEach(function (x) { if (x.key === msg.key) x.fav = !!msg.fav; });
        await saveItems(l2);
        sendResponse({ ok: true });
      } else if (msg.kind === "bridgeHistory") {
        var data = await bridgeFetch(
          "/api/history?limit=" + (msg.limit || 50) +
          (msg.q ? "&q=" + encodeURIComponent(msg.q) : ""), {});
        setBadge("✓");
        sendResponse({ ok: true, items: data.items || [] });
      } else if (msg.kind === "bridgeCopy") {
        await bridgeFetch("/api/copy", {
          method: "POST", body: JSON.stringify({ hash: msg.hash })
        });
        sendResponse({ ok: true });
      } else if (msg.kind === "bridgePush") {
        await pushToDesktop(msg.item);
        sendResponse({ ok: true });
      } else if (msg.kind === "testBridge") {
        sendResponse(await testBridge());
      } else {
        sendResponse({ ok: false, error: "unknown kind" });
      }
    } catch (err) {
      sendResponse({
        ok: false,
        error: String((err && err.message) || err)
      });
    }
  })();
  return true;      // 异步响应
});

// 连接状态心跳：连上桌面端时角标显示 ✓
async function heartbeat() {
  var settings = await getSettings();
  if (!settings.bridgeToken) {
    setBadge("");
    return;
  }
  try {
    await bridgeFetch("/api/ping", {}, settings);
    setBadge("✓");
  } catch (e) {
    setBadge("");
  }
}

chrome.alarms.create("yb-heartbeat", { periodInMinutes: 5 });
chrome.alarms.onAlarm.addListener(function (a) {
  if (a && a.name === "yb-heartbeat") heartbeat();
});
