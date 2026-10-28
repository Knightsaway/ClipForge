' Starts ClipForge without a console window.
' Finds ClipForge's Python environment (.venv); if it's missing, offers to run setup.bat.
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
here = fso.GetParentFolderName(WScript.ScriptFullName)
args = ""
For Each a In WScript.Arguments
  args = args & " " & a
Next

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
  If r = vbYes Then
    sh.CurrentDirectory = here
    sh.Run """" & here & "\setup.bat""", 1, False
  End If
  WScript.Quit
End If

sh.CurrentDirectory = appdir
sh.Run """" & py & """ -m clipforge" & args, 0, False
