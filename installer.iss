; Inno Setup installer for Ivyea Translate.
; Build (after "pyinstaller ivyea-translate.spec" produced dist\IvyeaTranslate\):
;   iscc installer.iss
; Output: dist\IvyeaTranslate-Setup.exe
; Per-user install (no admin/UAC), Start Menu + optional desktop shortcut, uninstaller.

#define AppName "Ivyea Translate"
#define AppVersion "0.31.0"
#define AppExe "IvyeaTranslate.exe"

[Setup]
AppId={{0A625927-CFEA-41D6-996A-DBB16A8387E6}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Ivyea
DefaultDirName={autopf}\IvyeaTranslate
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=IvyeaTranslate-Setup
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional tasks:"

[Files]
Source: "dist\IvyeaTranslate\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
