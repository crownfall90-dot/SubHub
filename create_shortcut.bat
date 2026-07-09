@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$root = '%~dp0'.TrimEnd('\'); ^
   $w = New-Object -ComObject WScript.Shell; ^
   $d = [Environment]::GetFolderPath('Desktop'); ^
   $s = $w.CreateShortcut((Join-Path $d 'SubHub.lnk')); ^
   $s.TargetPath = 'wscript.exe'; ^
   $s.Arguments = ('//nologo \"' + (Join-Path $root 'app_launch.vbs') + '\"'); ^
   $s.WorkingDirectory = $root; ^
   $s.Description = 'SubHub'; ^
   $ico = Join-Path $root 'assets\app.ico'; ^
   if (-not (Test-Path $ico)) { $ico = Join-Path $root 'app.ico' }; ^
   if (Test-Path $ico) { $s.IconLocation = ($ico + ',0') }; ^
   $s.Save()"
if exist "%USERPROFILE%\Desktop\SubHub.lnk" (
    echo Ярлык SubHub создан на рабочем столе.
) else (
    echo Не удалось создать ярлык.
)
pause
