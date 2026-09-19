@echo off
rem AI Assistant - login autostart: backend + Cloudflare named tunnel.
rem Uninstall: delete the shortcut in  shell:startup  and delete this file.
rem
rem Comments here are ASCII on purpose. cmd.exe reads .bat with the OEM code page
rem (936 on this machine), so UTF-8 Chinese text in this file gets mis-decoded and
rem cmd ends up executing fragments of a rem line. Verified on 2026-09-19: a Chinese
rem comment made the netstat guard below never run. Put prose in docs/ instead.
rem
rem Both processes start Hidden, not with "start /MIN": /MIN gives each one a console
rem window with a close button, and clicking that X silently kills the process. Two
rem "backend vanished" incidents today (about 2.5h and about 37min of downtime) had
rem this exact shape - no traceback in data\backend.log (the exe writes it itself),
rem no Windows crash report, no reboot. deploy\watchdog.ps1 starts them the same way,
rem so the two start paths behave identically.
setlocal
cd /d C:\Users\34426\ai-assistant

rem Skip the backend if something already listens on 8000: a second instance loses the
rem race for the port, and in the window before it dies it writes the same session and
rem memory files as the first one.
netstat -ano | findstr /R /C:":8000 " | findstr LISTENING >nul 2>&1
if errorlevel 1 (
  powershell -NoProfile -Command "Start-Process -FilePath 'C:\Users\34426\ai-assistant\dist\run_backend\run_backend.exe' -WorkingDirectory 'C:\Users\34426\ai-assistant' -WindowStyle Hidden"
)

rem Named tunnel for ai.fenever.xyz. Guard is "is there a cloudflared process", not a
rem port check - the tunnel dials out, so local :8000 says nothing about whether it runs.
tasklist /FI "IMAGENAME eq cloudflared.exe" | findstr /I cloudflared.exe >nul 2>&1
if errorlevel 1 (
  powershell -NoProfile -Command "Start-Process -FilePath 'C:\Users\34426\ai-assistant\tools\cloudflared.exe' -ArgumentList 'tunnel','run','ai-assistant' -WindowStyle Hidden"
)
endlocal
