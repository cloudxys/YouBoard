# 我做了个剪贴板工具，它和 Ditto / CopyQ / ClipClip / Paste / Raycast 比怎么样？

> 作者：YouBoard 开发者。数据来源：各家官网 + GitHub API，抓取时间 2026-09-18。
> 这是一篇**尽量客观**的对比：好的说好，差的说差。

---

## 一、先说结论

- **如果你在 Windows 上，想要"够用 + 中文 + AI + 文件/图片/手机"**：
  YouBoard 已经比系统自带的 Win+V 强太多，也比 Ditto / ClipClip 这类"老而稳但没 AI"的更符合 2026 年的用法。
- **如果你要的是"极致稳、插件脚本、跨平台生态、订阅制省心"**：
  Ditto / CopyQ（免费）和 Paste / Raycast（付费）确实比 YouBoard 成熟。
- 一句话：**功能密度上 YouBoard 在 Windows 单机这条线属于上游，生态和成熟度上它还在很早期。**

---

## 二、YouBoard 是什么

轻量级 Windows / macOS 剪贴板管理器：自动记录你复制过的**文本、图片、文件、网址**，
加密存在本机，随取随用。附带桌面小组件、AI 就地处理、局域网手机传输、云同步。

- 开源（MIT）、无账号、无遥测；历史用 Fernet 对称加密落盘
- 安装版 / 便携版；Windows 与 macOS（Apple Silicon + Intel）都能跑
- 当前版本 3.3.0，仓库：<https://github.com/cloudxys/YouBoard>

---

## 三、五个成熟对手（客观事实）

| 工具 | 平台 | 价格 | 内容类型 | 搜索 / 标签 / 置顶 | 同步 | 加密 | AI | 扩展性 | 社区 |
|---|---|---|---|---|---|---|---|---|---|
| **YouBoard** | Win + macOS | 开源免费(MIT) | 文本/图片/**文件**/网址 | 搜索·标签·收藏·置顶·快照回滚 | Gist/WebDAV 端到端加密 + **局域网手机传输** | **历史加密落盘**、Key 走 DPAPI | **总结/翻译/改写/提取 + 自由对话 + 图片视觉**（自带 Key） | 无脚本/插件 | 27★ |
| **Ditto** | Windows | 免费开源 | 文本/图片/文件/富文本 | 搜索·置顶·批量 | 局域网互传 | 可选数据库密码 | 无 | 插件 SDK、多语言、便携 | 7,159★ |
| **CopyQ** | Win/Linux/macOS | 免费开源 | 文本/图片/文件/自定义格式 | 搜索·**标签**·**分组**·命令 | 靠脚本 | 无内建 | 无 | **最强：命令 / 脚本 / 插件** | 12,266★ |
| **ClipClip** | Windows | 免费 + Pro **$49 一次性** | 文本/图片/文件 | 搜索·置顶 | 云端同步(Pro) | — | 无 | 屏幕截图 / 文件管理 / 格式转换 | 商业软件 |
| **Paste** | macOS / iOS | **订阅**（约 $2.49/月，年付 ~$29.99） | 文本/图片/链接 | 搜索·置顶·标签 | **iCloud 实时同步** | 系统级 | 有（Paste AI） | 团队版 | 商业软件 |
| **Raycast** | macOS(+Win/iOS) | 个人免费 / Pro **$10 月付** | 文本/图片/文件 | 搜索·置顶 | 有（Pro） | 系统级 | **有（AI 命令）** | **扩展商店生态** | 商业软件 |

参考附表（也常被拿来比）：

| 工具 | 一句话 |
|---|---|
| **ClipboardFusion** | Windows，免费 + Pro $19/$29 一次性，**宏脚本（C#/Python）**是亮点 |
| **Maccy** | macOS，免费开源（21,616★），极简轻快，没有同步和 AI |
| **Alfred** | macOS，Powerpack 一次买断，工作流生态最强 |
| **Win+V 内置** | 免费内置，上限 25 条，**没有搜索/标签/置顶，也不支持文件** |

---

## 四、YouBoard 强在哪（可以理直气壮说的部分）

### 1. AI 就地处理：这是目前最大的差异点

选中任意一条记录 → 右键「AI 处理」：

- **总结要点 / 翻译 / 改写润色 / 提取关键信息 / 自定义提示**
- **图片视觉分析**：描述图片、提取图中文字（OCR）、翻译图中文字
- **文件清单分析**：这批文件大概是什么、有没有重复或体积异常、该怎么归档
- **自由对话**：把这条记录当上下文，想聊什么聊什么，多轮问答

关键是：**自带 API Key**。支持 DeepSeek / 通义千问 / 智谱 GLM / OpenAI / Ollama 本地 /
任意 OpenAI 兼容接口 —— 想省钱用便宜档，想隐私就本地跑 Ollama。
对比：Paste 和 Raycast 也有 AI，但**绑死自家订阅**，不给你自带 Key；Ditto / CopyQ / ClipClip 完全没有。

隐私边界也写清楚了：只发送你选中的那一条，单次最多 2.4 万字符输入、约 1200 token 输出，
显示名不参与请求，Key 用系统 DPAPI 加密保存、不写日志。

### 2. 文件分类做得比大多数同行细

「文件」标签里按扩展名细分：视频 / 图片 / 设计源文件 / 音频 / 文档 / 压缩包 / 程序 / 代码 / 字体 / 其他，
覆盖 400+ 个扩展名（mp4→视频、psd/ai→设计源文件、rar/7z→压缩包…），
每行还能直接「打开 / 定位 / 复制路径」。Win+V 不支持文件，Paste / Maccy 也没有。

### 3. 标签 + 收藏 + 快照回滚

- 右键打标签、加星收藏；列表里收藏行有主题色五角星
- 标签页上方一排筛选胶囊（`收藏 N` / `#标签 N`），能跟搜索、文件细分叠加
- **历史快照**：删除 / 清空 / 置顶这些操作都能一键回滚 —— 这个功能在同价位工具里很少见

### 4. 安全模型比多数同行透明

- 历史用 Fernet 对称加密落盘（Ditto / CopyQ / Maccy 默认是明文数据库）
- 云同步是**端到端加密**（GitHub Gist / WebDAV，云端只有密文）
- Token / API Key 用 Windows DPAPI 加密；手机传输只监听局域网、每次启动随机 token

### 5. 手机端零安装

同一个 Wi-Fi 下扫码即用：手机浏览器查看 / 复制历史、上传图片文件、把手机文字发回电脑。
不用装 App、不经过云端。表里没有一家这么做（Paste 靠 iOS App + iCloud，Raycast 靠自家生态）。

### 6. 体感细节堆得最全

- **桌面小组件**：置顶小窗显示当前剪贴板 + 最近 20 条，点一下即复制；
  实测**点击不抢焦点**（`WS_EX_NOACTIVATE`，`WM_MOUSEACTIVATE` 返回 `MA_NOACTIVATE`），
  玩游戏时点它不会切出去；窗口置顶、位置尺寸自动记忆
- **Win+V 接管**：可选开启，按 Win+V 直接打开/收起主界面
- 复制/粘贴提示音、暗色/亮色/自定义皮肤、壁纸背景、环境灯带、中英双语
- 自定义动作快捷键（默认 `Enter` 复制、`空格` 置顶、`Ctrl+D` 收藏、`Ctrl+I` AI 总结）

### 7. 工程化程度不输商业产品

- **178 项回归门禁**（`tools/verify_all.py`），CI 上三平台（Windows + macOS arm64 + Intel）
  每次都先跑门禁再打包，不通过就不发版
- 内置更新器：多线程分段下载 + 多镜像探测
- 数据移植：自动扫描本机其它 YouBoard 安装，按内容去重合并（重复的只留一条，置顶优先）
- 版本号单一来源、一键同步文档

---

## 五、YouBoard 差在哪（这几条必须承认）

### 1. 生态规模差一个数量级

27★ vs Ditto 7,159★ / CopyQ 12,266★ / Maccy 21,616★。
没有教程、没有问答沉淀、没有第三方主题和插件。**遇到罕见问题，你大概率是第一个遇到的。**

### 2. 扩展性为零

CopyQ 能用命令和脚本自己扩展、ClipboardFusion 支持 C#/Python 宏、Alfred 有工作流、
Raycast 有插件商店。YouBoard 的"自动化"目前都写死在代码里，用户无法自己加动作。

### 3. 跨平台只到"能用"

没有 Linux 版、没有手机 App；macOS 版是移植版，能用，但体验不如 Maccy / Paste / Alfred 那种原生。

### 4. 缺一批"专业用户刚需"

- 粘贴为**纯文本**、粘贴特殊格式（Ditto / ClipClip / CopyQ 都有）
- 正则 / 模糊搜索（目前是包含匹配）
- 按应用设规则、多选批量粘贴、内容转换（大小写 / 去格式）

### 5. 同步是"手动 + 自备存储"

Paste / Raycast / ClipboardFusion 是自动实时同步；YouBoard 要手动点上传下载，
还得自己准备 Gist 或 WebDAV。没有官方托管通道。

### 6. 技术栈与分发

Python / PyQt6 打包后约 32MB，启动和内存占用都高于 C++（Ditto / CopyQ）或 Swift（Maccy）。
安装包**未做代码签名**，首次运行会有 SmartScreen 提示；也没上 winget / homebrew / 应用商店。

### 7. 稳定性还在爬坡

最近一周修掉的问题包括：导入数据后工具自己退出、重复记录在置顶与普通列表各留一份、
窗口缩放光标被锁死、弹层盖住桌面小组件……这些都是老项目早被百万用户磨平的东西。

### 8. 只有中英两种语言

Ditto、CopyQ 支持十几种到几十种语言。

---

## 六、那到底该选谁？

| 你的情况 | 建议 |
|---|---|
| Windows，想要"复制历史 + 文件/图片管理"，还要免费稳定 | **Ditto** 或 **CopyQ** |
| Windows，想要"用 AI 直接处理复制的内容"，不想为 AI 再买订阅 | **YouBoard**（自带 Key） |
| Windows，需要截图 + 剪贴板 + 文件管理一体 | **ClipClip**（$49 一次性） |
| macOS / iPhone / iPad 全家桶，愿意订阅 | **Paste** |
| macOS，想要启动器 + 插件生态 | **Raycast**（或 Alfred） |
| 只要"记住最近 25 条" | 直接按 **Win+V**，够用 |

---

## 七、接下来要补的三件事

1. **专业用户刚需**：粘贴为纯文本、批量操作、模糊搜索
2. **可扩展**：自定义动作（脚本或动作面板），哪怕先做最简单的"自定义提示词"
3. **分发第一印象**：上 winget / homebrew，做代码签名，解决"下载就被拦"

---

## 八、下载与反馈

- 下载（安装版 / 便携版 / macOS）：<https://github.com/cloudxys/YouBoard/releases/latest>
- 问题反馈：<https://github.com/cloudxys/YouBoard/issues>
- 开源协议：MIT（欢迎自己改，但请遵守协议）

> 数据来源：Ditto / CopyQ / Maccy 的 GitHub 仓库（star 数与最近推送时间，2026-09-18 抓取）、
> ClipClip、Paste、ClipboardFusion、Raycast、Alfred、1Clipboard 官网的定价与功能页面、
> 微软 Edge 开发者文档；各家功能与定价会变，以官方为准。

---

## 附一：社媒短文案（可直接发）

**微博 / 小红书版（约 200 字）**

```
做了个 Windows 剪贴板工具 YouBoard，免费开源：
· 文本/图片/文件/网址四分类，历史本地加密存
· 右键就能 AI 处理：总结、翻译、改写、提取要点，还能识别图片里的文字
· 自带 API Key，DeepSeek/通义/智谱/OpenAI/本地 Ollama 任选，不想花钱就本地跑
· 桌面小组件置顶显示，点它不会打断你正在玩的游戏
· 手机扫码就能看电脑剪贴板，不用装 App
跟 Ditto、CopyQ、ClipClip、Paste、Raycast 的详细对比写在 README 里了，优缺点都写了，欢迎来拍。
```

**知乎 / V2EX 版开头**

```
写了个剪贴板管理器，顺手做了一张和 Ditto / CopyQ / ClipClip / Paste / Raycast 的对比表，
包括我自己的短板。结论：功能密度上我在 Windows 单机这条线上游，生态和成熟度上我还在很早期。
```

## 附二：英文简介（用于 GitHub / Product Hunt）

```
YouBoard is a free, open-source clipboard manager for Windows and macOS:
text/image/file/URL categories, encrypted local history, tags & favourites,
snapshot rollback, a focus-friendly desktop widget, LAN phone transfer —
and built-in AI actions (summarize / translate / rewrite / extract, image OCR,
free-form chat) that work with your own API key (DeepSeek, Qwen, GLM, OpenAI,
or a local Ollama model). No account, no telemetry, nothing uploaded.

Honest comparison with Ditto, CopyQ, ClipClip, Paste and Raycast (pros and cons,
including my own weak spots) is in COMPARISON.md.
```
