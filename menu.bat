@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

:loop
cmd /d /c python menu.py
set "EX=%errorlevel%"

if "%EX%"=="42" goto restart
if not "%EX%"=="0" goto on_error
goto :eof

:restart
ping -n 2 127.0.0.1 >nul 2>&1
goto loop

:on_error
echo.
echo  Python error (code %EX%). Press any key...
pause >nul
