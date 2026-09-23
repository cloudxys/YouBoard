# 我做了个剪贴板工具，它和 Ditto / CopyQ / ClipClip / Paste / Raycast 比怎么样？

> 作者：YouBoard 开发者。数据来源：各家官网 + GitHub 仓库（star 数、最近发布），**2026-09-23 重新抓取**。
> 这是一篇**尽量客观**的对比：好的说好，差的说差，短板一条不藏。

---

## 一、先说结论

- **Windows 上想要"够用 + 中文 + AI + 文件/图片 + 手机"**：YouBoard 的功能密度在这条线上属于上游，比 Win+V 强太多，也比 Ditto / ClipClip 这类"老而稳但没 AI"的更合 2026 年的用法。
- **想要极致稳、插件脚本、跨平台生态、订阅制省心**：Ditto / CopyQ（免费）和 Paste / Raycast（付费）确实更成熟。
- 一句话：**功能密度上游，生态与成熟度还在早期。**

---

## 二、YouBoard 是什么

轻量级 Windows / macOS 剪贴板管理器：自动记录你复制过的**文本、图片、文件、网址**，加密存本机，随取随用；
附带桌面小组件、**AI 就地处理**、**密库**、**浏览器扩展（Edge/Chrome）**、局域网手机传输、云同步。

- 开源（MIT）、无账号、无遥测；历史用 Fernet 加密落盘
- 安装版 / 便携版；Windows 与 macOS（Apple Silicon + Intel）
- **当前版本 v3.3.3**，仓库：<https://github.com/cloudxys/YouBoard>
- 实测占用（任务管理器口径）：**运行中专用工作集约 9 MB，收进托盘后约 5 MB**

---

## 三、五个成熟对手（客观事实）

| 工具 | 平台 | 价格 | 内容类型 | 搜索 / 标签 / 置顶 | 同步 | 加密 | AI | 扩展性 | 社区 |
|---|---|---|---|---|---|---|---|---|---|
| **YouBoard** | Win + macOS | 开源免费(MIT) | 文本 / 图片 / **文件** / 网址 | 搜索 · 标签 · 收藏 · 置顶 · 快照回滚 | Gist / WebDAV 端到端加密 + **局域网手机传输** | **历史加密落盘**，Key 走 DPAPI | **总结/翻译/改写/提取 + 自由对话 + 图片视觉 + 文件清单**（自带 Key，密库内容同样可用） | 无脚本/插件（有 359 项回归门禁 + 三平台 CI） | 27★ |
| **Ditto** | Windows | 免费开源 | 文本/图片/文件/富文本 | 搜索 · 置顶 · 批量 | 局域网互传 | 可选数据库密码 | 无 | 插件 SDK、多语言、便携 | 老牌（SourceForge 文件最后更新 2023） |
| **CopyQ** | Win/Linux/macOS | 免费开源 | 文本/图片/文件/自定义格式 | 搜索 · **标签** · **分组** · 命令 | 靠脚本 | 无内建 | 无 | **最强：命令 / 脚本 / 插件** | 12,292★ · 最新 v16.0.0 |
| **ClipClip** | Windows | 免费 + Pro **$49 一次性** | 文本/图片/文件 | 搜索 · 置顶 | 云端同步(Pro) | — | 无 | 截图 / 文件管理 / 格式转换 | 商业软件 |
| **Paste** | macOS / iOS | **订阅**（约 $2.49/月，年付 ~$29.99） | 文本/图片/链接 | 搜索 · 置顶 · 标签 | **iCloud 实时同步** | 系统级 | 有（Paste AI） | 团队版 | 商业软件 |
| **Raycast** | macOS(+Win/iOS) | 个人免费 / Pro 约 **$10 月付** | 文本/图片/文件 | 搜索 · 置顶 | 有（Pro） | 系统级 | **有（AI 命令）** | **扩展商店生态** | 商业软件 |

参考附表（也常被拿来比）：

| 工具 | 一句话 |
|---|---|
| **ClipboardFusion** | Windows，免费 + Pro $19/$29 一次性，**宏脚本（C#/Python）**是亮点 |
| **Maccy** | macOS，免费开源（**21,687★**），极简轻快，没有同步和 AI |
| **EcoPaste** | 跨平台新秀（**7,438★**），Tauri + Rust + React，体积小、检索强；没有手机端和云同步 |
| **Alfred** | macOS，Powerpack 买断，工作流生态最强 |
| **Win+V 内置** | 免费内置，上限 25 条，**没有搜索/标签/置顶，也不支持文件** |

---

## 四、YouBoard 强在哪（可以理直气壮说的部分）

### 1. AI 就地处理，而且**密库里的内容一样能用**

选中任意一条（历史或密库）→ 右键「AI 处理」：

- **总结要点 / 翻译 / 改写润色 / 提取关键信息 / 自定义提示**
- **图片视觉分析**：描述图片、提取图中文字（OCR）、翻译图中文字
- **文件清单分析**：这批文件大概是什么、有没有重复或体积异常、该怎么归档
- **自由对话**：把这条记录当上下文，多轮问答
- **自带 API Key**：DeepSeek / 通义 / 智谱 / OpenAI / Ollama 本地 / 任意 OpenAI 兼容接口

对比：Paste 和 Raycast 也有 AI，但**绑死自家订阅**，不给自带 Key；Ditto / CopyQ / ClipClip 完全没有。
隐私边界也写清楚了：只发送你选中的那一条、单次上限 2.4 万字符、Key 用系统 DPAPI 加密、不写日志。

### 2. 密库：私密内容"移入 / 移出"，不留第二份

顶栏挂锁进入**密库**：五类归档（全部 / 文本 / 图片 / 文件 / 网址）、名称可选、可搜索、可重命名，
独立文件加密落盘，**不参与手机传输、云同步、历史快照**。

- 「加入密库」= **从剪贴板历史移走**（连原来的时间戳、标签、收藏、置顶一起带走）
- 「移出密库」= **按当初的时间原样回到历史**，标签收藏一并还原
- 复制内容走应用内复制标记，**不会又记进历史**

这条是"隐私工具"该有的形态：Ditto / CopyQ / ClipClip 都没有独立的密库概念。

### 3. 浏览器扩展（Edge / Chrome），中英双语

网页里复制的内容（文本 / 链接 / 图片）会进 YouBoard；面板里双击历史直接插进网页输入框。
扩展只与**本机 127.0.0.1** 的桌面端通信，不经过任何服务器；界面跟随浏览器语言（中文 / 英文）。

### 4. 文件分类做得细 + 手机端零安装

- 「文件」按扩展名细分：视频 / 图片 / 设计源文件 / 音频 / 文档 / 压缩包 / 程序 / 代码 / 字体 / 其他，覆盖 **400+ 扩展名**
- 同一 Wi-Fi 下**扫码即用**：手机浏览器看/复制历史、上传图片文件、把手机文字发回电脑，不用装 App、不过云

### 5. 标签 + 收藏 + 快照回滚

右键打标签、加星收藏；标签页上方一排筛选胶囊（`收藏 N` / `#标签 N`）可跟搜索、文件细分叠加；
**历史快照**能一键回滚删除 / 清空 / 置顶这些操作。

### 6. 安全模型透明

- 历史 Fernet 加密落盘（Ditto / CopyQ / Maccy 默认明文数据库）
- 云同步**端到端加密**（Gist / WebDAV，云端只有密文）
- Token / API Key 走 Windows DPAPI；手机传输只监听局域网、每次启动随机 token

### 7. 体感与占用

- **桌面小组件**：置顶小窗显示当前剪贴板 + 最近 20 条，点一下即复制；**点击不抢焦点**，玩游戏时点它不会切出去
- **内存占用**：实测运行中专用工作集约 **9 MB**、收进托盘约 **5 MB**（后台/空闲自动回收工作集）
- **卡片弹层可拖动**、内容变化时窗口自动跟随
- 复制/粘贴提示音、暗色/亮色/自定义皮肤、壁纸背景、中英双语、自定义动作快捷键

### 8. 工程化：更新不再"更新坏"

- **359 项回归门禁**（`tools/verify_all.py`），CI 上三平台（Windows + macOS arm64 + Intel）先跑门禁再打包
- 内置更新器：分段下载 + 多镜像探测 + **完整性校验（体积 / PE / PyInstaller 标记 / SHA256）** +
  **先备份再替换 + 装完自检 + 失败自动回滚**（曾发生过一次"更新把主程序换坏"的事故，现在从根上堵住）
- 版本号单一来源、`MAINTENANCE.md` 一页纸维护手册

---

## 五、YouBoard 差在哪（这几条必须承认）

### 1. 生态规模差一个数量级

**27★** vs CopyQ 12,292★ / Maccy 21,687★。没有教程沉淀、没有第三方主题和插件；
遇到罕见问题，你大概率是第一个遇到的。

### 2. 扩展性为零

CopyQ 有命令与脚本、ClipboardFusion 有 C#/Python 宏、Raycast 有插件商店；
YouBoard 的自动化都写死在代码里，用户不能自己加动作（只能靠"自定义 AI 提示词"变通）。

### 3. 跨平台只到"能用"

没有 Linux 版、没有手机 App；macOS 版是移植版，能用但不如 Maccy / Paste / Alfred 那样原生。

### 4. 缺一批"专业用户刚需"

- 粘贴为**纯文本**、粘贴特殊格式（Ditto / ClipClip / CopyQ 都有）
- 正则 / 模糊搜索（目前是包含匹配）
- 按应用设规则、多选批量粘贴、内容转换（大小写 / 去格式）

### 5. 同步是"手动 + 自备存储"

Paste / Raycast 是自动实时同步；YouBoard 要手动点上传下载，还得自己准备 Gist 或 WebDAV，没有官方托管通道。

### 6. 包体与分发

Python / PyQt6 打包后约 32 MB（**内存占用已优化到个位数 MB，这条不再是短板**，但体积仍是）；
安装包**未做代码签名**，首次运行会有 SmartScreen 提示；也没上 winget / homebrew / 应用商店。

### 7. 稳定性还在爬坡

最近几版修掉的问题包括：一次失败的自动更新把主程序换成坏文件、密库内容留在历史里、
点「自定义」卡片被裁切、卡片不能拖动、启动后占用 100 MB+……
这些在老项目里早被百万用户磨平了。

### 8. 只有中英两种语言

Ditto、CopyQ 支持十几种到几十种语言。

---

## 六、那到底该选谁？

| 你的情况 | 建议 |
|---|---|
| Windows，要"复制历史 + 文件/图片管理"，免费稳定 | **Ditto** 或 **CopyQ** |
| Windows，想用 AI 直接处理复制的内容，不想为 AI 再买订阅 | **YouBoard**（自带 Key） |
| 想把密码这类私密内容单独收起来 | **YouBoard 密库**（其他家没有） |
| Windows，需要截图 + 剪贴板 + 文件管理一体 | **ClipClip**（$49 一次性） |
| macOS / iPhone / iPad 全家桶，愿意订阅 | **Paste** |
| macOS，想要启动器 + 插件生态 | **Raycast**（或 Alfred） |
| 只要"记住最近 25 条" | 直接按 **Win+V**，够用 |

---

## 七、接下来要补的三件事

1. **专业用户刚需**：粘贴为纯文本、批量操作、模糊搜索
2. **可扩展性**：先做"自定义动作"，再考虑脚本 / 插件
3. **分发第一印象**：上 winget / homebrew，做代码签名

---

## 八、下载与反馈

- 下载（安装版 / 便携版 / macOS）：<https://github.com/cloudxys/YouBoard/releases/latest>
- 问题反馈：<https://github.com/cloudxys/YouBoard/issues>
- 开源协议：MIT

> 数据来源：Ditto（SourceForge 项目页）、CopyQ / EcoPaste / Maccy（GitHub 仓库，2026-09-23 抓取）、
> ClipClip（官网定价 $49）、Paste / Raycast（官网定价页）、微软 Win+V 文档。
> star 数与定价会变，以各家官方为准。

---

## 附一：社媒短文案（可直接发）

**微博 / 小红书版（约 200 字）**

```
做了个 Windows/macOS 剪贴板工具 YouBoard，免费开源：
· 文本/图片/文件/网址四分类，历史本地加密存
· 右键就能 AI 处理：总结、翻译、改写、提取要点，图片能识别文字，文件能分析清单
· 自带 API Key，DeepSeek/通义/智谱/OpenAI/本地 Ollama 任选
· 密库：密码这类私密内容单独收起来，需要时右键"移出密库"按原时间放回
· 桌面小组件置顶显示，点它不会打断你正在玩的游戏；手机扫码就能看电脑剪贴板
· 常驻内存个位数 MB，附 359 项回归门禁 + 三平台 CI
和 Ditto、CopyQ、ClipClip、Paste、Raycast 的详细对比写在仓库里，优缺点都写了，欢迎来拍。
```

**知乎 / V2EX 版开头**

```
写了个剪贴板管理器，顺手做了一张和 Ditto / CopyQ / ClipClip / Paste / Raycast 的对比表，
包括我自己的短板。结论：功能密度上在 Windows 单机这条线上游，生态和成熟度上还在很早期。
```

---

## 附二：英文简介（用于 GitHub / Product Hunt）

```
YouBoard is an open-source clipboard manager for Windows and macOS.

It records text, images, files and links, encrypts everything locally, and gives you:
- AI actions on any entry (summarize / translate / rewrite / extract, image OCR, file list analysis)
  with your own API key - including entries stored in the Vault
- A Vault for secrets: move an item in and it leaves your history; move it out and it returns
  at its original time, with tags and favourite intact
- A desktop widget that never steals focus, a bilingual browser extension (Edge/Chrome),
  LAN phone transfer with no app install, and end-to-end encrypted Gist/WebDAV sync
- ~9 MB resident memory, 359 regression checks, three-platform CI, and an updater with
  integrity verification plus automatic rollback

MIT licensed. Strengths and weaknesses vs Ditto / CopyQ / ClipClip / Paste / Raycast are in COMPARISON.md.
```
