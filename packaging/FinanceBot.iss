; FinanceBot.iss
; Script do instalador para o FinanceBot

[Setup]
AppName=FinanceBot
AppVersion=1.0
AppPublisher=MVA Comercio
DefaultDirName={userappdata}\FinanceBot
DisableProgramGroupPage=yes
OutputBaseFilename=FinanceBot_Setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\FinanceBot.exe

[Files]
; Copia todos os arquivos da build gerada pelo PyInstaller
Source: "..\dist\FinanceBot\*"; DestDir: "{app}"; Flags: recursesubdirs

; Copia secrets para AppData
Source: "..\secrets\*"; DestDir: "{app}\secrets"; Flags: recursesubdirs

[Dirs]
Name: "{app}\relatorios"
Name: "{app}\xmls_baixados"
Name: "{app}\braspress_archives"

[Icons]
Name: "{userdesktop}\FinanceBot"; Filename: "{app}\FinanceBot.exe"
Name: "{group}\FinanceBot"; Filename: "{app}\FinanceBot.exe"

[Run]
Filename: "{app}\FinanceBot.exe"; Description: "Executar FinanceBot agora"; Flags: nowait postinstall skipifsilent
