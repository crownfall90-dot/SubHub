' Запуск SubHub без окна консоли
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = scriptDir

appPy = scriptDir & "\app.py"
pythonw = "pythonw.exe"

On Error Resume Next
shell.Run """" & pythonw & """ """ & appPy & """", 0, False
If Err.Number <> 0 Then
    Err.Clear
    shell.Run "python """ & appPy & """", 0, False
End If
