Option Explicit

Dim fso, shell, scriptPath, scriptDir, rootDir, ps1Path, flowName, commandTemplate, command, exitCode
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

scriptPath = WScript.ScriptFullName
scriptDir = fso.GetParentFolderName(scriptPath)
rootDir = fso.GetParentFolderName(scriptDir)
flowName = GetConfigString(rootDir, "flows", "web_ui", "web-ui")
ps1Path = ExpandConfigPath(rootDir, GetConfigString(rootDir, "web_ui_schedule", "powershell_wrapper", "{project_root}/scripts/web-ui.ps1"))
commandTemplate = GetConfigString(rootDir, "web_ui_schedule", "hidden_powershell_command_template", "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File {script}")

LogWrite rootDir, flowName, "INFO", "silent web ui launcher started"

If Not fso.FileExists(ps1Path) Then
    LogWrite rootDir, flowName, "ERROR", "web ui PowerShell wrapper missing path=" & ps1Path
    WScript.Quit 2
End If

command = Replace(commandTemplate, "{script}", Quote(ps1Path))
exitCode = shell.Run(command, 0, True)

If exitCode = 0 Then
    LogWrite rootDir, flowName, "OK", "silent web ui launcher finished exit_code=0"
Else
    LogWrite rootDir, flowName, "ERROR", "silent web ui launcher finished exit_code=" & CStr(exitCode)
End If

WScript.Quit exitCode

Function Quote(value)
    Quote = Chr(34) & value & Chr(34)
End Function

Function ExpandConfigPath(rootPath, value)
    Dim expanded
    expanded = Replace(value, "/", "\")
    expanded = Replace(expanded, "{project_root}", rootPath)
    ExpandConfigPath = expanded
End Function

Function GetConfigString(rootPath, sectionName, keyName, fallback)
    On Error Resume Next
    Dim configPath, file, line, trimmed, inSection, eqIndex, key, value
    GetConfigString = fallback
    configPath = fso.BuildPath(fso.BuildPath(rootPath, "config"), "settings.toml")
    If Not fso.FileExists(configPath) Then Exit Function

    Set file = fso.OpenTextFile(configPath, 1, False)
    inSection = False
    Do Until file.AtEndOfStream
        line = file.ReadLine
        trimmed = Trim(line)
        If Len(trimmed) > 0 And Left(trimmed, 1) <> "#" Then
            If Left(trimmed, 1) = "[" And Right(trimmed, 1) = "]" Then
                inSection = (Mid(trimmed, 2, Len(trimmed) - 2) = sectionName)
            ElseIf inSection Then
                eqIndex = InStr(trimmed, "=")
                If eqIndex > 0 Then
                    key = Trim(Left(trimmed, eqIndex - 1))
                    If key = keyName Then
                        value = Trim(Mid(trimmed, eqIndex + 1))
                        If Left(value, 1) = """" And Right(value, 1) = """" Then
                            value = Mid(value, 2, Len(value) - 2)
                        End If
                        GetConfigString = value
                        file.Close
                        Exit Function
                    End If
                End If
            End If
        End If
    Loop
    file.Close
    On Error GoTo 0
End Function

Sub EnsureFolder(path)
    If fso.FolderExists(path) Then Exit Sub
    Dim parent
    parent = fso.GetParentFolderName(path)
    If Len(parent) > 0 And Not fso.FolderExists(parent) Then EnsureFolder parent
    fso.CreateFolder path
End Sub

Function Timestamp()
    Timestamp = Year(Now) & "-" & Right("0" & Month(Now), 2) & "-" & Right("0" & Day(Now), 2) & " " & Right("0" & Hour(Now), 2) & ":" & Right("0" & Minute(Now), 2) & ":" & Right("0" & Second(Now), 2)
End Function

Sub AppendLine(path, line)
    Dim file
    Set file = fso.OpenTextFile(path, 8, True)
    file.WriteLine line
    file.Close
End Sub

Sub LogWrite(rootPath, flow, level, message)
    On Error Resume Next
    Dim unifiedDir, vbsDir, unifiedLine, vbsLine
    unifiedDir = fso.BuildPath(fso.BuildPath(rootPath, "logs"), "unified")
    vbsDir = fso.BuildPath(fso.BuildPath(rootPath, "logs"), "vbs")
    EnsureFolder unifiedDir
    EnsureFolder vbsDir
    unifiedLine = "[" & Timestamp() & "] [VBS] [" & flow & "] [" & level & "] " & message
    vbsLine = "[" & Timestamp() & "] [" & level & "] " & message
    AppendLine fso.BuildPath(unifiedDir, flow & ".log"), unifiedLine
    AppendLine fso.BuildPath(unifiedDir, "_session.log"), unifiedLine
    AppendLine fso.BuildPath(vbsDir, flow & ".log"), vbsLine
    On Error GoTo 0
End Sub
