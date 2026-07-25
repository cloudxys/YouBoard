; YouBoard v1.6.0 Inno Setup 安装脚本
; 功能：多盘检测选最大空闲盘根目录安装，数据保留更新，uninstall.exe，自定义图标

#define MyAppName "YouBoard"
#define MyAppVersion "1.6.0"
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
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
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

[UninstallDelete]
; 卸载时不清除用户数据（保留配置和剪贴板历史），除非用户手动删除目录
; Type: files; Name: "{app}\.youboard.json"
; Type: files; Name: "{app}\.youboard_snapshots.json"

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
begin
  if CurStep = ssPostInstall then
  begin
    AppDir := ExpandConstant('{app}');
    { 创建 uninstall.exe 副本（与 unins000.exe 相同） }
    CopyFile(AppDir + '\unins000.exe', AppDir + '\uninstall.exe', False);
  end;
end;
