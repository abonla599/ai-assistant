@echo off
rem 生成一个邀请码。口令从 .env 读，避免手抄泄露。
setlocal
cd /d C:\Users\34426\ai-assistant
rem 只切第一个等号（tokens=1,*）。个人令牌与 bootstrap 口令都是 urlsafe base64，
rem 值里可能带 "=" 补位：写成 %BOOTSTRAP:ACCESS_TOKEN=% 会把整行前缀删成
rem "=<口令>="（两头各留一个等号），实测服务端回 401 缺少或错误的访问凭据。
for /f "usebackq tokens=1,* delims==" %%a in (`findstr /b ACCESS_TOKEN .env`) do set BOOTSTRAP=%%b
curl -s -X POST -H "Authorization: Bearer %BOOTSTRAP%" ^
     -H "Content-Type: application/json" ^
     -d "{\"max_uses\":1}" http://127.0.0.1:8000/v1/admin/invites
echo.
endlocal
