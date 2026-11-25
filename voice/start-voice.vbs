' Starts Nectar Voice in the background (no console window).
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
CreateObject("WScript.Shell").Run """" & here & "\.venv\Scripts\pythonw.exe"" """ & here & "\core.py""", 0, False
