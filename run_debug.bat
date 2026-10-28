@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo ClipForge isn't set up in this folder yet ^(the .venv folder is missing^).
  echo Running setup.bat now - your clips and settings are kept.
  echo.
  call setup.bat
)
echo Running ClipForge with a console so you can see errors...
".venv\Scripts\python.exe" -m clipforge
echo.
echo Log files: %APPDATA%\ClipForge\clipforge.log and crash.log
pause
