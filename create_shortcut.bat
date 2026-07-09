@echo off
cd /d "%~dp0"
cscript //nologo "%~dp0create_shortcut.vbs"
if errorlevel 1 echo Ne udalos sozdat yarlyk.
pause
