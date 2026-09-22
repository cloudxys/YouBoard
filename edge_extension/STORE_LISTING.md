# 商店上架文案（Partner Center 直接粘贴）

> 用法：Partner Center 里每一项都能在这个文件里找到对应内容，复制粘贴即可。
> 带 `[需替换]` 的地方按自己的信息改。

## 多语言（扩展界面 + 商店列表）

**扩展界面已经是双语的**：文案放在 `_locales/en/`（默认）和 `_locales/zh_CN/`，
manifest 里 `"default_locale": "en"`。浏览器界面是中文就显示中文，其它语言自动回落英文——
重新打包上传的 zip 里已经带好中文，不需要再额外设置。

**商店列表**的多语言要在 Partner Center 里加（微软官方文档里叫「Add or remove a language」）：

1. 打开扩展 → 左侧 **Store listing**（商店列表）；
2. 点页面上方的 **Add or remove a language** → 勾选 **Chinese (Simplified) / 简体中文**；
3. 切到中文那一栏，把下面「中文文案」里的名称 / 简短描述 / 详细描述 / 搜索关键词粘进去；
4. 截图可以在中文页用 **Duplicate asset from another language**（从另一种语言复制）拿过去，或单独传；
5. 保存并提交 —— 商店会按访问者所在区域的语言显示对应文案（中文区显示中文，其它显示英文）。

### 中文文案（简体中文那一栏）

名称：`YouBoard 剪贴板伴侣`

简短描述（≤132 字）：

```
把网页里复制的内容存进本机 YouBoard，并在任意输入框快速粘贴历史记录。只连本机，不联网上传。
```

详细描述：

```
YouBoard 剪贴板伴侣 —— 桌面版 YouBoard 的浏览器搭档。

这个扩展做两件事：
1) 记住你在网页里复制的内容（文本 / 链接 / 图片），并按网站标好来源；
2) 需要的时候，把历史记录一键插回当前网页的输入框里。

连上本机桌面版 YouBoard 后，网页里复制的内容会直接进桌面端历史——和你在其它程序里
复制的东西放在一起，可以搜索、打标签、收藏，也能在桌面端的小组件里看到。

• 完全本地：只与本机 127.0.0.1 上的 YouBoard 通信，不向任何服务器发送数据
• 不需要账号：连接信息由桌面版生成，一行粘贴即可
• 可排除站点：不想被记录的网站可以直接加到忽略列表
```

搜索关键词：`剪贴板, clipboard, 剪贴板历史, YouBoard, 效率工具, 复制粘贴, local clipboard`

### 英文文案（English 那一栏）

名称：`YouBoard Clipboard Companion`

Short description (≤132 chars):

```
Save what you copy on web pages into your local YouBoard and paste clipboard history anywhere. Localhost only.
```

Detailed description:

```
The YouBoard Clipboard Companion is the browser half of the YouBoard desktop clipboard manager.

It does two things:
1) It remembers what you copy on web pages (text / links / images) and tags each item with its source site.
2) It pastes your history straight back into the input field you are typing in.

When connected to the desktop app on the same machine, web copies land in your YouBoard history
next to everything else you copy — searchable, taggable, favouritable, and visible in the desktop widget.

• Fully local: talks only to YouBoard on 127.0.0.1, never to a remote server
• No account needed: the desktop app generates a one-line connection string
• Per-site ignore list for anything you would rather not record
```

Search terms: `clipboard, clipboard history, youboard, productivity, copy paste, local clipboard, web capture`

## 基本信息

| 字段 | 填写内容 |
|---|---|
| 名称（Name） | `YouBoard 剪贴板伴侣` |
| 英文名（备用） | `YouBoard Clipboard Companion` |
| 类别（Category） | `Productivity`（效率） |
| 默认语言 | 简体中文（可再加 English） |
| 版本 | `1.0.0` |
| 支持站点 | `https://github.com/cloudxys/YouBoard` |
| 隐私政策 URL | `https://github.com/cloudxys/YouBoard/blob/main/edge_extension/privacy.md` （已验证可公开访问） |
| 支持邮箱 / 反馈 | `[需替换：你的邮箱或 issues 链接]` |

## 简短描述（≤ 132 字符）

```
把网页里复制的内容存进本机 YouBoard，并在任意输入框快速粘贴历史记录。只连本机，不联网上传。
```

```
Save what you copy on web pages into your local YouBoard and paste clipboard history anywhere. Localhost only.
```

## 详细描述

```
YouBoard 剪贴板伴侣 —— 桌面版 YouBoard 的浏览器搭档。

平时用浏览器查资料、写东西时，复制过的东西往往散落在各个标签页里。这个扩展做两件事：

1) 记住你在网页里复制的内容（文本 / 链接 / 图片），并按网站标好来源；
2) 需要的时候，把历史记录一键插回当前网页的输入框里。

连上本机桌面版 YouBoard 后，网页里复制的内容会直接进桌面端历史——和你在其它程序里
复制的东西放在一起，可以搜索、打标签、收藏，也能在桌面端的小组件里看到。

· 面板两个页签：网页捕获（只存浏览器本地）/ YouBoard 历史（从桌面端读，可搜索）
· 单击条目 = 复制；双击 = 插进当前输入框
· 右键菜单：存选中内容 / 存本页链接 / 存这张图片
· 可以按网站设置不捕获（邮箱、网银这类站点建议加进去）
· 完全本地：只与本机 127.0.0.1 上的 YouBoard 通信，不向任何服务器发送数据

需要桌面版 YouBoard 3.3.0 或更高版本（Windows / macOS 均可）。
桌面版下载：https://github.com/cloudxys/YouBoard/releases
```

```
YouBoard Clipboard Companion is the browser partner of the YouBoard desktop app.

It remembers what you copy on web pages (text, links, images) with the source site,
lets you paste that history straight into any input field, and — when connected to
the desktop app on your own machine — keeps those copies alongside everything else
you copy, searchable, taggable and favouritable in YouBoard.

Two tabs: Page captures (stored in the browser) and YouBoard history (read from the
desktop app, searchable). Single click copies, double click inserts into the page.
Right-click to save a selection, the page link, or an image. Per-site ignore list.

Fully local: it only talks to 127.0.0.1 — nothing is uploaded anywhere.
Requires YouBoard desktop 3.3.0+ (Windows / macOS).
```

## 单一用途说明（Single Purpose Description）

```
本扩展只有一个用途：把用户在本机浏览器网页里主动复制的剪贴板内容，保存到本机的
YouBoard 剪贴板管理器，并把历史记录插回网页输入框。它不修改浏览器行为、不注入广告、
不做与剪贴板无关的事情。
```

## 权限理由（Permission Justification，逐条粘贴）

| 权限 | 理由（英文，Partner Center 里建议英文填写） |
|---|---|
| `storage` | Stores the user's captured clipboard items and extension settings locally in the browser. |
| `contextMenus` | Adds a right-click menu so the user can explicitly save a selection, the current page URL, or an image into YouBoard. |
| `clipboardWrite` | Writes the history item the user picked into the clipboard / inserts it into the focused input field. |
| `activeTab` | Needed to insert the chosen history item into the currently active tab after the user clicks in the extension panel. |
| `scripting` | Injects the small content script that inserts the selected text into the page's input field. |
| `alarms` | Periodically checks whether the local YouBoard desktop app is reachable, to show connection status. Local only. |
| `clipboardRead` (optional) | Only requested when the user clicks "Read clipboard and save" — used to read the current clipboard text so the user can store it. |
| host `http://127.0.0.1/*` | Communicates with the YouBoard desktop app running on the same machine (localhost only) to push captures and read history. |

## 远程代码声明

```
Are you using remote code?  → No
```

（所有 JS 都打包在扩展内，没有任何远程加载或 eval；可在提交表单里如实勾选。）

## 数据使用声明（Data usage）

```
- 不收集个人身份信息
- 不收集健康、金融、认证、位置、浏览历史等数据
- 捕获的内容仅在用户开启时保存到浏览器本地，以及用户本机的 YouBoard 桌面版
- 不向第三方传输任何数据
- 不出售数据
```

（对应表单里：只勾选「存储用户在本扩展内产生的数据」，其余全部选 No。）

## 搜索关键词（最多 7 个）

```
剪贴板, clipboard, 剪贴板历史, YouBoard, 效率工具, 复制粘贴, local clipboard
```

## 截图清单（上架时需要至少 1 张，建议 3 张 1280×800）

1. 面板「网页捕获」页签：几条来自不同网站的记录 + 连接状态 ✓
2. 面板「YouBoard 历史」页签：从桌面端读到的历史（含标签/收藏）
3. 设置页：连接信息已填、测试连接成功
4. 桌面端「设置 → 浏览器扩展」卡片：开关打开、复制连接信息（可选，用于说明配合关系）

## 提交后

- 审核最长 7 个工作日；被拒可在申诉 / 修改后重新提交
- 通过后链接形如：`https://microsoftedge.microsoft.com/addons/detail/<id>`
- 拿到链接后记得：写进桌面版 README 的「浏览器扩展」一节，再更新一次 Release 说明
