# YouBoard 维护手册

给以后接手的人（包括未来的我）看的一页纸：改哪里、怎么验、怎么发、哪些东西绝对不能碰。

## 1. 版本号只有一个来源

- 版本写在 `youboard_version.py` 的 `APP_VERSION`，**其它地方不要手改**。
- 改完跑一次 `python tools/sync_version.py --docs`：
  会同步 `version_info.txt`（EXE 版本资源）、`version_defines.iss`（安装包版本）、
  `README.md` / `README_MAC.md` 的下载文件名、`build_mac.sh` 的 DMG 名。
- 程序本身（`youboard_qt.py`）、macOS 的 CFBundleVersion、CI 产物名都直接读这个模块。

## 2. 回归门禁（改动前先跑，改动后再跑）

    python tools/verify_all.py     失败退出码 1；CI 里卡在打包之前

- 覆盖：语法 / 更新完整性 / 核心存储 / 标签收藏 / 密库 / 手机接口 / AI / 本机桥 / 界面像素级断言。
- 全部在**临时目录和项目副本**里跑，不读写用户真实数据；副本里的配置会关掉提示音。
- 新功能请顺手在 `tools/verify_all.py` 里加断言——这是唯一能防"改 A 面漏 B 面"的东西。
- GitHub Actions 的 build.yml 在打 tag 时会先跑这个门禁，不绿就不会打包发布。

## 3. 打包（每次改完代码都要重出三个产物）

    python -m PyInstaller YouBoard.spec --noconfirm     # 便携版，产出 dist/YouBoard.exe
    copy dist\YouBoard.exe YouBoard.exe                 # 项目根（自用那份）
    copy dist\YouBoard.exe ..\logo\YouBoard.exe         # logo 便携版
    编译 youboard_setup.iss                             # Inno Setup 7 → ..\logo\YouBoard_Setup_v<版本>.exe

- **打包前把 PATH 里的 poppler 目录摘掉**，否则它的 ICU DLL 会被打进包，体积从 33MB 涨到 46MB。
- 三个产物版本号必须一致（`(Get-Item 文件).VersionInfo.FileVersion`）。
- 冒烟：拷到临时目录里启动，确认进程活着、且没有生成 `youboard_error.log`；
  **不要在项目目录里跑冒烟**——那会写用户真实数据。

## 4. 自动更新的不变量（改动这里前先读）

历史事故：一次失败的更新把主程序换成了 87% 零字节的文件，且因为"先删后替换"无法回滚，用户彻底打不开。
下面几条是硬约束，`tools/verify_all.py` 里有对应断言：

1. 分片下载必须**整段无缝覆盖**才算成功（`_coverage_complete`）；每段要求 HTTP 206、
   写入长度必须等于请求区间。任何缺口 → 整包作废、换源重下。
2. 下载完必须过 `verify_update_file()`：体积下限、MZ 头、PE 头偏移+签名、
   PyInstaller 归档魔数（文件末尾 88 字节）、有发布摘要时比对 SHA256。
3. `_update.bat` 的顺序必须是 **改名备份 → 搬新文件 → certutil 校验 SHA256 →
   确认新进程活着**；任一步失败走 `_yb_rollback`。
4. 回滚**必须先确认 `.bak` 还在**：备份可用才动主程序（先把装进去的挪成
   `<exe>.failed`、再把备份改回来、搬不回来就放回去）；**没有备份时绝不删主程序**，
   只写 `<exe>.update_failed` 并提示到 Releases 重新下载安装包覆盖安装。
   —— 事故回顾：旧版回滚是"先 `del` 主程序再还原备份"，备份被提前删掉就等于把用户
   唯一的主程序删光（2026-09-24 真实发生）。
5. `.bak` **只有在确认新版真的活着之后**才删，并且由脚本自己删（成功分支
   `:_yb_done`）。"新版活着"的凭据 = 新版启动后写的 `<exe>.update_ok`
   （`YouBoardApp._check_update_leftover` 里写），脚本最多等 60 秒；写不出凭据时
   退回用进程名判断。**换包脚本还在跑（`_update.bat` 存在）时，应用这边绝不删 `.bak`。**
6. 脚本开跑前先清掉上一轮残留的 `.bak`（否则等待循环会把"旧备份"当成"这次备份好了"），
   并在主程序被强行挪走、备份还在时先用备份自愈。

## 5. 数据与红线

- 用户数据在 EXE 同目录（macOS 在 `~/Library/Application Support/YouBoard`）：
  `.youboard.json`（加密历史）、`youboard.key`、`youboard_config.json`、`images/`、
  `content/`、`file_cache/`、`youboard_vault.json` + `vault_files/`（密库）。
- **这些文件都不能提交**（`.gitignore` 已覆盖）；也**绝不要在验证时读写真实数据**，
  一律用临时目录 / 项目副本。
- 密库与剪贴板历史是两套独立数据：密库不参与手机传输、云同步、历史快照；
  从密库复制要调用 `mark_self_copy()`，否则会被剪贴板监控再收一条。

## 6. 改代码的两条老规矩

- 只改点名的地方，其余不动；改完把相关断言补进门禁。
- 文件读写统一 utf-8；写盘走 `_atomic_write()`；不要用 shell 重定向写源码。

## 7. 发版

1. 更新 `README.md` 顶部的「🆕 最近更新」和 `RELEASE_NOTES.md`（Release 正文取它）。
   - **README 的更新内容只保留"每个版本一两句短话"**（下载页要的是效率，不是流水账）；
     详细说明写进 `RELEASE_NOTES.md`，并在 README 末尾的「📜 更新日志」补一行。
2. 三个产物重出、冒烟通过、门禁全绿。
3. 打 tag 推送 → GitHub Actions 出 Windows + macOS（arm64/x86_64）产物并自动建 Release。
4. 发布后在应用里点一次「检查更新」，确认能正确识别版本号与更新说明。
