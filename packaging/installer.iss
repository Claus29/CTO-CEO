; Instalador de DEV CONTROL (Inno Setup 6).
; Lo compila tools/build_windows.py:  ISCC /DAppVersion=2.0.0 packaging\installer.iss
;
; * Se instala para el usuario actual (sin permisos de administrador).
; * Pregunta si este equipo es el ANFITRIÓN (guarda los datos) o un MIEMBRO (se conecta a un anfitrión),
;   permite probar la conexión con el servidor y, opcionalmente, importar los datos de la versión 1.
; * Con /ACTUALIZACION=1 (lo usa el actualizador del programa) no pregunta nada ni cambia la configuración.
; * Desinstalar NUNCA borra los datos (Documentos\DEV CONTROL y %LOCALAPPDATA%\DEV CONTROL).

#define AppName "DEV CONTROL"
#ifndef AppVersion
  #define AppVersion "2.0.0"
#endif
#define AppExe "DEV CONTROL.exe"

[Setup]
AppId={{8C2F4A7E-3B1D-4E59-9C1A-6D2E7F0B5A41}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=DEV CONTROL
AppPublisherURL=https://github.com/Claus29/CTO-CEO
AppSupportURL=https://github.com/Claus29/CTO-CEO/issues
VersionInfoVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\{#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=DEVCONTROL-Setup-{#AppVersion}
SetupIconFile=devcontrol.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=force
CloseApplicationsFilter=*.exe
RestartApplications=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
ShowLanguageDialog=auto

[Languages]
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Crear un acceso directo en el escritorio"; Check: not IsUpdate
Name: "autostart"; Description: "Abrir DEV CONTROL al iniciar Windows (imprescindible en el anfitrión)"; Check: not IsUpdate

[Files]
Source: "..\dist\DEV CONTROL\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; Restos de una versión anterior que ya no forman parte del programa (los datos están en otra carpeta).
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Abrir DEV CONTROL ahora"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{cmd}"; Parameters: "/C reg delete ""HKCU\Software\Microsoft\Windows\CurrentVersion\Run"" /v ""{#AppName}"" /f"; Flags: runhidden; RunOnceId: "QuitarInicio"

[Code]
var
  RolePage: TInputOptionWizardPage;
  ServerPage: TInputQueryWizardPage;
  ImportPage: TInputDirWizardPage;
  TestButton: TNewButton;
  TestLabel: TNewStaticText;

function IsUpdate: Boolean;
begin
  Result := ExpandConstant('{param:ACTUALIZACION|0}') = '1';
end;

function InstallIni: String;
begin
  Result := ExpandConstant('{localappdata}\DEV CONTROL\instalacion.ini');
end;

function ServerAddress(Value: String): String;
begin
  Result := Trim(Value);
  if (Result <> '') and (Pos(':', Result) = 0) then
    Result := Result + ':8765';
end;

function TestServer(Addr: String): String;
var
  Http: Variant;
  Status: Integer;
  Body: String;
begin
  if Trim(Addr) = '' then begin
    Result := 'Escribe la dirección del servidor (IP o nombre del equipo anfitrión).';
    exit;
  end;
  try
    Http := CreateOleObject('WinHttp.WinHttpRequest.5.1');
    Http.SetTimeouts(4000, 4000, 4000, 4000);
    Http.Open('GET', 'http://' + ServerAddress(Addr) + '/api/health', False);
    Http.Send('');
    Status := Http.Status;
    Body := Http.ResponseText;
    if (Status = 200) and (Pos('"DEV CONTROL"', Body) > 0) then
      Result := ''
    else
      Result := 'Ese equipo responde, pero no es un servidor de DEV CONTROL.';
  except
    Result := 'No se puede conectar. Comprueba que el anfitrión tiene DEV CONTROL abierto y que estáis en la misma red o VPN.';
  end;
end;

procedure TestClick(Sender: TObject);
var
  Msg: String;
begin
  TestLabel.Caption := 'Probando…';
  Msg := TestServer(ServerPage.Values[0]);
  if Msg = '' then
    TestLabel.Caption := 'Conexión correcta: el servidor responde.'
  else
    TestLabel.Caption := Msg;
end;

procedure InitializeWizard;
var
  Previous: String;
begin
  RolePage := CreateInputOptionPage(wpSelectTasks,
    '¿Qué papel tiene este equipo?',
    'DEV CONTROL funciona con un equipo ANFITRIÓN que guarda los datos y el resto de MIEMBROS que se conectan a él.',
    'Elige una opción. Podrás cambiarla más adelante en Documentos\DEV CONTROL\Configuración\config.ini.',
    True, False);
  RolePage.Add('Anfitrión: este equipo guarda los datos del equipo (debe estar encendido para que los demás trabajen)');
  RolePage.Add('Miembro: este equipo se conecta al anfitrión');
  Previous := GetIniString('instalacion', 'rol', 'miembro', InstallIni);
  if Previous = 'anfitrion' then RolePage.SelectedValueIndex := 0 else RolePage.SelectedValueIndex := 1;

  ServerPage := CreateInputQueryPage(RolePage.ID,
    'Servidor del equipo',
    '¿Dónde está el equipo anfitrión?',
    'Escribe su IP (por ejemplo la de Tailscale, 100.x.y.z) o su nombre. Si usa otro puerto, añádelo así: 100.1.2.3:9000. ' +
    'Al iniciar sesión siempre podrás elegir otro servidor.');
  ServerPage.Add('Servidor:', False);
  ServerPage.Values[0] := GetIniString('instalacion', 'servidor', '', InstallIni);
  TestButton := TNewButton.Create(ServerPage);
  TestButton.Parent := ServerPage.Surface;
  TestButton.Caption := 'Probar conexión';
  TestButton.Top := ServerPage.Edits[0].Top + ServerPage.Edits[0].Height + ScaleY(10);
  TestButton.Width := ScaleX(140);
  TestButton.Height := ScaleY(26);
  TestButton.OnClick := @TestClick;
  TestLabel := TNewStaticText.Create(ServerPage);
  TestLabel.Parent := ServerPage.Surface;
  TestLabel.Top := TestButton.Top + TestButton.Height + ScaleY(8);
  TestLabel.Width := ServerPage.SurfaceWidth;
  TestLabel.AutoSize := False;
  TestLabel.Height := ScaleY(40);
  TestLabel.WordWrap := True;
  TestLabel.Caption := '';

  ImportPage := CreateInputDirPage(RolePage.ID,
    'Datos de la versión 1 (opcional)',
    '¿Usabas la versión 1 del programa en este equipo?',
    'Si es así, elige la carpeta donde estaba (la que contenía la carpeta «datos»). Sus incidencias, tareas, mensajes y ' +
    'adjuntos se importarán la primera vez que se abra DEV CONTROL. Si no, deja el campo vacío.',
    False, '');
  ImportPage.Add('Carpeta de la versión 1:');
  ImportPage.Values[0] := '';
end;

function HasDatabase(Dir: String): Boolean;
var
  F: TFindRec;
begin
  Result := FindFirst(Dir + '*.db', F);
  if Result then
    FindClose(F);
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  if IsUpdate then
    Result := (PageID = RolePage.ID) or (PageID = ServerPage.ID) or (PageID = ImportPage.ID)
  else if PageID = ServerPage.ID then
    Result := RolePage.SelectedValueIndex = 0
  else if PageID = ImportPage.ID then
    Result := RolePage.SelectedValueIndex <> 0;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Msg: String;
begin
  Result := True;
  if (CurPageID = ServerPage.ID) and (Trim(ServerPage.Values[0]) <> '') then begin
    Msg := TestServer(ServerPage.Values[0]);
    if Msg <> '' then
      Result := MsgBox(Msg + #13#10#13#10 + '¿Quieres continuar de todas formas? Podrás cambiar el servidor al iniciar sesión.',
                       mbConfirmation, MB_YESNO) = IDYES;
  end;
  if (CurPageID = ImportPage.ID) and (Trim(ImportPage.Values[0]) <> '') then begin
    if not HasDatabase(AddBackslash(ImportPage.Values[0]) + 'datos\') and not HasDatabase(AddBackslash(ImportPage.Values[0])) then begin
      MsgBox('En esa carpeta no está la base de datos de la versión 1 (un archivo .db dentro de la carpeta «datos»).', mbError, MB_OK);
      Result := False;
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Ini: String;
begin
  if (CurStep = ssPostInstall) and not IsUpdate then begin
    Ini := InstallIni;
    ForceDirectories(ExtractFileDir(Ini));
    if RolePage.SelectedValueIndex = 0 then begin
      SetIniString('instalacion', 'rol', 'anfitrion', Ini);
      SetIniString('instalacion', 'servidor', '', Ini);
      SetIniString('instalacion', 'importar_desde', Trim(ImportPage.Values[0]), Ini);
    end else begin
      SetIniString('instalacion', 'rol', 'miembro', Ini);
      SetIniString('instalacion', 'servidor', Trim(ServerPage.Values[0]), Ini);
    end;
    if WizardIsTaskSelected('autostart') then
      SetIniString('instalacion', 'iniciar_con_windows', 'si', Ini)
    else
      SetIniString('instalacion', 'iniciar_con_windows', 'no', Ini);
    SetIniString('instalacion', 'aplicar', 'si', Ini);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    MsgBox('DEV CONTROL se ha desinstalado. Tus datos y copias de seguridad se conservan en Documentos\DEV CONTROL ' +
           'y en %LOCALAPPDATA%\DEV CONTROL. Si vuelves a instalarlo, los encontrará solo.', mbInformation, MB_OK);
end;
