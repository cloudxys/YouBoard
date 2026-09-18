/* YouBoard 剪贴板伴侣 —— 选项页 */
"use strict";

var DEFAULTS = {
  bridgeUrl: "http://127.0.0.1:8765",
  bridgeToken: "",
  captureEnabled: true,
  syncToDesktop: true,
  maxItems: 200,
  ignoreHosts: ""
};

function $(id) { return document.getElementById(id); }

function load() {
  chrome.storage.local.get(DEFAULTS, function (got) {
    var v = Object.assign({}, DEFAULTS, got || {});
    $("url").value = v.bridgeUrl || "";
    $("token").value = v.bridgeToken || "";
    $("capture").checked = v.captureEnabled !== false;
    $("sync").checked = v.syncToDesktop !== false;
    $("max").value = v.maxItems || 200;
    $("ignore").value = v.ignoreHosts || "";
    refreshStat();
  });
}

function save() {
  chrome.storage.local.set({
    bridgeUrl: $("url").value.trim() || DEFAULTS.bridgeUrl,
    bridgeToken: $("token").value.trim(),
    captureEnabled: $("capture").checked,
    syncToDesktop: $("sync").checked,
    maxItems: parseInt($("max").value, 10) || 200,
    ignoreHosts: $("ignore").value
  }, function () {
    setResult("已保存", "ok");
  });
}

function setResult(text, cls) {
  var el = $("test-result");
  el.textContent = text;
  el.className = "result" + (cls ? " " + cls : "");
}

function refreshStat() {
  chrome.storage.local.get({ yb_items: [] }, function (got) {
    var n = (got.yb_items || []).length;
    $("stat").textContent = "已存 " + n + " 条";
  });
}

function send(msg) {
  return new Promise(function (resolve) {
    chrome.runtime.sendMessage(msg, function (r) { resolve(r || { ok: false }); });
  });
}

// "127.0.0.1:8765|token" 或 "http://127.0.0.1:8765|token" 都能解析
function parseConn(line) {
  var raw = String(line || "").trim();
  if (!raw) return null;
  var parts = raw.split("|");
  var hostPart = parts[0].trim().replace(/^https?:\/\//, "").replace(/\/+$/, "");
  var token = (parts[1] || "").trim();
  var url = /^https?:\/\//.test(hostPart) ? hostPart : ("http://" + hostPart);
  return { url: url, token: token };
}

$("btn-apply").addEventListener("click", function () {
  var parsed = parseConn($("conn").value);
  if (!parsed) {
    setResult("这行连接信息没看懂", "bad");
    return;
  }
  $("url").value = parsed.url;
  $("token").value = parsed.token;
  save();
});

$("btn-test").addEventListener("click", async function () {
  save();
  setResult("测试中…");
  var r = await send({ kind: "testBridge" });
  if (r && r.ok) {
    var info = r.info || {};
    setResult("连接成功：YouBoard v" + (info.version || "?") +
      "（本机）", "ok");
  } else {
    setResult("连不上：" + ((r && r.error) || "请确认桌面版在运行、令牌一致"), "bad");
  }
});

$("btn-clear").addEventListener("click", async function () {
  await send({ kind: "clear" });
  refreshStat();
  setResult("已清空", "ok");
});

["capture", "sync", "max", "ignore"].forEach(function (id) {
  $(id).addEventListener("change", save);
});

load();
