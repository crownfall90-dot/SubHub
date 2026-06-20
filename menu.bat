@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

:loop
python menu.py
set "EX=%errorlevel%"

if "%EX%"=="42" goto restart
if not "%EX%"=="0" goto on_error
goto :eof

:restart
echo.
echo  Update applied. Restarting...
ping -n 3 127.0.0.1 >nul 2>&1
goto loop

:on_error
echo.
echo  Python error (code %EX%). Press any key...
pause >nul
