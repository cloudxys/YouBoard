# YouBoard - 剪贴板历史管理工具

轻量级 Windows / macOS 剪贴板管理器：自动记录复制过的文本、图片、文件、网址，加密存在本机，随取随用。附带桌面小组件、AI 就地处理和手机传输。

[![Latest release](https://img.shields.io/github/v/release/cloudxys/YouBoard?color=2fb3a0&label=version)](https://github.com/cloudxys/YouBoard/releases/latest)
[![Downloads](https://img.shields.io/github/downloads/cloudxys/YouBoard/total?color=2fb3a0&label=downloads)](https://github.com/cloudxys/YouBoard/releases)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS-2fb3a0)
[![License](https://img.shields.io/badge/license-MIT-2fb3a0)](https://github.com/cloudxys/YouBoard/blob/main/LICENSE)
[![Stars](https://img.shields.io/github/stars/cloudxys/YouBoard?color=2fb3a0&label=stars)](https://github.com/cloudxys/YouBoard/stargazers)

## ✨ 主要功能

- **四大分类自动记录** — 文本 / 图片 / 文件 / 网址 + 「全部」聚合视图，跨分类搜索
- **列表好用的操作** — 搜索、置顶、标签、收藏、排序、导出、历史快照一键回滚
- **AI 就地处理** — 选中一条右键「AI 处理 ▸」：总结要点 / 翻译 / 改写润色 / 提取关键信息 / 自定义提示，结果流式输出，可复制、另存为新条或替换本条
- **桌面小组件** — 置顶小窗实时显示当前剪贴板与最近 20 条，点一下即复制；**不抢焦点**（点它不会打断前台应用 / 游戏）、不被其它窗口盖住、位置尺寸自动记忆
- **Win+V 接管（可选）** — 按 Win+V 打开 / 收起主窗口，并可一键关闭系统剪贴板历史
- **手机传输** — 同一 Wi-Fi 扫码即用，手机浏览器查看 / 复制历史，或把手机内容发回电脑，全程局域网
- **浏览器扩展（伴侣）** — Edge / Chrome 扩展把网页里复制的内容存进 YouBoard，双击历史直接插进网页输入框；只连本机 127.0.0.1（见 [edge_extension](edge_extension/README.md)）
- **云同步** — GitHub Gist / WebDAV 手动同步，历史用同步密码端到端加密后上传
- **密库** — 顶栏挂锁按钮：私密内容手动存进去，五类归档、名称可选、四类记录都能右键存进去；独立加密落盘，不进历史、不参与同步
- **安全与隐私** — 历史以 Fernet 加密落盘；AI 只发送你选中的那一条；Token / API Key 用系统 DPAPI 加密保存
- **外观与体验** — 暗色 / 亮色 / 自定义皮肤、毛玻璃与自定义背景、复制粘贴提示音、中英双语、环境灯带动效
- **数据移植与自动更新** — 扫描本机其它 YouBoard 安装并按内容去重合并；内置更新器多线程分段下载 + 多镜像探测
- **轻量常驻** — 后台静默约 5 MB、运行中约 9 MB（自动回收工作集）；便携版单文件 EXE，安装版可覆盖升级
- **浏览器扩展（伴侣）** — Edge / Chrome 扩展把网页里复制的内容存进 YouBoard，中英双语，只连本机 127.0.0.1

## 🆕 最近更新

- **v3.3.4** — 修复「更新失败会把主程序删掉」：回滚前先确认备份可用（没备份就保留现有主程序），新版确认启动后才删备份；AI 配置的「自定义」服务商不再丢地址/模型；AI 服务卡片抓窗口不再带一圈背景、卡片位置保持居中
- **v3.3.3** — 卡片弹层支持**按住拖动**、内容变化时窗口自动跟随（不再裁切）；**内存占用大幅下降**（收进托盘 / 空闲时自动回收，实测后台静默与运行中专用工作集均 ≈5 MB）
- **v3.3.2** — 密库改成「移入 / 移出」：放进密库的内容不再留在剪贴板历史里，右键可移回并按原时间归位；密库列表补齐右键菜单（复制 / 打开 / 导出 / **AI 处理** / 移出密库 / 删除）
- **v3.3.1** — 新增密库（五类归档、名称可选、可搜索）；浏览器扩展支持中英双语；自动更新加完整性校验 + 备份 + 失败自动回滚；修复「编辑标签」、弹层截图、更新结果卡片被截断等问题
- **v3.3.0** — 浏览器扩展（伴侣）+ 桌面端本机桥（只连 127.0.0.1）

> 每个版本的完整说明见 [RELEASE_NOTES.md](RELEASE_NOTES.md) 与 [Releases](https://github.com/cloudxys/YouBoard/releases)。

## 📥 下载安装

- **安装版（推荐）**：`YouBoard_Setup_v3.3.4.exe` —— 双击安装，自动创建快捷方式与卸载程序；覆盖安装保留全部数据（历史 / 配置 / 背景 / 快捷键 / 标签收藏 / AI 设置）
- **便携版**：`YouBoard.exe` —— 放到任意目录双击运行，数据存在 EXE 同目录
- **macOS**：`YouBoard_macOS_arm64_v3.3.4.dmg` / `.zip`（Apple Silicon）、`YouBoard_macOS_x86_64_v3.3.4.dmg` / `.zip`（Intel）
- 卸载时可选择是否保留本地数据，方便换机后继续用

👉 [前往 Releases 下载](https://github.com/cloudxys/YouBoard/releases)

## 🚀 快速上手

1. 复制任意内容即自动记录；`Alt+Q`（可在设置里改）或 Win+V（开启接管后）呼出主窗口
2. 列表里双击或按 `Enter` 复制回剪贴板；右键可 AI 处理 / 置顶 / 收藏 / 编辑标签 / 复制路径 / 导出
3. 设置里可配：AI 服务、提示音、皮肤与背景、历史保留策略、手机传输、云同步、动作快捷键

| 快捷键 | 作用 |
|---|---|
| `Enter` | 复制选中记录 |
| `空格` | 置顶 / 取消置顶 |
| `Ctrl+D` | 收藏 / 取消收藏 |
| `Ctrl+I` | AI 处理（总结） |
| `Del` | 删除选中记录 |
| `Tab` / `Shift+Tab` | 下一个 / 上一个分类 |
| `Ctrl+A` / `Ctrl+O` / `F5` | 全选 / 打开 / 刷新 |

> 以上动作快捷键都能在「设置 → 动作快捷键」里改；窗口左下角会显示你当前的设置。

## 🤖 AI 就地处理

1. **设置 → AI 服务 →「配置」**：选服务商（DeepSeek / 通义千问 / 智谱 GLM / OpenAI / Ollama 本地 / 自定义），填自己的 API Key，点「测试连接」验证后保存
2. 列表里选中一条**文本或网址**记录 → 右键「AI 处理 ▸」→ 选动作（也能按 `Ctrl+I` 直接总结）
3. 结果可以：**复制**（回剪贴板直接用）、**另存为新条**（原记录保留）、**替换本条**（正文换掉，标签与收藏保留）

用量与隐私：只发送你在列表里选中的那一条（不带其它历史），单次最多 2.4 万字符输入、约 1200 token 输出；API Key 在本机加密保存、不写日志。温度（默认 0.3）越低越稳、越高越发散。

## 📱 手机传输 / ☁️ 云同步

- **手机传输**：托盘右键 →「发送到手机…」或 设置 → 手机传输。手机连同一个 Wi-Fi 扫码即用：查看 / 复制历史、上传图片文件、把手机文字发回电脑。每次启动随机 token、只监听局域网，停止或退出立即断开
- **云同步**：设置 → 云同步，支持 GitHub Gist 与 WebDAV（如坚果云）。历史用你自己设的同步密码端到端加密后上传，云端只存密文；换设备输入同一密码即可合并恢复

## 🖥️ 系统要求

- Windows 10 / 11（64 位）或 macOS 11+（Intel / Apple Silicon）
- 安装包与便携版都已打包全部依赖，无需额外运行环境

## 🛠️ 源码构建

```bash
pip install PyQt6 pillow pyperclip keyboard cryptography pyinstaller==6.22.3 qrcode
python youboard_qt.py                              # 直接运行
python tools/verify_all.py                         # 回归测试（发版门禁）
python -m PyInstaller YouBoard.spec --noconfirm    # 打包便携版 EXE
iscc youboard_setup.iss                            # 生成安装包（需 Inno Setup 7）
```

macOS 构建见 [README_MAC.md](README_MAC.md)。发版只需改 `youboard_version.py` 里的版本号，再跑一次 `python tools/sync_version.py --docs`。

## 📁 项目结构（主要文件）

```
YouBoard/
├── youboard_qt.py        # 主程序（PyQt6 界面）
├── youboard_core.py      # 核心：剪贴板监控、加密存储、Win32 API
├── youboard_ai.py        # AI 就地处理（OpenAI 兼容协议 + 流式输出）
├── youboard_phone.py     # 手机传输（局域网 HTTP + 二维码）
├── youboard_sync.py      # 云同步（Gist / WebDAV + 端到端加密）
├── youboard_version.py   # 版本号唯一来源
├── tools/                # verify_all.py（回归门禁）、sync_version.py（版本同步）
├── res/                  # 图标与提示音资源
├── YouBoard.spec / YouBoard_Mac.spec / build_mac.sh
└── youboard_setup.iss    # Inno Setup 安装脚本
```

## 🔐 隐私

- 剪贴板历史与快照在本机以对称加密（Fernet）落盘，密钥文件 `youboard.key` 只在本机
- AI 请求只包含你在列表里选中的那一条记录；API Key 加密保存、不写日志
- 手机传输只监听局域网、每次启动随机 token；云同步为端到端加密，云端只存密文

## 📜 更新日志

- **v3.3.4** — 更新失败不再删主程序（回滚前先确认备份、确认新版启动后才清备份）；AI 自定义服务商配置不再丢；AI 服务卡片截图不带背景、位置保持居中
- **v3.3.3** — 卡片弹层可拖动、内容变化时窗口自动跟随；内存占用下降（后台/空闲自动回收，实测 ≈5 MB）
- **v3.3.2** — 密库改成「移入 / 移出」（按原时间归位）；密库列表右键菜单补齐（含 AI 处理）
- **v3.3.1** — 密库（五类归档、名称可选）；浏览器扩展中英双语；自动更新完整性校验 + 备份 + 回滚；标签编辑 / 截图 / 更新卡片修复
- **v3.3.0** — 浏览器扩展（伴侣）+ 桌面端本机桥（127.0.0.1）；上架材料与对比宣传文
- **v3.2.9** — 图片（视觉分析）与文件分类接入 AI；设置新增「关闭窗口行为」开关；模型名左右分开（真实 id + 可改显示名）
- **v3.2.8** — AI 自由对话与导出 TXT；服务商预设校正；小组件/设置的光标与遮挡修复；卡片弹层不再始终置顶；设置窗口尺寸收敛
- **v3.2.7** — 标签 / 收藏（纯本地）；AI 就地处理（总结 / 翻译 / 改写 / 提取要点）；设置页精简与 AI 服务卡片；打开设置时桌面小组件仍可用；分类胶囊、AI 弹层排版与直角框修复
- **v3.2.6** — 发版门禁（CI 先跑回归再打包）、版本号单一来源、README 徽章
- **v3.2.5** — 手型光标修复与统一、自定义皮肤与保留策略统一卡片风格、置顶记录带主题色胶囊
- **v3.2.4 及更早** — 见 [Releases](https://github.com/cloudxys/YouBoard/releases)

## 🎬 演示

<video src="https://github.com/cloudxys/YouBoard/releases/download/v1.9.0/01.mp4" controls width="640"></video>

## ☕ 支持作者

如果 YouBoard 帮到了你，可以在爱发电请我喝杯咖啡嘛（感谢各位的鼎力支持）。

[![化原/cloudxys 的爱发电](res/afdian.jpg)](https://afdian.com/a/mingdan?utm_source=copylink&utm_medium=link)

作者：**化原/cloudxys**　·　爱发电主页：<https://afdian.com/a/mingdan>

## 📄 License

MIT © cloudxys
