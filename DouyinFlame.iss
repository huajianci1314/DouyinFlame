; 抖音续火助手 安装包脚本
; 编译：ISCC.exe DouyinFlame.iss
; 安装位置 {localappdata}\DouyinFlame：免管理员权限，且数据目录（douyin.db、
; user_data 登录态）落在 exe 旁的可写路径，卸载时默认保留这些数据

#define MyAppName "抖音续火助手"
#define MyAppExe "DouyinFlame.exe"

[Setup]
AppId={{D6A8F2B1-4C3E-4F9A-8B7D-1E2F3A4B5C6D}
AppName={#MyAppName}
AppVersion=1.0.1
DefaultDirName={localappdata}\DouyinFlame
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=installer
OutputBaseFilename=DouyinFlameSetup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName={#MyAppName}

[Tasks]
Name: "autostart"; Description: "开机自动启动（后台静默运行，自动补发当天漏掉的续火）"; GroupDescription: "附加任务："

[Files]
Source: "dist\DouyinFlame\*"; DestDir: "{app}"; Excludes: "douyin.db,user_data,user_data\*"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{userdesktop}\抖音续火控制台"; Filename: "{app}\{#MyAppExe}"; Comment: "打开续火管理页面（双击后自动弹出浏览器）"
Name: "{userprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "DouyinFlame"; ValueData: """{app}\{#MyAppExe}"" --autostart"; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "立即启动{#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
var
  RC: Integer;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  // 覆盖安装前结束正在运行的实例，避免文件占用导致安装失败
  Exec(ExpandConstant('{cmd}'),
       '/C taskkill /IM DouyinFlame.exe /F >nul 2>&1',
       '', SW_HIDE, ewWaitUntilTerminated, RC);
end;

function UninstallNeedRestart(): Boolean;
begin
  Exec(ExpandConstant('{cmd}'),
       '/C taskkill /IM DouyinFlame.exe /F >nul 2>&1',
       '', SW_HIDE, ewWaitUntilTerminated, RC);
  Result := False;
end;
