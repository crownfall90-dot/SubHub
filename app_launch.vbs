' SubHub — тихий запуск GUI без консоли
Option Explicit

Dim fso, shell, scriptDir, appPy, pythonw, cmd, rc, logPath, errMsg

Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = scriptDir

appPy = scriptDir & "\app.py"
If Not fso.FileExists(appPy) Then
    MsgBox "Не найден app.py в " & scriptDir, vbCritical, "SubHub"
    WScript.Quit 1
End If

pythonw = ResolvePythonw()
If pythonw = "" Then
    MsgBox "Не найден pythonw.exe." & vbCrLf & _
           "Установите Python и отметьте «Add python.exe to PATH»," & vbCrLf & _
           "или запустите: app.bat --console", vbCritical, "SubHub"
    WScript.Quit 1
End If

' Полный путь + кавычки — путь с пробелами («master 3») не ломает cmd.
cmd = """" & pythonw & """ """ & appPy & """"
On Error Resume Next
rc = shell.Run(cmd, 0, False)
errMsg = Err.Description
On Error GoTo 0

If rc = 0 And errMsg = "" Then
    WScript.Quit 0
End If

logPath = scriptDir & "\data\launch_error.log"
On Error Resume Next
If Not fso.FolderExists(scriptDir & "\data") Then
    fso.CreateFolder scriptDir & "\data"
End If
WriteTextFile logPath, Now & " cmd=" & cmd & vbCrLf & "rc=" & rc & " err=" & errMsg & vbCrLf
On Error GoTo 0

MsgBox "Не удалось запустить SubHub." & vbCrLf & vbCrLf & _
       "Команда: " & cmd & vbCrLf & _
       "Ошибка: " & errMsg & vbCrLf & vbCrLf & _
       "Попробуйте: app.bat --console", vbCritical, "SubHub"
WScript.Quit 1


Function ResolvePythonw()
    Dim candidates, i, localApp, home
    ResolvePythonw = ""

    ' PATH: через where без видимого окна (0 = hidden)
    Dim whichOut, tmpFile, ts
    On Error Resume Next
    tmpFile = shell.ExpandEnvironmentStrings("%TEMP%") & "\subhub_where_pythonw.txt"
    shell.Run "cmd /c where pythonw > """ & tmpFile & """ 2>nul", 0, True
    If fso.FileExists(tmpFile) Then
        Set ts = fso.OpenTextFile(tmpFile, 1)
        whichOut = Trim(ts.ReadAll)
        ts.Close
        fso.DeleteFile tmpFile, True
    End If
    On Error GoTo 0
    If whichOut <> "" Then
        candidates = Split(Replace(whichOut, vbCr, ""), vbLf)
        For i = 0 To UBound(candidates)
            If fso.FileExists(Trim(candidates(i))) Then
                ResolvePythonw = Trim(candidates(i))
                Exit Function
            End If
        Next
    End If

    ' Типичные пути Windows
    localApp = shell.ExpandEnvironmentStrings("%LOCALAPPDATA%")
    home = shell.ExpandEnvironmentStrings("%USERPROFILE%")
    candidates = Array( _
        localApp & "\Python\bin\pythonw.exe", _
        localApp & "\Programs\Python\Python314\pythonw.exe", _
        localApp & "\Programs\Python\Python313\pythonw.exe", _
        localApp & "\Programs\Python\Python312\pythonw.exe", _
        localApp & "\Programs\Python\Python311\pythonw.exe", _
        "C:\Python314\pythonw.exe", _
        "C:\Python313\pythonw.exe", _
        "C:\Python312\pythonw.exe", _
        home & "\AppData\Local\Microsoft\WindowsApps\pythonw.exe" _
    )
    For i = 0 To UBound(candidates)
        If fso.FileExists(candidates(i)) Then
            ResolvePythonw = candidates(i)
            Exit Function
        End If
    Next
End Function


Sub WriteTextFile(path, text)
    Dim ts
    Set ts = fso.OpenTextFile(path, 2, True)
    ts.Write text
    ts.Close
End Sub
