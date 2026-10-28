' Starts ClipForge as administrator: Windows then lets it give its capture top GPU priority,
' which stops stutter in games that use 100% of the GPU. Windows will ask for permission.
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
py = ""
appdir = here
cands = Array(here, here & "\ClipForge", fso.GetParentFolderName(here))
For Each c In cands
  If c <> "" Then
    If fso.FileExists(c & "\.venv\Scripts\pythonw.exe") And fso.FolderExists(c & "\clipforge") Then
      py = c & "\.venv\Scripts\pythonw.exe"
      appdir = c
      Exit For
    End If
  End If
Next
If py = "" Then
  r = MsgBox("ClipForge isn't set up in this folder yet (or its setup files were removed)." & vbCrLf & vbCrLf & _
             "Run setup now? It takes a couple of minutes. Your clips and settings are kept.", _
             vbYesNo + vbQuestion, "ClipForge")
  If r = vbYes Then CreateObject("WScript.Shell").Run """" & here & "\setup.bat""", 1, False
  WScript.Quit
End If
Set app = CreateObject("Shell.Application")
app.ShellExecute py, "-m clipforge", appdir, "runas", 0
