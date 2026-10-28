@echo off
rem Build the standalone Windows folder (ClipForge.exe + ffmpeg). Prefer CI Releases for users.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\build_windows.ps1"
echo.
echo Output: dist\ClipForge\ClipForge.exe
pause
