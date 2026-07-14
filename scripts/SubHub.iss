# SubHub — Windows installer (Inno Setup 6). Desktop shortcut by default.
# Prefer LocalAppData (no admin). Processes only while app runs (no forced startup).

#ifndef AppVer
  #define AppVer "1.4.1"
#endif

[Setup]
AppId={{Crownfall-SubHub-Desktop}}
AppName=SubHub
AppVersion={#AppVer}
AppPublisher=Crownfall
DefaultDirName={localappdata}\Programs\SubHub
DefaultGroupName=SubHub
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=SubHub-Setup-{#AppVer}
SetupIconFile=..\assets\app.ico
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
UninstallDisplayIcon={app}\SubHub.exe
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Ярлык на рабочем столе"; GroupDescription: "Дополнительно:"; Flags: checkedonce
Name: "startupicon"; Description: "Автозапуск при входе в Windows"; GroupDescription: "Дополнительно:"; Flags: unchecked

[Files]
Source: "..\dist\stage\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\SubHub"; Filename: "{app}\SubHub.exe"; WorkingDir: "{app}"
Name: "{group}\Удалить SubHub"; Filename: "{uninstallexe}"
Name: "{autodesktop}\SubHub"; Filename: "{app}\SubHub.exe"; WorkingDir: "{app}"; Tasks: desktopicon
Name: "{userstartup}\SubHub"; Filename: "{app}\SubHub.exe"; WorkingDir: "{app}"; Tasks: startupicon

[Run]
Filename: "{app}\SubHub.exe"; Description: "Запустить SubHub"; Flags: nowait postinstall skipifsilent
