' SubHub — ярлык на рабочем столе (без PowerShell)
Option Explicit

Dim fso, shell, scriptDir, desktop, shortcut, icoPath

Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
desktop = shell.SpecialFolders("Desktop")

Set shortcut = shell.CreateShortcut(desktop & "\SubHub.lnk")
shortcut.TargetPath = "wscript.exe"
shortcut.Arguments = "//nologo """ & scriptDir & "\app_launch.vbs"""
shortcut.WorkingDirectory = scriptDir
shortcut.Description = "SubHub"

icoPath = scriptDir & "\assets\app.ico"
If Not fso.FileExists(icoPath) Then
    icoPath = scriptDir & "\app.ico"
End If
If fso.FileExists(icoPath) Then
    shortcut.IconLocation = icoPath & ",0"
End If

shortcut.Save

If fso.FileExists(desktop & "\SubHub.lnk") Then
    MsgBox "Ярлык SubHub создан на рабочем столе." & vbCrLf & vbCrLf & desktop & "\SubHub.lnk", vbInformation, "SubHub"
    WScript.Quit 0
Else
    MsgBox "Не удалось создать ярлык.", vbCritical, "SubHub"
    WScript.Quit 1
End If
