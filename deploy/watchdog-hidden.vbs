' Launch the AI-assistant watchdog with its console hidden from the first frame.
'
' Why this file exists: the task action used to be
'   powershell.exe ... -WindowStyle Hidden -File deploy\watchdog.ps1
' but -WindowStyle Hidden is applied by PowerShell AFTER the console host has
' already created and shown its window. On this machine the default terminal is
' Windows Terminal, so every minute the task fired, a terminal window titled
' "powershell.exe" flashed for about 2 seconds.
'
' wscript.exe is a GUI-subsystem host: it allocates no console of its own, and
' Run(..., 0, ...) passes SW_HIDE to CreateProcess, so the child's console
' window is created hidden instead of shown-then-hidden.
'
' Keep this file ASCII-only. wscript reads .vbs as ANSI (OEM 936 here), the same
' trap documented at the top of tools\start-ai-stack.bat: a non-ASCII comment can
' get mis-decoded and turn into executed garbage.
'
' Uninstall: point the task back at powershell.exe directly, then delete this file.
Option Explicit

Dim sh
Set sh = CreateObject("WScript.Shell")

' waitOnReturn = True on purpose. If it were False, wscript would exit the instant
' it spawned PowerShell, so Task Scheduler would consider the task finished while
' the watchdog is still running - that silently defeats IgnoreNew and
' ExecutionTimeLimit. Keeping it True preserves what the old direct-powershell
' action meant.
sh.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -File ""C:\Users\34426\ai-assistant\deploy\watchdog.ps1""", 0, True
