; Native Windows frontend. All application mutations remain in BundleInstaller.
#ifndef Backend
  #error Backend must point to the sealed frozen setup
#endif
#ifndef BackendSHA
  #error BackendSHA is required
#endif
#ifndef Output
  #error Output is required
#endif
#ifndef ProductVersion
  #error ProductVersion is required
#endif

[Setup]
AppId=GreenAtlasInstallerWizard
AppName=Green Atlas
AppVersion={#ProductVersion}.2
AppPublisher=Green Atlas
VersionInfoVersion={#ProductVersion}.2
DefaultDirName={localappdata}\GreenAtlas\Installer\Wizard
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableWelcomePage=no
DisableStartupPrompt=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
WizardStyle=modern
WizardSizePercent=115
SetupLogging=yes
CloseApplications=no
RestartApplications=no
CreateUninstallRegKey=no
UninstallFilesDir={app}
UninstallDisplayName=Green Atlas
OutputDir={#Output}
OutputBaseFilename=GreenAtlasSetupWizard
Compression=lzma2
SolidCompression=no
DiskSpanning=no

[Languages]
Name: russian; MessagesFile: compiler:Languages\Russian.isl

[Messages]
russian.WelcomeLabel2=Мастер установит Green Atlas для вашей учётной записи Windows.%n%nПосле установки нажмите «Пуск» и введите Green Atlas. На следующих шагах можно создать ярлык на рабочем столе.%n%nДля установки и запуска окна AutoCAD и Blender не нужны. Они понадобятся для работы с чертежами и рендера.%n%nПеред продолжением закройте Green Atlas и AutoCAD.
russian.ButtonFinish=&Готово
russian.ClickFinish=Нажмите «Готово», чтобы завершить установку.
russian.FinishedHeadingLabel=Green Atlas установлен
russian.FinishedLabel=Чтобы найти программу позже, нажмите «Пуск» и введите Green Atlas.%n%nНажмите «Готово», чтобы открыть приложение сейчас. Ваши проекты сохраняются при обновлении и удалении программы.

[Tasks]
Name: desktopicon; Description: Создать ярлык Green Atlas на рабочем столе; GroupDescription: Быстрый запуск:

[Files]
Source: {#Backend}; DestDir: {tmp}; DestName: GreenAtlasBackend.exe; Flags: dontcopy nocompression

[Dirs]
Name: {app}

[Icons]
Name: {autodesktop}\Green Atlas; Filename: {userappdata}\Autodesk\ApplicationPlugins\GreenAtlas.bundle\Contents\Windows\GreenAtlasDesktop.exe; Tasks: desktopicon

[Run]
Filename: {userappdata}\Autodesk\ApplicationPlugins\GreenAtlas.bundle\Contents\Windows\GreenAtlasDesktop.exe; Description: Запустить Green Atlas; Flags: nowait postinstall skipifsilent; Check: InstallationSucceeded

[Code]
const
  UninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\GreenAtlas';
  ExpectedBackendSHA = '{#BackendSHA}';
var
  Locations: TOutputMsgWizardPage;
  LocationsText: TNewMemo;
  BackendRunning: Boolean;
  BackendSucceeded: Boolean;
  BackendError, MaintenanceBackend: String;
  DependenciesPage: TWizardPage;
  DependencyEdits: array[0..4] of TNewEdit;
  DependencyBrowseButtons: array[0..4] of TNewButton;
  DependencyFindButton, DependencyCheckButton: TNewButton;
  DependencyStatus: TNewStaticText;
  CurrentPageID: Integer;
  DependencyNames: array[0..4] of String;
  DependenciesLoaded, DependenciesAccepted: Boolean;

function RunDependencyAction(const Action: String): Boolean; forward;
procedure DependencyBrowse(Sender: TObject); forward;
procedure DependencyFind(Sender: TObject); forward;
procedure DependencyCheck(Sender: TObject); forward;

function Elevated: Boolean;
  external 'IsUserAnAdmin@shell32.dll stdcall';

function InstallationSucceeded: Boolean;
begin
  Result := BackendSucceeded;
end;

function GetCustomSetupExitCode: Integer;
begin
  if BackendSucceeded then Result := 0 else Result := 1;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  CurrentPageID := CurPageID;
  if (CurPageID = DependenciesPage.ID) and not WizardSilent and not DependenciesLoaded then begin
    DependenciesLoaded := True;
    { Discovery waits for the backend. Paint the new page and its child
      controls first; otherwise its first frame has blank edits and labels. }
    WizardForm.Refresh;
    DependenciesPage.Surface.Refresh;
    DependencyFind(nil);
  end;
  if (CurPageID = wpFinished) and not BackendSucceeded then begin
    WizardForm.FinishedHeadingLabel.Caption := 'Установка не завершена';
    WizardForm.FinishedLabel.Caption := BackendError + #13#10#13#10 +
      'Ваши проекты сохранены. Устраните причину и запустите установщик повторно.';
    WizardForm.RunList.Visible := False;
  end;
end;

function InitializeSetup: Boolean;
begin
  Result := not Elevated;
  if not Result then
    MsgBox('Запустите установщик обычным двойным щелчком, без «Запуск от имени администратора».', mbError, MB_OK);
end;

procedure InitializeWizard;
var
  I, RowHeight, Top: Integer;
  LabelText: array[0..4] of String;
  LabelControl: TNewStaticText;
  BrowseButton, FindButton, CheckButton: TNewButton;
begin
  Locations := CreateOutputMsgPage(wpWelcome, 'Папки и запуск',
    'Green Atlas устанавливается для текущего пользователя',
    'Программа и плагин AutoCAD:' + #13#10 +
    ExpandConstant('{userappdata}\Autodesk\ApplicationPlugins\GreenAtlas.bundle') + #13#10#13#10 +
    'Ваши проекты:' + #13#10 + ExpandConstant('{localappdata}\GreenAtlas\Workspace') + #13#10#13#10 +
    'Запуск: нажмите «Пуск» и введите Green Atlas.' + #13#10#13#10 +
    'Папка программы общая с плагином AutoCAD. Проекты хранятся отдельно и сохраняются при обновлении и удалении.');
  { Static labels do not break long paths. A readonly native memo does, and
    lets the user select and copy the real path without changing it. }
  LocationsText := TNewMemo.Create(WizardForm);
  LocationsText.Parent := Locations.Surface;
  LocationsText.SetBounds(0, 0, Locations.SurfaceWidth, Locations.SurfaceHeight);
  LocationsText.Text := Locations.MsgLabel.Caption;
  LocationsText.ReadOnly := True;
  LocationsText.WordWrap := True;
  LocationsText.ScrollBars := ssVertical;
  LocationsText.BorderStyle := bsNone;
  LocationsText.Color := clWindow;
  Locations.MsgLabel.Visible := False;
  DependenciesPage := CreateCustomPage(Locations.ID, 'Компоненты и ресурсы',
    'Найдите установленные программы и подключите свои библиотеки');
  DependencyNames[0] := 'autocad_root'; DependencyNames[1] := 'autocad_template';
  DependencyNames[2] := 'blender'; DependencyNames[3] := 'botaniq_root'; DependencyNames[4] := 'pbr_root';
  LabelText[0] := 'Папка AutoCAD 2027'; LabelText[1] := 'Шаблон AutoCAD (.dwt)';
  LabelText[2] := 'Программа Blender (blender.exe)'; LabelText[3] := 'Библиотека Botaniq'; LabelText[4] := 'PBR-текстуры';
  LabelControl := TNewStaticText.Create(WizardForm);
  LabelControl.Parent := DependenciesPage.Surface;
  LabelControl.SetBounds(0, 0, DependenciesPage.SurfaceWidth, ScaleY(32));
  LabelControl.AutoSize := False; LabelControl.WordWrap := True;
  LabelControl.Caption := 'Пустое поле: подключить позже в настройках Green Atlas. Botaniq и PBR нужны для рендера. Библиотеки не копируются в программу.';
  RowHeight := (DependenciesPage.SurfaceHeight - ScaleY(78)) div 5;
  for I := 0 to 4 do begin
    Top := ScaleY(35) + I * RowHeight;
    LabelControl := TNewStaticText.Create(WizardForm);
    LabelControl.Parent := DependenciesPage.Surface;
    LabelControl.SetBounds(0, Top, DependenciesPage.SurfaceWidth, ScaleY(16));
    LabelControl.Caption := LabelText[I];
    DependencyEdits[I] := TNewEdit.Create(WizardForm);
    DependencyEdits[I].Parent := DependenciesPage.Surface;
    DependencyEdits[I].SetBounds(0, Top + ScaleY(17), DependenciesPage.SurfaceWidth - ScaleX(100), ScaleY(23));
    BrowseButton := TNewButton.Create(WizardForm);
    BrowseButton.Parent := DependenciesPage.Surface;
    BrowseButton.SetBounds(DependenciesPage.SurfaceWidth - ScaleX(92), Top + ScaleY(17), ScaleX(92), ScaleY(23));
    BrowseButton.Caption := 'Выбрать…'; BrowseButton.Tag := I;
    BrowseButton.OnClick := @DependencyBrowse;
    DependencyBrowseButtons[I] := BrowseButton;
  end;
  FindButton := TNewButton.Create(WizardForm); FindButton.Parent := DependenciesPage.Surface;
  FindButton.SetBounds(0, DependenciesPage.SurfaceHeight - ScaleY(28), ScaleX(150), ScaleY(25));
  FindButton.Caption := 'Найти автоматически'; FindButton.OnClick := @DependencyFind;
  DependencyFindButton := FindButton;
  CheckButton := TNewButton.Create(WizardForm); CheckButton.Parent := DependenciesPage.Surface;
  CheckButton.SetBounds(ScaleX(160), FindButton.Top, ScaleX(150), ScaleY(25));
  CheckButton.Caption := 'Проверить пути'; CheckButton.OnClick := @DependencyCheck;
  DependencyCheckButton := CheckButton;
  DependencyStatus := TNewStaticText.Create(WizardForm);
  DependencyStatus.Parent := DependenciesPage.Surface;
  DependencyStatus.SetBounds(ScaleX(320), FindButton.Top + ScaleY(4), DependenciesPage.SurfaceWidth - ScaleX(320), ScaleY(24));
  DependencyStatus.AutoSize := False; DependencyStatus.WordWrap := True;
  WizardForm.ReadyMemo.WordWrap := True;
  WizardForm.ReadyMemo.ScrollBars := ssVertical;
  WizardForm.FilenameLabel.Visible := False;
end;

procedure DependencyBrowse(Sender: TObject);
var
  Index: Integer;
  Selected, Filter, Extension: String;
begin
  Index := TNewButton(Sender).Tag;
  Selected := DependencyEdits[Index].Text;
  if (Index = 1) or (Index = 2) then begin
    if Index = 1 then begin Filter := 'Шаблоны AutoCAD|*.dwt'; Extension := 'dwt'; end
    else begin Filter := 'Blender|blender.exe|Программы|*.exe'; Extension := 'exe'; end;
    if GetOpenFileName('Выберите файл', Selected, ExtractFileDir(Selected), Filter, Extension) then
      DependencyEdits[Index].Text := Selected;
  end else if BrowseForFolder('Выберите папку компонента', Selected, False) then
    DependencyEdits[Index].Text := Selected;
  DependenciesAccepted := False;
end;

function RunDependencyAction(const Action: String): Boolean;
var
  Backend, InputFile, OutputFile, Summary: String;
  Lines: TArrayOfString;
  Code, I: Integer;
  NextEnabled, BackEnabled, CancelEnabled: Boolean;
begin
  Result := False;
  Backend := ExpandConstant('{tmp}\GreenAtlasBackend.exe');
  if not FileExists(Backend) then ExtractTemporaryFile('GreenAtlasBackend.exe');
  if CompareText(GetSHA256OfFile(Backend), ExpectedBackendSHA) <> 0 then
    RaiseException('Установочный комплект повреждён.');
  InputFile := ExpandConstant('{tmp}\dependency-input.ini');
  OutputFile := ExpandConstant('{tmp}\dependency-output.ini');
  SetArrayLength(Lines, 6); Lines[0] := '[paths]';
  for I := 0 to 4 do Lines[I + 1] := DependencyNames[I] + '=' + Trim(DependencyEdits[I].Text);
  if not SaveStringsToUTF8File(InputFile, Lines, False) then Exit;
  DeleteFile(OutputFile);
  BackendRunning := True;
  NextEnabled := WizardForm.NextButton.Enabled;
  BackEnabled := WizardForm.BackButton.Enabled;
  CancelEnabled := WizardForm.CancelButton.Enabled;
  WizardForm.NextButton.Enabled := False;
  WizardForm.BackButton.Enabled := False;
  WizardForm.CancelButton.Enabled := False;
  DependencyFindButton.Enabled := False;
  DependencyCheckButton.Enabled := False;
  for I := 0 to 4 do begin
    DependencyEdits[I].Enabled := False;
    DependencyBrowseButtons[I].Enabled := False;
  end;
  if Action = 'discover' then DependencyStatus.Caption := 'Ищем компоненты…'
  else if Action = 'check' then DependencyStatus.Caption := 'Проверяем пути…'
  else DependencyStatus.Caption := 'Сохраняем пути…';
  WizardForm.Refresh;
  DependenciesPage.Surface.Refresh;
  try
    Result := Exec(Backend, '--quiet --dependency-action ' + Action +
      ' --dependency-file "' + InputFile + '" --dependency-result "' + OutputFile + '"', '', SW_HIDE, ewWaitUntilTerminated, Code);
    Result := Result and (Code = 0) and FileExists(OutputFile);
    if FileExists(OutputFile) then begin
      Summary := GetIniString('result', 'warning', '', OutputFile);
      for I := 0 to 4 do begin
        if (Action <> 'discover') or (DependencyEdits[I].Text = '') then
          DependencyEdits[I].Text := GetIniString('paths', DependencyNames[I], DependencyEdits[I].Text, OutputFile);
        Summary := Summary + #13#10 + GetIniString('result', DependencyNames[I], '', OutputFile);
      end;
      if not Result then MsgBox('Проверьте выбранные компоненты:' + Summary, mbError, MB_OK);
    end else MsgBox('Не удалось проверить компоненты. Повторите поиск или оставьте поля пустыми для настройки позже.', mbError, MB_OK);
    if not Result then DependencyStatus.Caption := 'Проверьте пути'
    else if Action = 'discover' then DependencyStatus.Caption := 'Поиск завершён'
    else if Action = 'check' then DependencyStatus.Caption := 'Пути проверены'
    else DependencyStatus.Caption := 'Пути сохранены';
  finally
    BackendRunning := False;
    WizardForm.NextButton.Enabled := NextEnabled or (CurrentPageID = DependenciesPage.ID);
    WizardForm.BackButton.Enabled := BackEnabled or (CurrentPageID = DependenciesPage.ID);
    WizardForm.CancelButton.Enabled := CancelEnabled;
    DependencyFindButton.Enabled := True;
    DependencyCheckButton.Enabled := True;
    for I := 0 to 4 do begin
      DependencyEdits[I].Enabled := True;
      DependencyBrowseButtons[I].Enabled := True;
    end;
  end;
end;

procedure DependencyFind(Sender: TObject);
begin
  RunDependencyAction('discover');
end;

procedure DependencyCheck(Sender: TObject);
begin
  if RunDependencyAction('check') then
    MsgBox('Выбранные пути проверены. Пустые компоненты можно подключить позже. Настройки сохранятся только после установки.', mbInformation, MB_OK);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (CurPageID = DependenciesPage.ID) and not WizardSilent then begin
    Result := RunDependencyAction('check');
    DependenciesAccepted := Result;
  end;
end;

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo, MemoTypeInfo,
  MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
begin
  Result := 'Программа:' + NewLine + Space +
    ExpandConstant('{userappdata}\Autodesk\ApplicationPlugins\GreenAtlas.bundle') + NewLine + NewLine +
    'Запуск:' + NewLine + Space + 'Нажмите «Пуск» и введите Green Atlas' + NewLine + NewLine +
    MemoTasksInfo + NewLine + NewLine +
    'Установщик проверит комплект и Microsoft WebView2. Если системный WebView2 нужно обновить, Windows запросит разрешение для установщика Microsoft.';
end;

function StageText(const Stage: String): String;
begin
  Result := '';
  if Stage = 'checking' then Result := 'Проверяем, что Green Atlas и AutoCAD закрыты';
  if Stage = 'profile' then Result := 'Подготавливаем приватный профиль Green Atlas';
  if Stage = 'saving' then Result := 'Сохраняем средства обновления и восстановления';
  if Stage = 'webview' then Result := 'Проверяем и устанавливаем Microsoft WebView2';
  if Stage = 'installing' then Result := 'Устанавливаем и проверяем файлы Green Atlas';
  if Stage = 'shortcuts' then Result := 'Добавляем Green Atlas в меню «Пуск»';
  if Stage = 'removing' then Result := 'Удаляем программу, сохраняя ваши проекты';
  if Stage = 'complete' then Result := 'Готово';
end;

procedure BackendLog(const S: String; const Error, FirstLine: Boolean);
var
  Text: String;
begin
  Log(S);
  if Copy(S, 1, 9) = 'GA_ERROR:' then BackendError := Copy(S, 10, 8192);
  if Copy(S, 1, 9) <> 'GA_STAGE:' then Exit;
  Text := StageText(Copy(S, 10, 100));
  if Text = '' then Exit;
  if IsUninstaller then UninstallProgressForm.StatusLabel.Caption := Text
  else WizardForm.StatusLabel.Caption := Text;
end;

function RunBackend(const Executable, Arguments: String): Boolean;
var
  Code: Integer;
begin
  BackendError := '';
  BackendRunning := True;
  try
    Result := ExecAndLogOutput(Executable, Arguments, '', SW_HIDE,
      ewWaitUntilTerminated, Code, @BackendLog);
    Result := Result and (Code = 0);
    if not Result and (BackendError = '') then
      BackendError := 'Не удалось завершить операцию. Код: ' + IntToStr(Code) + '. Повторите запуск установщика.';
  finally
    BackendRunning := False;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Backend: String;
begin
  if CurStep <> ssPostInstall then Exit;
  Backend := ExpandConstant('{tmp}\GreenAtlasBackend.exe');
  if CompareText(GetSHA256OfFile(Backend), ExpectedBackendSHA) <> 0 then
    RaiseException('Проверка установочного комплекта не пройдена. Скачайте установщик повторно.');
  WizardForm.StatusLabel.Caption := 'Подготавливаем установку Green Atlas';
  WizardForm.ProgressGauge.Style := npbstMarquee;
  WizardForm.CancelButton.Enabled := False;
  try
    if not RunBackend(Backend, '--quiet --progress --native-uninstaller "' + ExpandConstant('{uninstallexe}') + '"') then begin
      SuppressibleMsgBox(BackendError, mbError, MB_OK, IDOK);
      Exit;
    end;
    if DependenciesAccepted and not RunDependencyAction('save') then begin
      BackendError := 'Программа установлена, но пути не сохранены. Откройте настройки компонентов в Green Atlas.';
      SuppressibleMsgBox(BackendError, mbError, MB_OK, IDOK);
      Exit;
    end;
    BackendSucceeded := True;
  finally
    WizardForm.ProgressGauge.Style := npbstNormal;
    WizardForm.CancelButton.Enabled := True;
  end;
end;

procedure CancelButtonClick(CurPageID: Integer; var Cancel, Confirm: Boolean);
begin
  if BackendRunning then Cancel := False;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Backend: String;
begin
  Result := '';
  WizardForm.PreparingLabel.Caption := 'Подготавливаем комплект и проверяем, что Green Atlas и AutoCAD закрыты';
  ExtractTemporaryFile('GreenAtlasBackend.exe');
  Backend := ExpandConstant('{tmp}\GreenAtlasBackend.exe');
  if CompareText(GetSHA256OfFile(Backend), ExpectedBackendSHA) <> 0 then
    Result := 'Установочный комплект повреждён. Скачайте его повторно.'
  else if DependenciesAccepted and not RunDependencyAction('check') then
    Result := 'Выбранные компоненты больше недоступны. Вернитесь к выбору путей.'
  else if not RunBackend(Backend, '--quiet --progress --check-closed') then
    Result := BackendError
  else if not RunBackend(Backend, '--quiet --progress --prepare-profile') then
    Result := BackendError;
end;

function InitializeUninstall: Boolean;
var
  Hash, Prefix: String;
begin
  Result := False;
  if Elevated then begin
    MsgBox('Удаляйте Green Atlas из своей учётной записи без повышения прав.', mbError, MB_OK);
    Exit;
  end;
  if not RegQueryStringValue(HKCU, UninstallKey, 'BackendSetup', MaintenanceBackend) then Exit;
  Prefix := ExpandConstant('{localappdata}\GreenAtlas\Installer\Versions\');
  if CompareText(Copy(MaintenanceBackend, 1, Length(Prefix)), Prefix) <> 0 then Exit;
  Hash := ExtractFileName(ExtractFileDir(MaintenanceBackend));
  if (Length(Hash) <> 64) or
    (CompareText(MaintenanceBackend, Prefix + Hash + '\GreenAtlasSetup.exe') <> 0) or
    not FileExists(MaintenanceBackend) then Exit;
  Result := CompareText(GetSHA256OfFile(MaintenanceBackend), Hash) = 0;
  if not Result then MsgBox('Средства удаления повреждены. Запустите установщик Green Atlas повторно.', mbError, MB_OK);
  if Result then begin
    Result := RunBackend(MaintenanceBackend, '--quiet --progress --check-closed');
    if not Result then MsgBox(BackendError, mbError, MB_OK);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Directory, Hash, Marker: String;
  MarkerText: AnsiString;
begin
  if CurUninstallStep <> usUninstall then Exit;
  if not RunBackend(MaintenanceBackend, '--uninstall --quiet --progress') then begin
    SuppressibleMsgBox(BackendError, mbError, MB_OK, IDOK);
    Abort;
  end;
  if not RunBackend(MaintenanceBackend, '--purge-app-backups --quiet --progress') then begin
    SuppressibleMsgBox(BackendError, mbError, MB_OK, IDOK);
    Abort;
  end;
  Directory := ExtractFileDir(MaintenanceBackend);
  Hash := ExtractFileName(Directory);
  Marker := Directory + '\setup-version.json';
  if LoadStringFromFile(Marker, MarkerText) and
    (String(MarkerText) = '{"schema": "green-atlas.saved-setup/1", "file": "GreenAtlasSetup.exe", "sha256": "' + Hash + '"}') and
    (CompareText(GetSHA256OfFile(MaintenanceBackend), Hash) = 0) then begin
    if DeleteFile(MaintenanceBackend) then begin
      DeleteFile(Marker);
      RemoveDir(Directory);
    end;
  end;
end;
