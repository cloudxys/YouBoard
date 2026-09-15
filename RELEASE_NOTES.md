v3.2.3：把应用内更新这条链路彻底收拾干净。

修掉更新时会弹出的 `timeout.exe - Application Error`（应用程序无法正常启动 0xc0000142）：更新脚本原来用 `timeout /t N` 做等待，而脚本是以"无控制台"的方式被拉起的，`timeout.exe` 在这种环境里起不来，于是弹错误框、而且并没有真的等待。现在等待改用 `ping`，不依赖控制台；替换 EXE 时的重试也加上了上限（最多 60 次），不会再空转或卡住。

同时强化了 v3.2.2 开始的修复：更新时会把继承自旧进程的 PyInstaller onefile 环境变量（`_PYI_APPLICATION_HOME_DIR` / `_PYI_ARCHIVE_FILE` / `_PYI_PARENT_PROCESS_LEVEL` / `_MEIPASS*`）清干净再启动新版本。不清的话，新版本会以为自己是旧进程的子进程、去复用那个已经被删掉的临时目录，于是报 `Failed to load Python DLL …\_MEI…\python312.dll` 或 `Security validation failure: unexpected name of application's home directory!`，必须退出重开。现在更新完成即自动打开，正常使用即可（本次实测：带着这套污染环境执行完整更新流程，新版本能正常启动、无任何报错弹窗）。

如果你还在 v3.2.2 或更早版本，升级还会一并拿到：设置界面的分组感重做（模块标题升为 13px 主色、条目降一档、组间留白 15px > 组内 7px）、列表里 Ctrl+C 复制完整原文（不再把带 ⏎、被截断的预览文字复制出去，也不会再因此多出一条重复记录）、预览面板完整显示长文、列表列宽自适应与大小单位补齐到 GB / TB、复制文件不再多出一条"文件名文本"、窗口缩放更顺滑且背景保持比例铺满、退出不残留临时目录、中英文界面不再互相夹杂。构建脚本里的 PyInstaller 仍固定为 6.22.3。

支持 Windows（安装版 / 便携版）与 macOS（Apple Silicon / Intel）。
