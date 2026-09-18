/* YouBoard 剪贴板伴侣 —— 页面侧脚本
 *
 * 只做两件事：
 *   1) 监听网页里的复制 / 剪切，把用户主动复制的内容交给后台脚本；
 *   2) 收到后台发来的文字时，插入到当前聚焦的输入框（"一键粘贴历史"）。
 *
 * 注意：这里读的是页面内的 copy 事件（用户自己按的 Ctrl+C），
 * 不去读系统剪贴板，也不碰其它程序。
 */
(function () {
  "use strict";

  var MAX_TEXT = 200000;        // 单条最多送这么多字符，超长截断
  var MAX_IMAGE_BYTES = 8 * 1024 * 1024;

  function hashOf(text) {
    var h = 5381;
    for (var i = 0; i < text.length; i++) {
      h = ((h << 5) + h + text.charCodeAt(i)) | 0;
    }
    return "h" + (h >>> 0).toString(16);
  }

  function looksLikeUrl(text) {
    return /^https?:\/\/\S+$/i.test((text || "").trim());
  }

  function send(item) {
    try {
      chrome.runtime.sendMessage({ kind: "captured", item: item });
    } catch (e) {
      /* 扩展被禁用 / 上下文失效：忽略 */
    }
  }

  function handleCopy(e) {
    try {
      var dt = e.clipboardData;
      if (!dt) return;
      var html = dt.getData("text/html") || "";
      var text = dt.getData("text/plain") || "";
      var imageItem = null;
      if (dt.items) {
        for (var i = 0; i < dt.items.length; i++) {
          if (dt.items[i].type && dt.items[i].type.indexOf("image/") === 0) {
            imageItem = dt.items[i];
            break;
          }
        }
      }

      if (imageItem) {
        var file = imageItem.getAsFile();
        if (file && file.size <= MAX_IMAGE_BYTES) {
          var reader = new FileReader();
          reader.onload = function () {
            send({
              type: "image",
              content: String(reader.result || ""),
              mime: file.type || "image/png",
              source: location.href,
              title: document.title || "",
              ts: Date.now()
            });
          };
          reader.readAsDataURL(file);
          return;
        }
      }

      text = (text || "").replace(/\u0000/g, "");
      if (!text.trim() && !html) return;
      if (text.length > MAX_TEXT) text = text.slice(0, MAX_TEXT);
      send({
        type: looksLikeUrl(text) ? "url" : "text",
        content: text,
        html: html.slice(0, MAX_TEXT),
        source: location.href,
        title: document.title || "",
        hash: hashOf(text),
        ts: Date.now()
      });
    } catch (err) {
      /* 复制事件处理失败不该影响页面 */
    }
  }

  document.addEventListener("copy", handleCopy, true);
  document.addEventListener("cut", handleCopy, true);

  // ---- 反向：把历史记录插进当前输入框 ----
  function insertText(text) {
    var el = document.activeElement;
    if (!el) return false;
    var tag = (el.tagName || "").toLowerCase();
    var editable = el.isContentEditable ||
      tag === "textarea" ||
      (tag === "input" && !/^(checkbox|radio|file|submit|button)$/i.test(el.type || "text"));
    if (!editable) return false;
    try {
      el.focus();
      if (el.isContentEditable) {
        document.execCommand("insertText", false, text);
      } else {
        var start = el.selectionStart == null ? el.value.length : el.selectionStart;
        var end = el.selectionEnd == null ? el.value.length : el.selectionEnd;
        var next = el.value.slice(0, start) + text + el.value.slice(end);
        el.value = next;
        var caret = start + text.length;
        if (el.setSelectionRange) el.setSelectionRange(caret, caret);
        el.dispatchEvent(new Event("input", { bubbles: true }));
      }
      return true;
    } catch (err) {
      return false;
    }
  }

  chrome.runtime.onMessage.addListener(function (msg, _sender, sendResponse) {
    if (!msg) return;
    if (msg.kind === "insert") {
      sendResponse({ ok: insertText(String(msg.text || "")) });
      return true;
    }
    if (msg.kind === "ping") {
      sendResponse({ ok: true, url: location.href });
      return true;
    }
  });
})();
