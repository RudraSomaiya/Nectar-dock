' Starts Gizmo: the Nectar notch (if it isn't running) and the voice engine, with no console windows.
' Runs at sign-in (HKCU Run "NectarVoice") and from the Start menu shortcut "Gizmo".
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
here = fso.GetParentFolderName(WScript.ScriptFullName)
nectar = fso.GetParentFolderName(here) & "\src-tauri\target\release\nectar.exe"

Function Running(name)
    Set procs = GetObject("winmgmts:").ExecQuery("select ProcessId from Win32_Process where Name = '" & name & "'")
    Running = (procs.Count > 0)
End Function

If fso.FileExists(nectar) And Not Running("nectar.exe") Then
    sh.Run """" & nectar & """", 1, False
End If

' core.py refuses to start twice (single-instance lock), so this is safe to run again.
sh.Run """" & here & "\.venv\Scripts\pythonw.exe"" """ & here & "\core.py""", 0, False
