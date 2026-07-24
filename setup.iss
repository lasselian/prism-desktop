; Script generated for Prism Desktop
; Requires Inno Setup: https://jrsoftware.org/isdl.php

#define MyAppName "Prism Desktop"
#define MyAppVersion "1.5.5"
#define MyAppPublisher "Lasse Lian"
#define MyAppExeName "PrismDesktop.exe"

[Setup]
; NOTE: The value of AppId uniquely identifies this application. Do not use the same AppId value in installers for other applications.
; (To generate a new GUID, click Tools | Generate GUID inside the IDE.)
AppId={{D3FAC2A3-1A2B-4C3D-5E6F-789012345678}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=installer
OutputBaseFilename=PrismDesktopSetup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
CloseApplicationsFilter=PrismDesktop.exe
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
; Name: "norwegian"; MessagesFile: "compiler:Languages\Norwegian.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "startup"; Description: "Start {#MyAppName} with Windows"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; IMPORTANT: Ensure you run 'python build_exe.py' BEFORE compiling this script
; NOTE: onedir build — copy the entire dist\PrismDesktop\ folder
Source: "dist\PrismDesktop\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; NOTE: We do NOT bundle config.json so that new installs start fresh (or use AppData)

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; No skipifsilent: the in-app auto-updater runs this installer with /VERYSILENT
; and relies on this entry to relaunch the app afterward (see auto_updater.py).
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall

[Registry]
; Start with Windows logic
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#MyAppName}"; ValueData: """{app}\{#MyAppExeName}"""; Flags: uninsdeletevalue; Tasks: startup

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  { Force-kill all running instances including PyInstaller child processes (/T = process tree) }
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM PrismDesktop.exe /T', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Sleep(1500);
  Result := '';
end;
