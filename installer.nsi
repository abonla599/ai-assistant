!include "MUI2.nsh"

; ==================== 基本信息 ====================
Name "AI智能助手"
OutFile "AI智能助手_Setup.exe"
InstallDir "$PROGRAMFILES\AI_Assistant"
RequestExecutionLevel admin
ShowInstDetails show

; ==================== 界面设置 ====================
!define MUI_ABORTWARNING
!define MUI_ICON "${NSISDIR}\Contrib\Graphics\Icons\modern-install.ico"
!define MUI_WELCOMEFINISHPAGE_BITMAP "${NSISDIR}\Contrib\Graphics\Wizard\nsis3-logo.bmp"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "LICENSE"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_LANGUAGE "SimpChinese"

; ==================== 安装部分 ====================
Section "安装AI智能助手" SecInstall
  SetOutPath "$INSTDIR"

  ; 复制所有文件到安装目录
  File /r "dist\*"
  File "start_ai.bat"
  File ".env.example"

  ; 创建开始菜单快捷方式
  CreateDirectory "$SMPROGRAMS\AI智能助手"
  CreateShortCut "$SMPROGRAMS\AI智能助手\AI智能助手.lnk" "$INSTDIR\start_ai.bat" "" "$INSTDIR\ai_assistant.exe" 0
  CreateShortCut "$SMPROGRAMS\AI智能助手\卸载.lnk" "$INSTDIR\uninstall.exe"

  ; 创建桌面快捷方式
  CreateShortCut "$DESKTOP\AI智能助手.lnk" "$INSTDIR\start_ai.bat" "" "$INSTDIR\ai_assistant.exe" 0

  ; 创建卸载程序
  WriteUninstaller "$INSTDIR\uninstall.exe"
SectionEnd

; ==================== 卸载部分 ====================
Section "卸载" Uninstall
  ; 删除安装的文件
  RMDir /r "$INSTDIR"

  ; 删除快捷方式
  Delete "$SMPROGRAMS\AI智能助手\AI智能助手.lnk"
  Delete "$SMPROGRAMS\AI智能助手\卸载.lnk"
  RMDir "$SMPROGRAMS\AI智能助手"
  Delete "$DESKTOP\AI智能助手.lnk"

  ; 删除卸载程序自身
  Delete "$INSTDIR\uninstall.exe"
SectionEnd

; ==================== 描述信息 ====================
LangString DESC_SecInstall ${LANG_SIMPCHINESE} "安装AI智能助手主程序及所有依赖"

!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
  !insertmacro MUI_DESCRIPTION_TEXT ${SecInstall} $(DESC_SecInstall)
!insertmacro MUI_FUNCTION_DESCRIPTION_END
