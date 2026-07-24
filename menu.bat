@echo off
cd /d "%~dp0"
chcp 65001 >nul 2>&1
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

rem --- find real python.exe (not the Microsoft Store stub) ---
set "PYEXE="
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python*") do if exist "%%D\python.exe" set "PYEXE=%%D\python.exe"
if not defined PYEXE for /f "delims=" %%P in ('where python 2^>nul') do if not defined PYEXE set "PYEXE=%%P"
if not defined PYEXE (
  echo Python not found. Install Python 3.10+ and check "Add Python to PATH".
  pause
  exit /b 1
)

:loop
"%PYEXE%" -m subhub
set "EX=%errorlevel%"

if "%EX%"=="42" goto restart
if not "%EX%"=="0" goto on_error
goto :eof

:restart
ping -n 2 127.0.0.1 >nul 2>&1
goto loop

:on_error
echo.
echo  Python exited with an error (code %EX%).
ping -n 3 127.0.0.1 >nul 2>&1
goto :eof
