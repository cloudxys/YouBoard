# YouBoard 剪贴板伴侣（浏览器扩展）

配合桌面版 **YouBoard** 使用的浏览器扩展：把你在网页里复制的内容存进 YouBoard，
并在任意网页输入框里快速粘贴历史记录。只与本机 `127.0.0.1` 上的 YouBoard 通信，
不向任何服务器上传数据。

## 能做什么

- **捕获网页里的复制**：在页面上按 Ctrl+C / 剪切，扩展记下文本、链接、图片
- **一键粘贴历史**：面板里双击任意条目 → 直接插进当前网页的输入框
- **存进桌面版**：连上本机 YouBoard 后，网页里复制的内容会进桌面端历史（可被搜索、打标签、收藏）
- **从桌面端取历史**：面板第二个页签直接查 YouBoard 的全部历史（含标签/收藏），
  点一下就复制到系统剪贴板（走桌面端，所以是真正的系统剪贴板）
- **右键菜单**：存选中内容 / 存本页链接 / 存这张图片
- **按网站排除**：某些站点（比如邮箱、网银）可以设置不捕获

## 语言

扩展是**中英双语**的：文案放在 `_locales/en/`（默认）与 `_locales/zh_CN/`，
manifest 里声明了 `"default_locale": "en"`。浏览器界面语言是中文就显示中文，
其它语言自动回落到英文——包括扩展名称、简介、面板/设置页文字和右键菜单。
商店列表的中英文案与"如何在 Partner Center 加语言"的步骤见 [STORE_LISTING.md](STORE_LISTING.md)。

## 做不到什么（这是浏览器扩展的边界，桌面版才负责）

- 看不到其它程序（Office、资源管理器、微信…）的复制，只能看到浏览器页面里的
- 拿不到"复制的文件"（浏览器里没有文件剪贴板）
- 没有托盘、开机常驻、全局快捷键，不能接管 Win+V
- 默认不读系统剪贴板：只有你点「读剪贴板并保存」时才会申请一次读取权限

## 本地安装（开发 / 试用）

1. 打开 Edge → 地址栏输入 `edge://extensions/`
2. 打开左下角「开发人员模式」
3. 点「加载解压缩的扩展」→ 选中本目录（`edge_extension`）
4. 点扩展图标 → 「设置」→ 把下面那行连接信息粘进去

## 和桌面版连上（两步）

1. **桌面版**：设置 → 浏览器扩展 → 勾选「启用本机桥接」→ 点「复制连接信息」
   （形如 `127.0.0.1:8765|xxxxxxxx`，只在本机有效）
2. **扩展**：设置 → 「连接本机 YouBoard」→ 粘贴 → 「解析并保存」→ 「测试连接」

看到角标变成 ✓、状态显示「已连接桌面端」就成了。

## 上架 Microsoft Edge 加载项商店

官方事实（微软文档，2026-09 核对）：

| 项目 | 说明 |
|---|---|
| 注册费 | **免费**（原文：*There is no registration fee for submitting extensions to the Microsoft Edge program*） |
| 账号 | 用 Microsoft 账号在 **Partner Center** 注册 Edge 开发者（个人 / 公司均可） |
| 清单版本 | **必须 Manifest V3**（2022-07 起不再接受新提交的 MV2） |
| 审核 | 提交后**最长 7 个工作日** |
| 必须填写 | 单一用途说明、**每个权限的理由**、是否使用远程代码、数据使用声明、**隐私政策链接** |

### 提交步骤

1. 注册开发者账号：<https://learn.microsoft.com/en-us/microsoft-edge/extensions/publish/create-dev-account>
2. 把本目录打包成 zip（`manifest.json` 必须在压缩包**根目录**）：
   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File build_zip.ps1
   ```
   产物：`YouBoard_Companion_v1.0.1.zip`
3. Partner Center → Edge 程序 → 「新建扩展」→ 上传 zip
4. 按 [STORE_LISTING.md](STORE_LISTING.md) 里的文案逐项粘贴：
   - 单一用途说明、每个权限的理由、远程代码=否、数据使用声明
   - 隐私政策 URL：直接填
     `https://github.com/cloudxys/YouBoard/blob/main/edge_extension/privacy.md`
     （已随仓库公开，可访问）
5. 提交 → 等审核（最长 7 个工作日）→ 通过后自动上架

### 顺手多铺两个商店

同一份 zip 可以直接投：

- **Chrome 应用商店**：一次性 $5 开发者费（<https://chrome.google.com/webstore/devconsole>）
- **Firefox 附加组件**：免费（<https://addons.mozilla.org/developers/>），
  注意 Firefox 用的是 `browser.*` 命名空间，需要把 `chrome.*` 做一层兼容

## 常见被拒原因（提前避开）

- **权限理由写太笼统**：`clipboardWrite` / `activeTab` 要写清楚"用来做什么"，
  详见 STORE_LISTING.md 里已经写好的理由
- **隐私政策缺失或打不开**：URL 必须是公开可访问的
- **单一用途不明确**：本扩展的定位就是"配合 YouBoard 的剪贴板伴侣"，别顺带做别的事
- **代码混淆 / 远程加载**：本扩展所有 JS 都打包在包内，无远程代码，符合要求
- **截图不清晰**：至少 1 张 1280×800 或 640×400 的清晰截图（展示面板与设置页）

## 目录结构

```
edge_extension/
├── manifest.json         MV3 清单（权限：storage/contextMenus/clipboardWrite/activeTab/scripting/alarms + 可选 clipboardRead）
├── background.js         service worker：本地历史、右键菜单、本机桥通信
├── content.js            页面侧：捕获复制、把历史插进输入框
├── panel.html/.css/.js   点图标弹出的面板（网页捕获 / YouBoard 历史 两个页签）
├── options.html/.css/.js 设置页（连接信息、捕获开关、忽略站点、清空）
├── icons/                16/32/48/128 图标
├── privacy.md            隐私政策（上架要填的 URL）
├── STORE_LISTING.md      商店文案 + 权限理由（可直接粘贴）
└── build_zip.ps1         打包成上架用的 zip
```

## 版本

- 扩展：1.0.1（1.0.1：中英双语 + 修复桌面端历史时间显示错误 + 简介长度符合商店 132 字符限制）
- 需要桌面版 YouBoard **3.3.0 或更高**（本机桥从 3.3.0 开始提供）
