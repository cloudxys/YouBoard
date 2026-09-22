#!/usr/bin/env node
/*
 * YouBoard 浏览器扩展的行为测试（不依赖浏览器：用 Node 起一个假的 chrome API）。
 *
 * 覆盖：捕获 / 去重 / 条数上限 / 站点排除 / 查询 / 删除 / 收藏 / 清空、
 *       与桌面端本机桥的推拉（含令牌头）、右键菜单的中英文案、
 *       面板的时间格式化与徽标文案。
 *
 * 用法： node tools/verify_extension.js
 * 输出： 每行 "PASS 名称" / "FAIL 名称 详情"，失败时退出码为 1（CI 门禁用）。
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const EXT = path.join(__dirname, "..", "edge_extension");
const FAILED = [];

function check(name, cond, extra) {
  const line = (cond ? "PASS " : "FAIL ") + name + (extra ? "  " + extra : "");
  console.log(line);
  if (!cond) FAILED.push(name);
}

function readLocale(loc) {
  return JSON.parse(
    fs.readFileSync(path.join(EXT, "_locales", loc, "messages.json"), "utf8"));
}

/* ---------- 假的 chrome API ---------- */
function makeChrome(locale) {
  const msgs = readLocale(locale);
  const store = {};
  const calls = { menus: [], fetch: [], badge: [], opened: 0 };
  const listeners = { message: [], installed: [], startup: [], alarm: [],
                      menuClick: [] };
  const chrome = {
    i18n: {
      getUILanguage: () => (locale === "zh_CN" ? "zh-CN" : "en-US"),
      getMessage: (key, subs) => {
        const item = msgs[key];
        if (!item) return "";
        let msg = item.message;
        const ph = item.placeholders || {};
        Object.keys(ph).forEach((name) => {
          const spec = String(ph[name].content || "$1");
          const idx = parseInt(spec.replace("$", ""), 10) - 1;
          const val = (subs && subs[idx] !== undefined) ? String(subs[idx]) : "";
          msg = msg.split("$" + name.toUpperCase() + "$").join(val);
        });
        return msg;
      }
    },
    storage: {
      local: {
        get(defaults, cb) {
          const out = {};
          Object.keys(defaults || {}).forEach((k) => {
            out[k] = Object.prototype.hasOwnProperty.call(store, k)
              ? store[k] : defaults[k];
          });
          if (typeof defaults === "function") defaults(out);
          else if (cb) cb(out);
        },
        set(obj, cb) {
          Object.assign(store, obj);
          if (cb) cb();
        }
      }
    },
    action: {
      setBadgeText: (o) => calls.badge.push(o.text || ""),
      setBadgeBackgroundColor: () => {}
    },
    contextMenus: {
      removeAll: (cb) => { calls.menus.length = 0; if (cb) cb(); },
      create: (o) => calls.menus.push(o),
      onClicked: { addListener: (fn) => listeners.menuClick.push(fn) }
    },
    runtime: {
      onInstalled: { addListener: (fn) => listeners.installed.push(fn) },
      onStartup: { addListener: (fn) => listeners.startup.push(fn) },
      onMessage: { addListener: (fn) => listeners.message.push(fn) },
      sendMessage: (msg, cb) => {
        if (cb) cb({ ok: false, items: [] });
        return Promise.resolve({ ok: false, items: [] });
      },
      openOptionsPage: () => { calls.opened += 1; },
      lastError: null
    },
    alarms: {
      create: () => {},
      onAlarm: { addListener: (fn) => listeners.alarm.push(fn) }
    },
    tabs: {
      query: async () => [{ id: 1 }],
      sendMessage: (id, msg, cb) => { if (cb) cb({ ok: true }); }
    },
    permissions: {
      contains: async () => true,
      request: async () => true
    }
  };
  return { chrome, store, calls, listeners, msgs };
}

function makeFetch(handler) {
  return async function (url, opts) {
    const rec = { url: String(url), opts: opts || {} };
    handler.calls.push(rec);
    const reply = handler.reply ? handler.reply(rec) : { ok: true, data: {} };
    return {
      ok: reply.ok !== false,
      status: reply.status || 200,
      json: async () => reply.data || {}
    };
  };
}

/* ---------- 加载 background.js ---------- */
function loadBackground(locale) {
  const env = makeChrome(locale);
  const fetchHandler = { calls: [], reply: () => ({ ok: true, data: { ok: true } }) };
  const sandbox = {
    chrome: env.chrome,
    fetch: makeFetch(fetchHandler),
    console,
    setTimeout,
    clearTimeout,
    URL,
    Date,
    Promise,
    Object,
    Array,
    String,
    Number,
    Math,
    JSON,
    isFinite,
    parseInt,
    encodeURIComponent,
    FileReader: function () {}
  };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(path.join(EXT, "background.js"), "utf8"),
                  sandbox, { filename: "background.js" });
  return { sandbox, env, fetchHandler };
}

function send(sandbox, msg) {
  return new Promise((resolve) => {
    const done = (resp) => resolve(resp);
    sandbox.chrome.runtime.onMessage._fake = true;
    const listener = sandbox.__listeners_message;
    listener(msg, {}, done);
  });
}

async function testBackground() {
  const { sandbox, env, fetchHandler } = loadBackground("zh_CN");
  sandbox.__listeners_message = null;
  // 取出注册过的 onMessage 监听器（fake chrome 里存着）
  const listeners = [];
  sandbox.chrome.runtime.onMessage = { addListener: (fn) => listeners.push(fn) };
  vm.runInContext(fs.readFileSync(path.join(EXT, "background.js"), "utf8"),
                  sandbox, { filename: "background.js" });
  const onMessage = (msg) => new Promise((resolve) => {
    listeners.forEach((fn) => fn(msg, {}, resolve));
  });

  await onMessage({ kind: "captured", item: { type: "text", content: "第一条",
    source: "https://a.example/x", title: "A" } });
  await onMessage({ kind: "captured", item: { type: "text", content: "第二条",
    source: "https://b.example/y", title: "B" } });
  await onMessage({ kind: "captured", item: { type: "text", content: "第一条",
    source: "https://a.example/x", title: "A" } });
  let list = await onMessage({ kind: "list", limit: 50 });
  check("ext: dedupes and keeps newest first",
        list.items.length === 2 && list.items[0].content === "第一条",
        JSON.stringify(list.items.map((i) => i.content)));
  check("ext: stores host for source",
        list.items[0].host === "a.example", String(list.items[0].host));
  const q = await onMessage({ kind: "list", q: "b.example" });
  check("ext: search by host", q.items.length === 1, JSON.stringify(q.items.length));
  const added = await onMessage({ kind: "add", item: { type: "text", content: "直接加的" } });
  check("ext: add returns saved item",
        added.ok && added.item && added.item.content === "直接加的");
  const fav = await onMessage({ kind: "fav", key: added.item.key, fav: true });
  const favList = await onMessage({ kind: "list" });
  check("ext: fav flags the item",
        fav.ok && favList.items.some((i) => i.key === added.item.key && i.fav));
  const removed = await onMessage({ kind: "remove", key: added.item.key });
  const afterRemove = await onMessage({ kind: "list" });
  check("ext: remove drops the item",
        removed.ok && !afterRemove.items.some((i) => i.key === added.item.key));
  await onMessage({ kind: "clear" });
  const cleared = await onMessage({ kind: "list" });
  check("ext: clear empties everything", cleared.items.length === 0);

  // 站点排除 + 关闭捕获
  env.store.ignoreHosts = "mail.example.com";
  await onMessage({ kind: "captured", item: { type: "text", content: "x",
    source: "https://mail.example.com/inbox" } });
  const ignored = await onMessage({ kind: "list" });
  check("ext: ignore list blocks the site", ignored.items.length === 0);
  env.store.ignoreHosts = "";
  env.store.captureEnabled = false;
  await onMessage({ kind: "captured", item: { type: "text", content: "y" } });
  const offList = await onMessage({ kind: "list" });
  check("ext: capture off blocks capture", offList.items.length === 0);
  env.store.captureEnabled = true;

  // 条数上限
  env.store.maxItems = 20;
  for (let i = 0; i < 25; i++) {
    await onMessage({ kind: "captured", item: { type: "text", content: "n" + i } });
  }
  const capped = await onMessage({ kind: "list", limit: 100 });
  check("ext: maxItems trims the list", capped.items.length === 20,
        String(capped.items.length));
  await onMessage({ kind: "clear" });

  // 本机桥：推送 / 历史 / 复制 / 连接测试
  env.store.bridgeToken = "tok-123";
  env.store.bridgeUrl = "http://127.0.0.1:8765";
  fetchHandler.calls.length = 0;
  await onMessage({ kind: "captured", item: { type: "text", content: "推到桌面",
    source: "https://c.example/z" } });
  await new Promise((r) => setTimeout(r, 30));
  const pushCall = fetchHandler.calls.find((c) => c.url.indexOf("/api/items") >= 0);
  check("ext: pushes new capture to the desktop bridge",
        !!pushCall && pushCall.opts.method === "POST",
        pushCall ? pushCall.url : "no call");
  check("ext: bridge calls carry the token header",
        !!pushCall && pushCall.opts.headers &&
        pushCall.opts.headers["X-YouBoard-Token"] === "tok-123");
  const body = pushCall ? JSON.parse(pushCall.opts.body) : {};
  check("ext: push body keeps type/content/source",
        body.content === "推到桌面" && body.type === "text" &&
        body.source === "https://c.example/z", JSON.stringify(body));

  env.store.syncToDesktop = false;
  fetchHandler.calls.length = 0;
  await onMessage({ kind: "captured", item: { type: "text", content: "不该推" } });
  await new Promise((r) => setTimeout(r, 30));
  check("ext: sync off means no push",
        !fetchHandler.calls.some((c) => c.url.indexOf("/api/items") >= 0));
  env.store.syncToDesktop = true;

  fetchHandler.reply = () => ({ ok: true, data: { ok: true, items: [
    { hash: "h1", type: "text", content: "桌面历史", ts: "2026-09-22T15:20:41" }] } });
  const hist = await onMessage({ kind: "bridgeHistory", limit: 30, q: "桌面" });
  check("ext: bridge history returns items",
        hist.ok && hist.items.length === 1 && hist.items[0].hash === "h1");
  const histCall = fetchHandler.calls.find((c) => c.url.indexOf("/api/history") >= 0);
  check("ext: history query keeps limit + q",
        !!histCall && histCall.url.indexOf("limit=30") >= 0 &&
        histCall.url.indexOf("q=") >= 0, histCall ? histCall.url : "no call");

  fetchHandler.reply = () => ({ ok: true, data: { ok: true } });
  const copied = await onMessage({ kind: "bridgeCopy", hash: "h1" });
  const copyCall = fetchHandler.calls.find((c) => c.url.indexOf("/api/copy") >= 0);
  check("ext: bridge copy posts the hash",
        copied.ok && !!copyCall &&
        JSON.parse(copyCall.opts.body).hash === "h1");

  fetchHandler.reply = () => ({ ok: false, status: 401, data: { ok: false } });
  const bad = await onMessage({ kind: "testBridge" });
  check("ext: test connection reports failure",
        bad.ok === false && /401/.test(String(bad.error)), JSON.stringify(bad));
  fetchHandler.reply = () => ({ ok: true, data: { ok: true } });
  const good = await onMessage({ kind: "testBridge" });
  check("ext: test connection reports success", good.ok === true);
  check("ext: badge shows ✓ when connected",
        env.calls.badge.indexOf("✓") >= 0, JSON.stringify(env.calls.badge));

  const unknown = await onMessage({ kind: "nope" });
  check("ext: unknown message answered with error",
        unknown.ok === false && !!unknown.error);
}

/* ---------- 右键菜单的中英文案 ---------- */
async function testMenus() {
  for (const loc of ["zh_CN", "en"]) {
    const { sandbox, env, listeners } = loadBackground(loc);
    const installed = [];
    sandbox.chrome.runtime.onInstalled = { addListener: (fn) => installed.push(fn) };
    vm.runInContext(fs.readFileSync(path.join(EXT, "background.js"), "utf8"),
                    sandbox, { filename: "background.js" });
    installed.forEach((fn) => fn({ reason: "install" }));
    await new Promise((r) => setTimeout(r, 10));
    const titles = env.calls.menus.map((m) => m.title);
    const expected = loc === "zh_CN"
      ? ["存到 YouBoard（选中内容）", "存到 YouBoard（本页链接）", "存到 YouBoard（这张图片）"]
      : ["Save to YouBoard (selection)", "Save to YouBoard (page link)",
         "Save to YouBoard (this image)"];
    check("ext: context menus localized (" + loc + ")",
          JSON.stringify(titles) === JSON.stringify(expected), JSON.stringify(titles));
    check("ext: three context menu items (" + loc + ")",
          env.calls.menus.length === 3 &&
          env.calls.menus.every((m) => m.contexts && m.contexts.length === 1));
  }
}

/* ---------- 面板：时间格式 + 徽标文案 ---------- */
function loadPanel(locale) {
  const env = makeChrome(locale);
  const nodes = {};
  function fakeNode(tag) {
    const node = {
      tagName: tag, children: [], style: {}, dataset: {},
      textContent: "", className: "", value: "", checked: false,
      classList: { add() {}, remove() {} },
      setAttribute() {}, getAttribute() { return null; },
      appendChild(child) { node.children.push(child); return child; },
      addEventListener() {},
      querySelectorAll() { return []; }
    };
    return node;
  }
  const ids = ["status", "q", "tab-local", "tab-desktop", "n-local", "list",
               "empty", "btn-read", "btn-clear", "btn-options"];
  ids.forEach((id) => { nodes[id] = fakeNode("div"); });
  const document = {
    documentElement: { lang: "" },
    getElementById: (id) => nodes[id] || (nodes[id] = fakeNode("div")),
    createElement: (tag) => fakeNode(tag),
    querySelectorAll: () => [],
    activeElement: null,
    addEventListener() {}
  };
  const sandbox = {
    chrome: env.chrome,
    document,
    console,
    navigator: { clipboard: { writeText: async () => {}, write: async () => {} } },
    ClipboardItem: function () {},
    Date, Promise, Object, Array, String, Number, JSON, isFinite, parseInt,
    setTimeout, clearTimeout,
    fetch: async () => ({ blob: async () => ({ type: "image/png" }) })
  };
  sandbox.globalThis = sandbox;
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(path.join(EXT, "panel.js"), "utf8"),
                  sandbox, { filename: "panel.js" });
  return { sandbox, env, nodes };
}

function testPanel() {
  const zh = loadPanel("zh_CN");
  const iso = "2026-09-22T15:20:41";
  const local = new Date(iso);
  const want = ("0" + (local.getMonth() + 1)).slice(-2) + "-" +
    ("0" + local.getDate()).slice(-2) + " " +
    ("0" + local.getHours()).slice(-2) + ":" +
    ("0" + local.getMinutes()).slice(-2);
  check("panel: iso timestamp formatted (not 'now')",
        zh.sandbox.fmtTime(iso) === want,
        zh.sandbox.fmtTime(iso) + " want " + want);
  const ms = Date.now() - 3600 * 1000;
  const msWant = (function () {
    const d = new Date(ms);
    return ("0" + (d.getMonth() + 1)).slice(-2) + "-" +
      ("0" + d.getDate()).slice(-2) + " " +
      ("0" + d.getHours()).slice(-2) + ":" +
      ("0" + d.getMinutes()).slice(-2);
  })();
  check("panel: numeric timestamp still works",
        zh.sandbox.fmtTime(ms) === msWant, zh.sandbox.fmtTime(ms));
  check("panel: garbage timestamp falls back to now",
        /^\d\d-\d\d \d\d:\d\d$/.test(zh.sandbox.fmtTime("oops")));

  const zhNode = zh.sandbox.itemNode(
    { hash: "h", type: "url", content: "https://a.example", ts: iso, fav: false },
    true);
  const badge = zhNode.children[0].children[0].textContent;
  const stamp = zhNode.children[0].children[1].textContent;
  check("panel: desktop badge localized (zh)", badge === "网址", badge);
  check("panel: desktop row shows the real time", stamp === want, stamp);

  const en = loadPanel("en");
  const enNode = en.sandbox.itemNode(
    { hash: "h", type: "image", content: "图片", ts: iso, thumb: "data:," }, true);
  check("panel: desktop badge localized (en)",
        enNode.children[0].children[0].textContent === "Image",
        enNode.children[0].children[0].textContent);
}

/* ---------- 入口 ---------- */
(async function main() {
  await testBackground();
  await testMenus();
  testPanel();
  if (FAILED.length) {
    console.log("RESULT=FAILED:" + FAILED.join(","));
    process.exit(1);
  }
  console.log("RESULT=ALL_PASS");
})().catch((err) => {
  console.log("FAIL extension harness crashed: " + err.stack);
  console.log("RESULT=FAILED:harness");
  process.exit(1);
});
