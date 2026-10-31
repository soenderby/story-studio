; Inno Setup 6 script for Story Studio (per-user, no admin prompt).
; Build: packaging\build.ps1, or after PyInstaller:
;   ISCC.exe /DAppVersion=0.1.0 packaging\installer.iss
;
; Extra command-line switch (besides Inno's own /VERYSILENT, /SUPPRESSMSGBOXES, /DIR=, /LOG=...):
;   /RELAUNCH   start Story Studio after the install, also when silent (the updater uses it).
;
; Closing a running Story Studio before files are replaced (PrepareToInstall, and at uninstall):
;   1. POST http://127.0.0.1:<port>/api/quit with the token from instance.json in the app-data
;      folder (STORY_STUDIO_HOME if set, else %APPDATA%\Story Studio), then wait up to 5 s for
;      that pid to exit.
;   2. Fallback: CloseApplications=yes, the Restart Manager (the tray accepts its session-end messages).
; The uninstaller removes only installed files: never %APPDATA%\Story Studio or any story folder.

#ifndef AppVersion
  #error Pass the version: ISCC /DAppVersion=X.Y.Z installer.iss (build.ps1 reads it from version.py)
#endif
#ifndef AppNumericVersion
  #define AppNumericVersion AppVersion
#endif
#ifndef DistDir
  #define DistDir "..\dist\StoryStudio"
#endif

[Setup]
AppId={{15941046-8201-491A-A5E0-2A057BDD1F20}
AppName=Story Studio
AppVersion={#AppVersion}
AppVerName=Story Studio {#AppVersion}
AppPublisher=Story Studio
AppPublisherURL=https://github.com/soenderby/story-studio
AppSupportURL=https://github.com/soenderby/story-studio/issues
AppUpdatesURL=https://github.com/soenderby/story-studio/releases
VersionInfoVersion={#AppNumericVersion}
VersionInfoProductVersion={#AppNumericVersion}
VersionInfoDescription=Story Studio Setup
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\Story Studio
DisableDirPage=yes
DefaultGroupName=Story Studio
DisableProgramGroupPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=Output
OutputBaseFilename=StoryStudio-Setup-{#AppVersion}
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\StoryStudio.exe
UninstallDisplayName=Story Studio
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
CloseApplications=yes
RestartApplications=no
SetupLogging=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; The install dir is replaced on every update: no stale modules from an older build.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Story Studio"; Filename: "{app}\StoryStudio.exe"
Name: "{autodesktop}\Story Studio"; Filename: "{app}\StoryStudio.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\StoryStudio.exe"; Description: "Launch Story Studio"; Flags: nowait postinstall skipifsilent; Check: not ShouldRelaunch
; After an update the open editor tabs reload by themselves, so no new browser tab.
Filename: "{app}\StoryStudio.exe"; Parameters: "--no-browser"; Flags: nowait; Check: ShouldRelaunch

[Code]
const
  SYNCHRONIZE = $00100000;

function OpenProcess(DesiredAccess: DWORD; InheritHandle: BOOL; ProcessId: DWORD): THandle;
  external 'OpenProcess@kernel32.dll stdcall';
function WaitForSingleObject(Handle: THandle; Milliseconds: DWORD): DWORD;
  external 'WaitForSingleObject@kernel32.dll stdcall';
function CloseHandle(Handle: THandle): BOOL;
  external 'CloseHandle@kernel32.dll stdcall';

function ShouldRelaunch: Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), '/RELAUNCH') = 0 then
      Result := True;
end;

function AppDataDir: String;
begin
  Result := GetEnv('STORY_STUDIO_HOME');
  if Result = '' then
    Result := ExpandConstant('{userappdata}\Story Studio');
end;

{ Enough JSON for instance.json: a string value without escapes, or an unsigned integer. }
function JsonValue(const Json, Key: String): String;
var
  P: Integer;
  S: String;
begin
  Result := '';
  P := Pos('"' + Key + '"', Json);
  if P = 0 then
    Exit;
  S := Copy(Json, P + Length(Key) + 2, MaxInt);
  P := Pos(':', S);
  if P = 0 then
    Exit;
  S := Trim(Copy(S, P + 1, MaxInt));
  if (Length(S) > 0) and (S[1] = '"') then
  begin
    S := Copy(S, 2, MaxInt);
    P := Pos('"', S);
    if P > 0 then
      Result := Copy(S, 1, P - 1);
  end
  else
  begin
    P := 1;
    while (P <= Length(S)) and (S[P] >= '0') and (S[P] <= '9') do
      P := P + 1;
    Result := Copy(S, 1, P - 1);
  end;
end;

procedure QuitRunningApp;
var
  Json: AnsiString;
  Port, Token: String;
  Process: THandle;
  Http: Variant;
begin
  if not LoadStringFromFile(AppDataDir + '\instance.json', Json) then
  begin
    Log('Story Studio: no instance.json, nothing to quit');
    Exit;
  end;
  Port := JsonValue(Json, 'port');
  Token := JsonValue(Json, 'token');
  if (Port = '') or (Token = '') then
    Exit;
  { Open the process first, so the wait below is for that process even if its pid is reused. }
  Process := OpenProcess(SYNCHRONIZE, False, StrToIntDef(JsonValue(Json, 'pid'), 0));
  try
    try
      Http := CreateOleObject('WinHttp.WinHttpRequest.5.1');
      Http.SetTimeouts(2000, 2000, 2000, 3000);
      Http.Open('POST', 'http://127.0.0.1:' + Port + '/api/quit', False);
      Http.SetRequestHeader('X-Story-Token', Token);
      Http.Send('');
      Log('Story Studio: /api/quit answered ' + IntToStr(Http.Status));
      if (Http.Status = 200) and (Process <> 0) then
        Log('Story Studio: wait for exit returned ' + IntToStr(WaitForSingleObject(Process, 5000)));
    except
      Log('Story Studio: /api/quit failed: ' + GetExceptionMessage);
    end;
  finally
    if Process <> 0 then
      CloseHandle(Process);
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  QuitRunningApp;
  Result := '';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    QuitRunningApp;
end;
