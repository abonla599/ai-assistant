@echo off
rem 生成一个邀请码。口令从 .env 读，避免手抄进 shell 历史。
rem
rem 本文件的可执行行刻意只用 ASCII：cmd 按当前控制台代码页逐字节读批处理文件，
rem UTF-8 中文字符串的尾字节会把紧随其后的字符吞掉，实测把 echo 的后半截当成
rem 命令名执行（不是内部或外部命令），而文件开头的 chcp 65001 会让 cmd 重新定位
rem 文件偏移、把整行切碎。中文说明只放在 rem 与 docs 里。
setlocal
cd /d C:\Users\34426\ai-assistant

rem 只切第一个等号（tokens=1,*）。个人令牌与 bootstrap 口令都是 urlsafe base64，
rem 值里可能带 = 补位：写成 %BOOTSTRAP:ACCESS_TOKEN=% 会把整行前缀删成
rem "=<口令>="（两头各留一个等号），实测服务端回 401 缺少或错误的访问凭据。
rem delims 里带上空格：否则 "ACCESS_TOKEN=  " 这种只有空白的值会被当成有效口令。

if exist .env goto have_env
echo [FAIL] no .env in %cd%
echo        Invite codes must be signed with the bootstrap admin credential.
echo        Create .env in the project root from .env.example first.
exit /b 1

:have_env
set "BOOTSTRAP="
for /f "usebackq tokens=1,* delims== " %%a in (`findstr /b /c:"ACCESS_TOKEN" .env`) do set "BOOTSTRAP=%%b"
if defined BOOTSTRAP goto have_token
echo [FAIL] .env has no usable ACCESS_TOKEN: the line is missing, or its value is empty.
echo        An empty ACCESS_TOKEN no longer means "no auth". /v1/admin/invites accepts
echo        only the bootstrap admin credential, so with none there is no admin at all
echo        and this request can never succeed.
echo        Fix: add ACCESS_TOKEN=your-credential to %cd%\.env, then RESTART the server
echo        (the credential is read once at startup and never hot-reloaded).
echo        What this credential is, and why it must never be given to a user:
echo        See the multi-user deployment section of the install guide under docs.
exit /b 1

:have_token
set "BODY=%TEMP%\make-invite-body.json"
set "CODE="
for /f "usebackq delims=" %%c in (`curl -s -o "%BODY%" -w "%%{http_code}" -X POST -H "Authorization: Bearer %BOOTSTRAP%" -H "Content-Type: application/json" -d "{\"max_uses\":1}" http://127.0.0.1:8000/v1/admin/invites`) do set "CODE=%%c"
if defined CODE goto have_code
echo [FAIL] curl returned no status code at all - is curl missing from PATH?
exit /b 1

:have_code
if not "%CODE%"=="000" goto check_code
echo [FAIL] cannot reach http://127.0.0.1:8000 - the server is not running, or not on 8000.
exit /b 1

:check_code
if "%CODE%"=="200" goto ok
if "%CODE%"=="401" goto unauthorized
if "%CODE%"=="403" goto forbidden
if "%CODE%"=="503" goto nocreds
echo [FAIL] server answered HTTP %CODE%. Body:
type "%BODY%" 2>nul
goto bail

:unauthorized
echo [FAIL] HTTP 401 - the server does not accept the ACCESS_TOKEN from .env.
echo        Most likely you edited .env but did not restart: the credential is loaded
echo        once at startup and never re-read while running.
echo        Also check the value has no quotes or leading spaces around the = sign.
goto bail

:forbidden
echo [FAIL] HTTP 403 - that credential is valid but is not an admin.
echo        ACCESS_TOKEN must be the bootstrap admin credential; do not substitute a
echo        user's personal invite-issued token here.
goto bail

:nocreds
echo [FAIL] HTTP 503 - the server says it has no credentials configured at all.
echo        .env does have an ACCESS_TOKEN, so this process is not reading the .env in
echo        the project root. Restart it from the project root.
goto bail

:ok
echo New invite code (send the line below to one person only, never to a group chat):
type "%BODY%"
echo.
echo Note: the same code also appears in GET /v1/admin/invites, which is also sensitive.

del "%BODY%" >nul 2>&1
endlocal
exit /b 0

rem bail 之前必须先落盘再收尾；本次修的原始缺陷是"失败也悄悄 return 0"，
rem 只改文案不改退出码，上层脚本与计划任务仍然分不出成与败。
:bail
del "%BODY%" >nul 2>&1
endlocal
exit /b 1
