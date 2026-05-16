; ============================================================================
; Inno Setup script — Achadinhos Agent (Fase 9.5)
;
; Gera o installer Windows nativo a partir do `.exe` já buildado pelo
; PyInstaller (dist/AchadinhosAgent.exe).
;
; Build local:
;     # 1. Build do exe primeiro (PyInstaller)
;     pyinstaller --noconfirm --clean build.spec
;
;     # 2. Compila o installer (precisa Inno Setup 6 instalado:
;     # https://jrsoftware.org/isdl.php)
;     iscc installer.iss
;     # → installer/AchadinhosAgent-Setup-3.0.0.exe
;
; Build CI: via GitHub Actions (.github/workflows/release-installer.yml).
;
; O installer:
; - Coloca o .exe em %ProgramFiles%\Achadinhos (per-machine) OU em
;   %LocalAppData%\Programs\Achadinhos (per-user, sem admin).
; - Cria atalho no Menu Iniciar (sempre) e no Desktop (opt-in).
; - Registra `achadinhos://` URL protocol no Windows (Fase 9.6 vai
;   processar argumentos vindos dele).
; - Adiciona o agente ao auto-start (HKCU\...\Run) — fica rodando em
;   background a cada boot. Crítico pro cenário "controlar PC via celular".
; - Permite rodar o agente direto depois de instalar (opt-in).
; ============================================================================

#define MyAppName      "Achadinhos Agent"
#define MyAppVersion   "3.8.10"
#define MyAppPublisher "Achadinhos"
#define MyAppURL       "https://achadinhos.maisseguidores.ia.br"
#define MyAppExeName   "AchadinhosAgent.exe"

[Setup]
; AppId é um GUID novo, mantido para futuras versões — Inno usa pra detectar
; instalação existente e oferecer upgrade no lugar de instalar duplicado.
AppId={{D8C3F2A1-7E4B-4C9A-B6E5-3F8A1D2C5B9E}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
; Per-user install (não exige admin). Pra per-machine, trocar pra
; `{commonpf}\Achadinhos` e adicionar PrivilegesRequired=admin.
DefaultDirName={userpf}\Achadinhos
DefaultGroupName={#MyAppName}
; Permite mudar dir mas esconde a página por padrão (UX limpa).
DisableProgramGroupPage=yes
DisableDirPage=auto
; Saída do build
OutputDir=installer
OutputBaseFilename=AchadinhosAgent-Setup-{#MyAppVersion}
SetupIconFile=
Compression=lzma2/ultra
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
; Idioma (default English, mas pacote brazilian abaixo)
ShowLanguageDialog=auto
; Não criar restore point — instalação leve
CreateUninstallRegKey=yes
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
; Suporta Windows 10+ (mesmo target do agente)
MinVersion=10.0
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "portuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; \
  GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "autostart"; Description: "Iniciar o agente automaticamente quando o Windows ligar"; \
  GroupDescription: "Configuração"; Flags: checkedonce

[Files]
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; Menu Iniciar
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autoprograms}\Desinstalar {#MyAppName}"; Filename: "{uninstallexe}"
; Desktop (opt-in)
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; ── URL protocol handler `achadinhos://` (Fase 9.6 vai consumir os args)
Root: HKCU; Subkey: "Software\Classes\achadinhos"; \
  ValueType: string; ValueName: ""; ValueData: "URL:Achadinhos Protocol"; \
  Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\achadinhos"; \
  ValueType: string; ValueName: "URL Protocol"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\achadinhos\DefaultIcon"; \
  ValueType: string; ValueName: ""; ValueData: "{app}\{#MyAppExeName},0"
Root: HKCU; Subkey: "Software\Classes\achadinhos\shell\open\command"; \
  ValueType: string; ValueName: ""; \
  ValueData: """{app}\{#MyAppExeName}"" --uri ""%1"""

; ── Auto-start (opt-in via task `autostart`)
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
  ValueType: string; ValueName: "AchadinhosAgent"; \
  ValueData: """{app}\{#MyAppExeName}"""; \
  Tasks: autostart; Flags: uninsdeletevalue

[Run]
; Roda o agente direto depois de instalar (opt-in — checkbox final).
; nowait + skipifsilent → não bloqueia o installer; em modo silent não pergunta.
Filename: "{app}\{#MyAppExeName}"; Description: "Iniciar o {#MyAppName} agora"; \
  Flags: nowait postinstall skipifsilent

[UninstallRun]
; Tenta matar o processo do agente antes de remover o exe (senão dá erro
; "file in use"). taskkill é nativo do Windows e sai limpo se nada rodando.
Filename: "{cmd}"; Parameters: "/C taskkill /F /IM {#MyAppExeName} /T"; \
  Flags: runhidden; RunOnceId: "KillAgent"

[Code]
// Garante que se já houver instalação anterior, ela é desinstalada antes
// de continuar — evita arquivos órfãos / mistura de versões.
function GetUninstallString(): String;
var
  sUnInstPath: String;
  sUnInstallString: String;
begin
  sUnInstPath := ExpandConstant('Software\Microsoft\Windows\CurrentVersion\Uninstall\{#emit SetupSetting("AppId")}_is1');
  sUnInstallString := '';
  if not RegQueryStringValue(HKCU, sUnInstPath, 'UninstallString', sUnInstallString) then
    RegQueryStringValue(HKLM, sUnInstPath, 'UninstallString', sUnInstallString);
  Result := sUnInstallString;
end;

function IsUpgrade(): Boolean;
begin
  Result := (GetUninstallString() <> '');
end;

function InitializeSetup(): Boolean;
var
  V: Integer;
  iResultCode: Integer;
  sUnInstallString: String;
begin
  Result := True;
  if IsUpgrade() then begin
    V := MsgBox(ExpandConstant('Uma versão do {#MyAppName} já está instalada. Deseja desinstalar a versão atual e continuar?'),
                mbInformation, MB_YESNO);
    if V = IDYES then begin
      sUnInstallString := GetUninstallString();
      sUnInstallString := RemoveQuotes(sUnInstallString);
      Exec(ExpandConstant(sUnInstallString), '/SILENT /NORESTART /SUPPRESSMSGBOXES', '', SW_HIDE, ewWaitUntilTerminated, iResultCode);
    end else begin
      Result := False;
    end;
  end;
end;
