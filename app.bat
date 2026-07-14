@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

if /I "%~1"=="--console" goto console

if exist "%~dp0SubHub.exe" (
  start "" "%~dp0SubHub.exe"
  exit /b 0
)
wscript.exe //nologo "%~dp0app_launch.vbs"
exit /b 0

:console
:loop
python "%~dp0app.py"
set "EX=%errorlevel%"

if "%EX%"=="42" goto restart
if not "%EX%"=="0" pause
goto :eof

:restart
ping -n 2 127.0.0.1 >nul 2>&1
goto loop
