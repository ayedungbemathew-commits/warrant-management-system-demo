; Inno Setup 6 script -- builds  WarrantSystem-Setup-<version>.exe
; Do not edit the version here: "Build Installer.bat" reads it from wms_version.py and passes /DMyAppVersion=...
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#define MyAppName "WMS Portfolio Demo"
#define MyAppPublisher "Portfolio Demonstration"
#define MyAppExe "Warrant System.exe"

[Setup]
; AppId must never change: it is how a newer Setup recognises and upgrades an existing install
AppId={{7A2BC3E1-6617-42A9-A70E-77C9F02A163D}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
VersionInfoVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist_installer
OutputBaseFilename=WarrantSystem-Setup-{#MyAppVersion}
SetupIconFile=..\assets\wms_icon.ico
UninstallDisplayIcon={app}\{#MyAppExe}
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
ChangesAssociations=yes
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist\Warrant System\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Dirs]
; The data folder lives OUTSIDE the program folder, so installing, upgrading or uninstalling never touches saved warrants.
; Every Windows user of this PC may read and write it.
Name: "{commonappdata}\WarrantSystemPortfolioDemo"; Permissions: users-modify

[Registry]
; .wmsw = a shared, editable warrant. Double-clicking one opens it in the Warrant System.
Root: HKA; Subkey: "Software\Classes\.wmsw"; ValueType: string; ValueName: ""; ValueData: "WarrantSystem.SharedWarrant"; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\WarrantSystem.SharedWarrant"; ValueType: string; ValueName: ""; ValueData: "Warrant System shared warrant"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\WarrantSystem.SharedWarrant\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#MyAppExe},0"
Root: HKA; Subkey: "Software\Classes\WarrantSystem.SharedWarrant\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExe}"" ""%1"""

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "Start {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
// Uninstall removes the program only. Saved warrants, backups and logs stay in the data folder unless the user asks otherwise.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    if MsgBox('Also delete ALL saved warrants, backups and settings?' + #13#10 + #13#10 +
              'Choose No to keep them (recommended) -- they remain in:' + #13#10 +
              ExpandConstant('{commonappdata}\WarrantSystemPortfolioDemo'),
              mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      DelTree(ExpandConstant('{commonappdata}\WarrantSystemPortfolioDemo'), True, True, True);
end;
