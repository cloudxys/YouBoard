# YouBoard 剪贴板伴侣 · 隐私政策 / Privacy Policy

最后更新：2026-09-18

## 简体中文

**一句话**：这个扩展不向任何服务器发送你的数据。

### 我们处理哪些数据

1. **你在网页里主动复制的内容**（文本、链接、图片）。
   仅在你在扩展设置里保持"捕获网页里的复制"开启时才会记录。
2. **该内容所属的网页地址与标题**，用于在面板里显示来源。
3. **你的扩展设置**（本机地址、令牌、是否捕获、忽略的网站列表）。

### 数据存在哪里

- 上述内容默认只保存在**你自己的浏览器本地存储**（`chrome.storage.local`）里；
- 如果你在设置里打开了"同时写进本机 YouBoard"，内容会通过
  `http://127.0.0.1` 发送给**你这台电脑上运行的 YouBoard 桌面版**，
  由桌面版按它自己的加密方式保存在本机。

### 我们不做的事

- 不向任何远程服务器上传、不接入统计 / 广告 / 崩溃上报 SDK；
- 不读取你没有主动复制的内容；
- 不读取系统剪贴板，除非你点击"读剪贴板并保存"并在弹出的授权提示里同意
  （该权限 `clipboardRead` 是可选的，不点就不会申请）；
- 不收集账号、位置、设备标识等个人信息。

### 权限说明

| 权限 | 用途 |
|---|---|
| `storage` | 把捕获内容和设置存在浏览器本地 |
| `contextMenus` | 提供右键菜单"存到 YouBoard" |
| `clipboardWrite` | 把历史条目写进剪贴板 / 插入网页输入框 |
| `activeTab`、`scripting` | 把选中的历史直接插入当前网页的输入框 |
| `alarms` | 定时检查与桌面版的连接状态（只在本地） |
| `clipboardRead`（可选） | 仅在你点"读剪贴板并保存"时申请 |
| `http://127.0.0.1/*` | 与本机桌面版 YouBoard 通信 |

### 如何删除数据

扩展设置页 →「清空浏览器本地数据」；或直接在 Edge 的扩展管理里移除本扩展。
桌面端的历史请在 YouBoard 桌面版里删除。

### 联系方式

问题反馈：<https://github.com/cloudxys/YouBoard/issues>

## English

**In one line**: this extension never sends your data to any server.

**What we handle**: content you actively copy on web pages (text, links, images),
the page URL/title it came from, and your extension settings. Everything is stored
locally in `chrome.storage.local`; if you enable "also write into YouBoard", it is
sent to the YouBoard desktop app running on **your own machine** via
`http://127.0.0.1`.

**What we never do**: no remote servers, no analytics/ads/crash SDKs, no reading
content you did not copy, no reading the system clipboard unless you click
"Read clipboard and save" and grant the optional `clipboardRead` permission.

**Permissions**: `storage` (local history/settings), `contextMenus` (right-click
save), `clipboardWrite` (copy/paste history), `activeTab` + `scripting` (insert
history into the page), `alarms` (local connection heartbeat),
`clipboardRead` (optional, on demand), `http://127.0.0.1/*` (talk to the local
desktop app).

**Deleting data**: clear it in the extension's options page, or remove the
extension. Desktop history is managed inside the YouBoard desktop app.

Contact: <https://github.com/cloudxys/YouBoard/issues>
