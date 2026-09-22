/* YouBoard 剪贴板伴侣 —— 选项页 */
"use strict";

/* ---- 多语言（与 panel.js 同一套做法） ---- */
function t(key, subs) {
  try {
    var msg = chrome.i18n.getMessage(key, subs);
    return msg || key;
  } catch (e) {
    return key;
  }
}

function applyI18n() {
  document.querySelectorAll("[data-i18n]").forEach(function (el) {
    el.textContent = t(el.getAttribute("data-i18n"));
  });
  document.querySelectorAll("[data-i18n-placeholder]").forEach(function (el) {
    el.setAttribute("placeholder", t(el.getAttribute("data-i18n-placeholder")));
  });
  try {
    document.documentElement.lang = chrome.i18n.getUILanguage() || "en";
  } catch (e) {
    /* 忽略 */
  }
}

applyI18n();

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
    setResult(t("optSaved"), "ok");
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
    $("stat").textContent = t("optStat", [String(n)]);
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
    setResult(t("optParseFailed"), "bad");
    return;
  }
  $("url").value = parsed.url;
  $("token").value = parsed.token;
  save();
});

$("btn-test").addEventListener("click", async function () {
  save();
  setResult(t("optTesting"));
  var r = await send({ kind: "testBridge" });
  if (r && r.ok) {
    var info = r.info || {};
    setResult(t("optConnected", [String(info.version || "?")]), "ok");
  } else {
    setResult(t("optConnectFailed",
                [String((r && r.error) || t("optConnectFailedHint"))]), "bad");
  }
});

$("btn-clear").addEventListener("click", async function () {
  await send({ kind: "clear" });
  refreshStat();
  setResult(t("optCleared"), "ok");
});

["capture", "sync", "max", "ignore"].forEach(function (id) {
  $(id).addEventListener("change", save);
});

load();
