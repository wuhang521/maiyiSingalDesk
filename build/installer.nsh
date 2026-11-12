!ifndef BUILD_UNINSTALLER
  !include "getProcessInfo.nsh"
  Var pid
  Var signalDeskRuntime

  !macro SignalDeskRuntimeCheck mode
    System::Call 'kernel32::SetEnvironmentVariableW(w "SIGNALDESK_RUNTIME_INSTALL", w "$INSTDIR") i.r0'
    System::Call 'kernel32::SetEnvironmentVariableW(w "SIGNALDESK_RUNTIME_MANIFEST", w "$PLUGINSDIR\signaldesk-video-manifest.json") i.r0'
    System::Call 'kernel32::SetEnvironmentVariableW(w "SIGNALDESK_RUNTIME_SCRIPT", w "$PLUGINSDIR\signaldesk-video-update.ps1") i.r0'
    nsExec::ExecToLog `"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -NonInteractive -Command "& ([ScriptBlock]::Create([IO.File]::ReadAllText($$env:SIGNALDESK_RUNTIME_SCRIPT))) ${mode}"`
    Pop $0
  !macroend

  !macro SignalDeskRuntimeInputs
    File "/oname=$PLUGINSDIR\signaldesk-video-update.ps1" "${BUILD_RESOURCES_DIR}\video-runtime-update.ps1"
    File "/oname=$PLUGINSDIR\signaldesk-video-manifest.json" "${PROJECT_DIR}\artifacts\video-runtime-installer\manifest.json"
  !macroend

  !macro SignalDeskRuntimeFailure
    MessageBox MB_OK|MB_ICONSTOP "视频运行环境更新失败。请关闭迈医协同后重新运行安装程序。" /SD IDOK
    SetErrorLevel 2
    Quit
  !macroend

  !macro customCheckAppRunning
    !insertmacro IS_POWERSHELL_AVAILABLE
    !insertmacro _CHECK_APP_RUNNING
    !insertmacro SignalDeskRuntimeInputs
    DetailPrint "正在检查现有视频运行环境…"
    !insertmacro SignalDeskRuntimeCheck Prepare
    ${if} $0 == 0
      StrCpy $signalDeskRuntime "staged"
    ${elseif} $0 != 2
      !insertmacro SignalDeskRuntimeFailure
    ${endif}
    ClearErrors
  !macroend

  !macro customHeader
    Function .onInstFailed
      ${if} $signalDeskRuntime == "staged"
        !insertmacro SignalDeskRuntimeCheck Recover
      ${endif}
    FunctionEnd
  !macroend

  !macro customInstall
    ${if} $signalDeskRuntime == "staged"
      DetailPrint "沿用现有视频运行环境。"
      !insertmacro SignalDeskRuntimeCheck Restore
      ${if} $0 != 0
        !insertmacro SignalDeskRuntimeFailure
      ${endif}
      StrCpy $signalDeskRuntime "restored"
    ${else}
      !insertmacro SignalDeskRuntimeInputs
      DetailPrint "正在安装视频运行环境…"
      SetOutPath "$INSTDIR\resources\video-runtime"
      SetCompress off
      File "/oname=$PLUGINSDIR\signaldesk-video-runtime.7z" "${PROJECT_DIR}\artifacts\video-runtime-installer\video-runtime.7z"
      !ifdef COMPRESS
        SetCompress "${COMPRESS}"
      !endif
      Nsis7z::Extract "$PLUGINSDIR\signaldesk-video-runtime.7z"
      Pop $0
      SetOutPath "$INSTDIR"
      !insertmacro SignalDeskRuntimeCheck Verify
      ${if} $0 != 0
        !insertmacro SignalDeskRuntimeFailure
      ${endif}
    ${endif}
    ClearErrors
  !macroend
!endif
