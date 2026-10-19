Set shell = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
base = fs.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = base
shell.Run Chr(34) & base & "\dist-review\Speak\Speak.exe" & Chr(34), 1, False
