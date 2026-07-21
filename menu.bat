@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

rem --- Найти настоящий python (не заглушку Microsoft Store) ---
set "PYEXE="
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python*") do if exist "%%D\python.exe" set "PYEXE=%%D\python.exe"
if not defined PYEXE for /f "delims=" %%P in ('where python 2^>nul') do if not defined PYEXE set "PYEXE=%%P"
if not defined PYEXE (
  echo Python не найден. Установите Python 3.10+ и отметьте "Add Python to PATH".
  pause
  exit /b 1
)

:loop
"%PYEXE%" menu.py
set "EX=%errorlevel%"

if "%EX%"=="42" goto restart
if not "%EX%"=="0" goto on_error
goto :eof

:restart
ping -n 2 127.0.0.1 >nul 2>&1
goto loop

:on_error
echo.
echo  Python завершился с ошибкой (код %EX%).
ping -n 3 127.0.0.1 >nul 2>&1
