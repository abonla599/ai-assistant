!include "MUI2.nsh"

; Basic settings
Name "AI Assistant"
OutFile "AI_Assistant_Setup.exe"
InstallDir "$PROGRAMFILES\AI_Assistant"
RequestExecutionLevel admin

; UI pages
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_LANGUAGE "SimpChinese"

Section "Install"
  SetOutPath "$INSTDIR"

  ; Copy all files from dist folder
  File /r "dist\*"

  ; Copy startup script
  File "start_ai.bat"

  ; Create shortcuts
  CreateDirectory "$SMPROGRAMS\AI Assistant"
  CreateShortCut "$SMPROGRAMS\AI Assistant\AI Assistant.lnk" "$INSTDIR\start_ai.bat"
  CreateShortCut "$DESKTOP\AI Assistant.lnk" "$INSTDIR\start_ai.bat"

  ; Create uninstaller
  WriteUninstaller "$INSTDIR\uninstall.exe"
SectionEnd

Section "Uninstall"
  RMDir /r "$INSTDIR"
  Delete "$SMPROGRAMS\AI Assistant\AI Assistant.lnk"
  RMDir "$SMPROGRAMS\AI Assistant"
  Delete "$DESKTOP\AI Assistant.lnk"
SectionEnd
