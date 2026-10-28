@echo off
rem Optional: bundle ClipForge into a standalone folder with ClipForge.exe (no Python needed to run it).
cd /d "%~dp0"
".venv\Scripts\python.exe" -m pip install pyinstaller
".venv\Scripts\pyinstaller.exe" --noconfirm --windowed --name ClipForge --add-data "ffmpeg\bin;ffmpeg\bin" -p . clipforge\__main__.py
echo Built: dist\ClipForge\ClipForge.exe
pause
