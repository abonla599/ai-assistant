@echo off
chcp 65001 >nul
title AI智能助手启动器

echo ========================================
echo   AI智能助手 - 正在启动...
echo ========================================
echo.

REM 获取当前目录
set "CURRENT_DIR=%~dp0"

REM 启动后端（隐藏窗口）
echo [1/2] 正在启动后端服务...
start "" /MIN "%CURRENT_DIR%run_backend\run_backend.exe"

REM 等待后端启动
echo      等待后端初始化（3秒）...
timeout /t 3 /nobreak >nul

REM 启动前端
echo [2/2] 正在启动应用界面...
start "" "%CURRENT_DIR%ai_assistant.exe"

echo.
echo ========================================
echo   启动完成！
echo   提示：后端在后台运行，请勿关闭任务栏图标
echo ========================================

exit
