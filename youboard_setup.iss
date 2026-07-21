; YouBoard v1.2.0 Inno Setup 安装脚本
; 功能：多盘检测选最大空闲盘根目录安装，注册添加或删除程序，生成卸载程序，创建开始菜单快捷方式

#define MyAppName "YouBoard"
#define MyAppVersion "1.2.0"
#define MyAppPublisher "YouBoard"
#define MyAppExeName "YouBoard.exe"
#define MyAppURL "https://github.com"

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
SetupIconFile=.\You.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} {#MyAppVersion}

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: ".\dist\YouBoard.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
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
  Drives: array of string;
  I: Integer;
  DriveLetter: string;
  BestDrive: string;
  BestFree: Int64;
  Free: Int64;
  DriveCount: Integer;
  HasNonC: Boolean;
begin
  { 检测所有可用磁盘，选择最大空闲空间的盘 }
  BestDrive := '';
  BestFree := 0;
  DriveCount := 0;
  HasNonC := False;

  for I := Ord('A') to Ord('Z') do
  begin
    DriveLetter := Chr(I) + ':';
    if GetDriveType(DriveLetter + '\') = DRIVE_FIXED then
    begin
      DriveCount := DriveCount + 1;
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

  { 仅 C 盘则安装到 C:\Program Files\YouBoard }
  if not HasNonC then
  begin
    Result := ExpandConstant('{autopf}\{#MyAppName}');
  end
  else
  begin
    { 选最大空闲盘根目录，如 D:\YouBoard }
    Result := BestDrive + '\{#MyAppName}';
  end;
end;
