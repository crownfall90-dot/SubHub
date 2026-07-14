@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0.."

set "CSC="
if exist "%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe" (
  set "CSC=%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
) else if exist "%WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe" (
  set "CSC=%WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe"
)

if "%CSC%"=="" (
  echo csc.exe not found. Install .NET Framework 4.x Developer Pack.
  exit /b 1
)

set "ICO=assets\app.ico"
if not exist "%ICO%" set "ICO=app.ico"
if not exist "%ICO%" (
  echo Icon not found: assets\app.ico
  exit /b 1
)

"%CSC%" /nologo /target:winexe /platform:anycpu /optimize+ ^
  /win32icon:"%ICO%" ^
  /r:System.Windows.Forms.dll /r:System.Drawing.dll ^
  /out:"SubHub.exe" ^
  "scripts\SubHubLauncher.cs"

if errorlevel 1 (
  echo Build failed.
  exit /b 1
)

echo OK: %CD%\SubHub.exe
exit /b 0
