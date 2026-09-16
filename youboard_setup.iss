; YouBoard v3.2.5 Inno Setup 安装脚本
; 功能：多盘检测选最大空闲盘根目录安装，数据保留更新，uninstall.exe，自定义图标

#define MyAppName "YouBoard"
#define MyAppVersion "3.2.5"
#define MyAppPublisher "YouBoard"
#define MyAppExeName "YouBoard.exe"
#define MyAppURL "https://github.com/cloudxys/YouBoard"

[Setup]
AppId={{A3F7B2C1-9D4E-4A68-B5C2-1E8F0D3A7B9C}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={code:GetInstallDir}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\logo
OutputBaseFilename=YouBoard_Setup_v{#MyAppVersion}
SetupIconFile=.\YouBoard.ico
UninstallDisplayIcon={app}\YouBoard.ico
UninstallDisplayName={#MyAppName} {#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
; 安装/卸载界面统一成应用本身的浅色风格（浅灰底 + 应用图标），安装和卸载共用。
; 这里只用 Inno 6/7 都支持的写法，保证 GitHub Actions 上的 Inno 版本也能编译通过。
WizardStyle=modern
WizardImageFile=res\setup_wizard_large.bmp
WizardSmallImageFile=res\setup_wizard_small.bmp
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "chinesesimplified"; MessagesFile: "Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: ".\dist\YouBoard.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: ".\YouBoard.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{app}\uninstall.exe"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[Code]
const
  DRIVE_FIXED = 3;

function GetDiskFreeSpaceEx(lpDirectoryName: string;
  var lpFreeBytesAvailable, lpTotalNumberOfBytes, lpTotalNumberOfFreeBytes: Int64): Boolean;
  external 'GetDiskFreeSpaceExW@kernel32.dll stdcall';

function GetDriveType(lpRootPathName: string): Cardinal;
  external 'GetDriveTypeW@kernel32.dll stdcall';

function GetFreeSpace(const Drive: string): Int64;
var
  FreeAvailable, TotalSpace, TotalFree: Int64;
begin
  Result := 0;
  if GetDiskFreeSpaceEx(Drive + '\', FreeAvailable, TotalSpace, TotalFree) then
    Result := FreeAvailable;
end;

function GetInstallDir(Param: string): string;
var
  I: Integer;
  DriveLetter: string;
  BestDrive: string;
  BestFree: Int64;
  Free: Int64;
  HasNonC: Boolean;
begin
  BestDrive := '';
  BestFree := 0;
  HasNonC := False;

  for I := Ord('A') to Ord('Z') do
  begin
    DriveLetter := Chr(I) + ':';
    if GetDriveType(DriveLetter + '\') = DRIVE_FIXED then
    begin
      Free := GetFreeSpace(DriveLetter);
      if Free > BestFree then
      begin
        BestFree := Free;
        BestDrive := DriveLetter;
      end;
      if Chr(I) <> 'C' then
        HasNonC := True;
    end;
  end;

  if not HasNonC then
  begin
    Result := ExpandConstant('{autopf}\{#MyAppName}');
  end
  else
  begin
    Result := BestDrive + '\{#MyAppName}';
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  AppDir: string;
  UninstStr: string;
begin
  if CurStep = ssPostInstall then
  begin
    AppDir := ExpandConstant('{app}');
    { 将 unins000.exe/dat 重命名为 uninstall.exe/dat，只保留一个卸载文件 }
    if FileExists(AppDir + '\unins000.exe') then
    begin
      if FileExists(AppDir + '\uninstall.exe') then
        DeleteFile(AppDir + '\uninstall.exe');
      if FileExists(AppDir + '\uninstall.dat') then
        DeleteFile(AppDir + '\uninstall.dat');
      RenameFile(AppDir + '\unins000.exe', AppDir + '\uninstall.exe');
      if FileExists(AppDir + '\unins000.dat') then
        RenameFile(AppDir + '\unins000.dat', AppDir + '\uninstall.dat');
      { 更新注册表中的卸载路径 }
      UninstStr := AppDir + '\uninstall.exe';
      RegWriteStringValue(HKCU,
        'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting("AppId")}_is1',
        'UninstallString', UninstStr);
      RegWriteStringValue(HKCU,
        'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting("AppId")}_is1',
        'QuietUninstallString', UninstStr + ' /SILENT');
    end;
  end;
end;

{ 让安装 / 卸载窗口用应用同款浅色底（浅灰 #f3f4f6，TColor 是 BGR 顺序） }
procedure InitializeWizard();
begin
  WizardForm.Color := $00F6F4F3;
end;

procedure InitializeUninstallProgressForm();
begin
  UninstallProgressForm.Color := $00F6F4F3;
end;

{ ---------------------------------------------------------------------------
  卸载：询问是否一并删除本地数据

  YouBoard 的历史 / 设置 / 图片都存在安装目录里（.youboard.json、images 等），
  Inno 默认不会删除它们——如果什么都不做，卸载后重装会"悄悄"沿用旧数据。
  这里给用户一个明确选择：
    · 是 = 一并删除（不可恢复）
    · 否 = 保留（默认；重装后新版本会继续读取）
  静默卸载（/SILENT、/VERYSILENT 或 /SUPPRESSMSGBOXES）不弹窗，按默认值走，
  也就是"保留"，避免自动化流程里误删用户数据。
  --------------------------------------------------------------------------- }

{ 本地数据清单：与 youboard_core.py 里的 _BASE_DIR 下各路径保持一致 }
function HasLocalData(AppDir: String): Boolean;
begin
  Result := FileExists(AppDir + '\.youboard.json') or
            FileExists(AppDir + '\.youboard.json.bak') or
            FileExists(AppDir + '\.youboard_snapshots.json') or
            FileExists(AppDir + '\youboard_config.json') or
            FileExists(AppDir + '\youboard.key') or
            FileExists(AppDir + '\youboard_error.log') or
            DirExists(AppDir + '\images') or
            DirExists(AppDir + '\content') or
            DirExists(AppDir + '\file_cache');
end;

procedure DeleteLocalData(AppDir: String);
begin
  { 历史是加密的，必须连密钥 youboard.key 一起删；只删一半会留下打不开的历史 }
  DeleteFile(AppDir + '\.youboard.json');
  DeleteFile(AppDir + '\.youboard.json.bak');
  DeleteFile(AppDir + '\.youboard_snapshots.json');
  DeleteFile(AppDir + '\youboard_config.json');
  DeleteFile(AppDir + '\youboard.key');
  DeleteFile(AppDir + '\youboard_error.log');
  DelTree(AppDir + '\images', True, True, True);
  DelTree(AppDir + '\content', True, True, True);
  DelTree(AppDir + '\file_cache', True, True, True);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  AppDir: String;
  Answer: Integer;
begin
  if CurUninstallStep = usUninstall then
  begin
    AppDir := ExpandConstant('{app}');
    if not HasLocalData(AppDir) then
      Exit;
    Answer := SuppressibleMsgBox(
      '是否同时删除 YouBoard 的本地数据？' + #13#10#13#10 +
      '包含：剪贴板历史、图片、设置、加密密钥' + #13#10 +
      '位置：' + AppDir + #13#10#13#10 +
      '选择「是」：一并删除，删除后无法恢复。' + #13#10 +
      '选择「否」（默认）：保留下来，以后重新安装会继续读取这些数据。',
      mbConfirmation, MB_YESNO, IDNO);
    if Answer = IDYES then
      DeleteLocalData(AppDir);
  end;
end;
