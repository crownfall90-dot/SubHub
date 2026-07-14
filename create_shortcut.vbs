' SubHub — ярлык на рабочем столе (предпочитает SubHub.exe в папке приложения)
Option Explicit

Dim fso, shell, scriptDir, desktop, shortcut, icoPath, target, args, silent

Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
desktop = shell.SpecialFolders("Desktop")

Set shortcut = shell.CreateShortcut(desktop & "\SubHub.lnk")
If fso.FileExists(scriptDir & "\SubHub.exe") Then
    target = scriptDir & "\SubHub.exe"
    args = ""
Else
    target = "wscript.exe"
    args = "//nologo """ & scriptDir & "\app_launch.vbs"""
End If
shortcut.TargetPath = target
shortcut.Arguments = args
shortcut.WorkingDirectory = scriptDir
shortcut.Description = "SubHub"

icoPath = scriptDir & "\assets\app.ico"
If Not fso.FileExists(icoPath) Then
    icoPath = scriptDir & "\app.ico"
End If
If fso.FileExists(scriptDir & "\SubHub.exe") Then
    shortcut.IconLocation = scriptDir & "\SubHub.exe,0"
ElseIf fso.FileExists(icoPath) Then
    shortcut.IconLocation = icoPath & ",0"
End If

shortcut.Save

silent = False
If WScript.Arguments.Count > 0 Then
    If LCase(WScript.Arguments(0)) = "/silent" Then silent = True
End If

If fso.FileExists(desktop & "\SubHub.lnk") Then
    If Not silent Then
        MsgBox "Ярлык SubHub создан на рабочем столе." & vbCrLf & vbCrLf & desktop & "\SubHub.lnk", vbInformation, "SubHub"
    End If
    WScript.Quit 0
Else
    If Not silent Then
        MsgBox "Не удалось создать ярлык.", vbCritical, "SubHub"
    End If
    WScript.Quit 1
End If
