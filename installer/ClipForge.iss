; ClipForge installer (Inno Setup 6). Built by scripts/build_installer.ps1 from dist\ClipForge.
; Per-user install (no admin prompt) into %LOCALAPPDATA%\Programs\ClipForge, so ClipForge can
; update itself in place. Clips (Videos\ClipForge) and settings (%APPDATA%\ClipForge) are never removed.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6F1C2B7E-4E0D-4C55-9C1B-2D8B5C0A9F41}
AppName=ClipForge
AppVersion={#AppVersion}
AppVerName=ClipForge {#AppVersion}
AppPublisher=Knightsaway
AppPublisherURL=https://github.com/Knightsaway/ClipForge
AppSupportURL=https://github.com/Knightsaway/ClipForge/issues
AppUpdatesURL=https://github.com/Knightsaway/ClipForge/releases
DefaultDirName={localappdata}\Programs\ClipForge
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir=..\dist_release
OutputBaseFilename=ClipForge-{#AppVersion}-setup
SetupIconFile=..\assets\ClipForge.ico
UninstallDisplayIcon={app}\ClipForge.exe
UninstallDisplayName=ClipForge
LicenseFile=..\LICENSE
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"

[InstallDelete]
; a new version replaces the whole app folder content, so no stale DLLs from an older version stay behind
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\ffmpeg"

[Files]
Source: "..\dist\ClipForge\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\ClipForge"; Filename: "{app}\ClipForge.exe"
Name: "{autodesktop}\ClipForge"; Filename: "{app}\ClipForge.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\ClipForge.exe"; Description: "Start ClipForge now"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; stop "start with Windows" entries ClipForge may have added
Filename: "{sys}\schtasks.exe"; Parameters: "/Delete /TN ClipForge /F"; Flags: runhidden; RunOnceId: "DelTask"

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "ClipForge"; Flags: uninsdeletevalue dontcreatekey
