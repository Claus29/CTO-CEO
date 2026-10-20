; Instalador de DEV CONTROL (Inno Setup 6).
; Lo compila tools/build_windows.py:  ISCC /DAppVersion=2.0.0 packaging\installer.iss
;
; * Se instala para el usuario actual (sin permisos de administrador).
; * Pregunta si este equipo es el ANFITRIÓN (guarda los datos) o un MIEMBRO (se conecta a un anfitrión),
;   permite probar la conexión con el servidor y, solo si se marca la casilla, recuperar datos anteriores
;   (carpeta de la versión 1, base de datos .db o copia .zip).
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
  ImportPage: TWizardPage;
  ImportCheck: TNewCheckBox;
  ImportHint: TNewStaticText;
  ImportEdit: TNewEdit;
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

procedure UpdateImportCheck;
begin
  // Recuperar datos antiguos solo tiene sentido en el anfitrión (es quien guarda los datos).
  ImportCheck.Enabled := RolePage.SelectedValueIndex = 0;
  if not ImportCheck.Enabled then
    ImportCheck.Checked := False;
  ImportHint.Enabled := ImportCheck.Enabled;
end;

procedure RoleClick(Sender: TObject);
begin
  UpdateImportCheck;
end;

function HasDatabase(Dir: String): Boolean;
var
  F: TFindRec;
begin
  Result := FindFirst(Dir + '*.db', F);
  if Result then
    FindClose(F);
end;

// Carpeta de la versión anterior (con datos\*.db o *.db), una base de datos .db o una copia .zip de DEV CONTROL.
function ImportPathValid(Path: String): Boolean;
var
  Ext: String;
begin
  Path := Trim(Path);
  Ext := Lowercase(ExtractFileExt(Path));
  if Path = '' then
    Result := False
  else if DirExists(Path) then
    Result := HasDatabase(AddBackslash(Path) + 'datos\') or HasDatabase(AddBackslash(Path))
  else
    Result := FileExists(Path) and ((Ext = '.zip') or (Ext = '.db'));
end;

procedure BrowseFolderClick(Sender: TObject);
var
  Dir: String;
begin
  Dir := Trim(ImportEdit.Text);
  if BrowseForFolder('Elige la carpeta del programa anterior (la que contiene la carpeta «datos»):', Dir, False) then
    ImportEdit.Text := Dir;
end;

procedure BrowseFileClick(Sender: TObject);
var
  FileName: String;
begin
  FileName := '';
  if GetOpenFileName('Elige una copia de seguridad o una base de datos', FileName,
                     ExpandConstant('{userdocs}\DEV CONTROL\Copias de seguridad'),
                     'Copias y bases de datos (*.zip;*.db)|*.zip;*.db|Todos los archivos (*.*)|*.*', 'zip') then
    ImportEdit.Text := FileName;
end;

procedure InitializeWizard;
var
  Previous: String;
  Info: TNewStaticText;
  FolderButton, FileButton: TNewButton;
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
  RolePage.CheckListBox.Height := ScaleY(76);
  RolePage.CheckListBox.OnClickCheck := @RoleClick;

  ImportCheck := TNewCheckBox.Create(RolePage);
  ImportCheck.Parent := RolePage.Surface;
  ImportCheck.Top := RolePage.CheckListBox.Top + RolePage.CheckListBox.Height + ScaleY(14);
  ImportCheck.Width := RolePage.SurfaceWidth;
  ImportCheck.Height := ScaleY(18);
  ImportCheck.Caption := 'Tengo datos de la versión anterior y quiero recuperarlos';
  ImportCheck.Checked := False;
  ImportHint := TNewStaticText.Create(RolePage);
  ImportHint.Parent := RolePage.Surface;
  ImportHint.Top := ImportCheck.Top + ImportCheck.Height + ScaleY(4);
  ImportHint.Left := ScaleX(18);
  ImportHint.Width := RolePage.SurfaceWidth - ScaleX(18);
  ImportHint.AutoSize := False;
  ImportHint.WordWrap := True;
  ImportHint.Height := ScaleY(32);
  ImportHint.Caption := 'Márcala solo si usabas la versión anterior del programa o tienes una copia de seguridad que ' +
    'quieras recuperar en este equipo. Si no, déjala sin marcar.';
  UpdateImportCheck;

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

  // Página propia (no una página de carpeta de Inno Setup, que no admite dejarla vacía).
  ImportPage := CreateCustomPage(RolePage.ID, 'Recuperar datos anteriores',
    'Elige dónde están los datos que quieres recuperar.');
  Info := TNewStaticText.Create(ImportPage);
  Info.Parent := ImportPage.Surface;
  Info.Width := ImportPage.SurfaceWidth;
  Info.AutoSize := False;
  Info.WordWrap := True;
  Info.Height := ScaleY(66);
  Info.Caption := 'Puedes elegir la CARPETA del programa anterior (la que contenía la carpeta «datos») o un ARCHIVO de ' +
    'copia de seguridad (.zip) o de base de datos (.db). Los datos se recuperarán la primera vez que se abra DEV CONTROL, ' +
    'solo si en este equipo todavía no hay datos. El original no se modifica.' + #13#10 +
    'Si al final no quieres recuperar nada, pulsa «Atrás» y desmarca la casilla.';
  ImportEdit := TNewEdit.Create(ImportPage);
  ImportEdit.Parent := ImportPage.Surface;
  ImportEdit.Top := Info.Top + Info.Height + ScaleY(10);
  ImportEdit.Width := ImportPage.SurfaceWidth;
  ImportEdit.Text := '';
  FolderButton := TNewButton.Create(ImportPage);
  FolderButton.Parent := ImportPage.Surface;
  FolderButton.Top := ImportEdit.Top + ImportEdit.Height + ScaleY(10);
  FolderButton.Width := ScaleX(170);
  FolderButton.Height := ScaleY(26);
  FolderButton.Caption := 'Elegir carpeta…';
  FolderButton.OnClick := @BrowseFolderClick;
  FileButton := TNewButton.Create(ImportPage);
  FileButton.Parent := ImportPage.Surface;
  FileButton.Top := FolderButton.Top;
  FileButton.Left := FolderButton.Left + FolderButton.Width + ScaleX(10);
  FileButton.Width := ScaleX(210);
  FileButton.Height := ScaleY(26);
  FileButton.Caption := 'Elegir copia (.zip o .db)…';
  FileButton.OnClick := @BrowseFileClick;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  if IsUpdate then
    Result := (PageID = RolePage.ID) or (PageID = ServerPage.ID) or (PageID = ImportPage.ID)
  else if PageID = ServerPage.ID then
    Result := RolePage.SelectedValueIndex = 0
  else if PageID = ImportPage.ID then
    Result := (RolePage.SelectedValueIndex <> 0) or not ImportCheck.Checked;
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
  if (CurPageID = ImportPage.ID) and not ImportPathValid(ImportEdit.Text) then begin
    if Trim(ImportEdit.Text) = '' then
      MsgBox('Elige la carpeta o la copia que quieres recuperar.' + #13#10#13#10 +
             'Si no quieres recuperar nada, pulsa «Atrás» y desmarca la casilla «Tengo datos de la versión anterior».',
             mbInformation, MB_OK)
    else
      MsgBox('Ahí no se encuentran datos de DEV CONTROL.' + #13#10#13#10 +
             'Elige la carpeta del programa anterior (la que contiene la carpeta «datos» con un archivo .db) o una copia ' +
             '.zip de la carpeta Documentos\DEV CONTROL\Copias de seguridad.', mbError, MB_OK);
    Result := False;
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
    end else begin
      SetIniString('instalacion', 'rol', 'miembro', Ini);
      SetIniString('instalacion', 'servidor', Trim(ServerPage.Values[0]), Ini);
    end;
    // Sin la casilla marcada se borra cualquier ruta de una instalación anterior.
    if (RolePage.SelectedValueIndex = 0) and ImportCheck.Checked then
      SetIniString('instalacion', 'importar_desde', Trim(ImportEdit.Text), Ini)
    else
      SetIniString('instalacion', 'importar_desde', '', Ini);
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
