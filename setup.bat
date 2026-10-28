@echo off
setlocal
cd /d "%~dp0"
title ClipForge setup
echo.
echo  ===  ClipForge setup  ===
echo.

rem ---- 1. Python ----------------------------------------------------------
set "PY="
where py >nul 2>nul && (py -3.12 -c "import sys" >nul 2>nul && set "PY=py -3.12")
if not defined PY where py >nul 2>nul && (py -3 -c "import sys; assert sys.version_info>=(3,10)" >nul 2>nul && set "PY=py -3")
if not defined PY where python >nul 2>nul && (python -c "import sys; assert sys.version_info>=(3,10)" >nul 2>nul && set "PY=python")
if not defined PY (
  echo [1/6] Installing Python 3.12 with winget...
  winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
  set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
)
echo [1/6] Using Python: %PY%

rem ---- 2. Virtual environment + packages ----------------------------------
if not exist ".venv\Scripts\python.exe" (
  %PY% -m venv .venv || goto :fail
)
echo [2/6] Installing packages (PySide6, numpy, soundcard)...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :fail

rem ---- 3. FFmpeg (BtbN build: has ddagrab + NVENC/AMF/QSV) ----------------
if exist "ffmpeg\bin\ffmpeg.exe" (
  echo [3/6] FFmpeg already present.
) else (
  echo [3/6] Downloading FFmpeg...
  powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$ProgressPreference='SilentlyContinue';" ^
    "Invoke-WebRequest 'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip' -OutFile ffmpeg.zip;" ^
    "Expand-Archive ffmpeg.zip -DestinationPath ffmpeg_tmp -Force;" ^
    "Move-Item (Get-ChildItem ffmpeg_tmp -Directory | Select-Object -First 1).FullName ffmpeg;" ^
    "Remove-Item ffmpeg.zip; Remove-Item ffmpeg_tmp -Recurse -Force" || goto :fail
)

rem ---- 4. Optional: auto captions ------------------------------------------
echo.
choice /C YN /M "[4/6] Install auto-captions (faster-whisper, ~1 GB with the model, uses your GPU)?"
if errorlevel 2 goto :ollama
".venv\Scripts\python.exe" -m pip install faster-whisper nvidia-cublas-cu12 nvidia-cudnn-cu12

:ollama
rem ---- 5. Optional: local AI writer -----------------------------------------
echo.
choice /C YN /M "[5/6] Install the local AI writer (Ollama + a 5 GB model; writes hooks, titles and hashtags offline)?"
if errorlevel 2 goto :caster
where ollama >nul 2>nul || winget install -e --id Ollama.Ollama --accept-package-agreements --accept-source-agreements
if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" (
  "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" pull llama3.1:8b
) else (
  ollama pull llama3.1:8b
)

:caster
rem ---- 6. Optional: AI caster voice -----------------------------------------
echo.
choice /C YN /M "[6/6] Install the AI caster voice (Piper neural voice, ~100 MB; otherwise the Windows voice is used)?"
if errorlevel 2 goto :shortcut
".venv\Scripts\python.exe" -m pip install piper-tts
".venv\Scripts\python.exe" -m piper.download_voices en_US-ryan-high --data-dir "%APPDATA%\ClipForge\voices"

:shortcut
powershell -NoProfile -Command ^
  "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\ClipForge.lnk');" ^
  "$s.TargetPath='wscript.exe'; $s.Arguments='\"%~dp0ClipForge.vbs\"'; $s.WorkingDirectory='%~dp0';" ^
  "$s.IconLocation='%SystemRoot%\System32\imageres.dll,18'; $s.Save()"
echo.
echo  Done! Start ClipForge from the desktop shortcut (or double-click ClipForge.vbs).
echo  Tip: quit SteelSeries GG Moments first - both apps want the PageUp key.
echo.
pause
exit /b 0

:fail
echo.
echo  Setup failed - scroll up for the error. Run "run_debug.bat" after fixing it.
pause
exit /b 1
