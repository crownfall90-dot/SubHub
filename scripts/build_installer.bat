@echo off
chcp 65001 >nul 2>&1
setlocal EnableExtensions
cd /d "%~dp0.."

set "CSC=%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if not exist "%CSC%" set "CSC=%WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe"
if not exist "%CSC%" set "CSC="

set "ISCC="
if exist "%LocalAppData%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"

if not exist "SubHub.exe" call scripts\build_subhub_exe.bat
if not exist "SubHub.exe" (
  echo SubHub.exe missing
  exit /b 1
)

if not exist "dist" mkdir dist

echo [1/3] Packaging payload...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0package_payload.ps1"
if errorlevel 1 exit /b 1

set "VER=1.4.0"
if exist "VERSION" (
  set /p VER=<VERSION
)

set "OUT=dist\SubHub-Setup-%VER%.exe"
set "ICO=assets\app.ico"
if not exist "%ICO%" set "ICO=SubHub.exe"

if defined ISCC (
  echo [2/3] Inno Setup...
  "%ISCC%" /DAppVer=%VER% "%~dp0SubHub.iss"
  if not errorlevel 1 (
    copy /Y "dist\SubHub-Setup-%VER%.exe" "dist\SubHub-Setup.exe" >nul 2>&1
    echo OK: dist\SubHub-Setup-%VER%.exe
    exit /b 0
  )
  echo Inno failed — C# setup fallback
)

if not defined CSC (
  echo csc.exe not found
  exit /b 1
)
if not exist "dist\payload.zip" (
  echo dist\payload.zip missing
  exit /b 1
)

echo [2/3] Compiling SubHub-Setup.exe ...
"%CSC%" /nologo /target:winexe /platform:anycpu /optimize+ /win32icon:"%ICO%" /r:System.Windows.Forms.dll /r:System.Drawing.dll /r:System.Management.dll /r:System.IO.Compression.FileSystem.dll /r:System.IO.Compression.dll /resource:"dist\payload.zip",payload.zip /resource:"VERSION",VERSION /out:"%OUT%" "scripts\SubHubSetup.cs"
if errorlevel 1 (
  echo Setup build failed
  exit /b 1
)

copy /Y "%OUT%" "dist\SubHub-Setup.exe" >nul
echo [3/3] OK: %CD%\%OUT%
echo      %CD%\dist\SubHub-Setup.exe
exit /b 0