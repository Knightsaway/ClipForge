# Build the Windows onedir release folder (run on Windows).
# Usage: powershell -ExecutionPolicy Bypass -File scripts/build_windows.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

Write-Host "==> Fetching pinned FFmpeg"
python scripts/fetch_ffmpeg.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "==> Installing build deps"
python -m pip install --upgrade pip
python -m pip install -r requirements.lock
python -m pip install pyinstaller

Write-Host "==> PyInstaller"
python -m PyInstaller --noconfirm ClipForge.spec
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$out = "dist\ClipForge"
if (-not (Test-Path "$out\ClipForge.exe")) {
  Write-Error "Missing $out\ClipForge.exe"
  exit 1
}

Write-Host "==> Vendor ffmpeg next to exe"
if (Test-Path "$out\ffmpeg") { Remove-Item -Recurse -Force "$out\ffmpeg" }
Copy-Item -Recurse "ffmpeg" "$out\ffmpeg"

Write-Host "==> Version + licenses"
$ver = python -c "import clipforge; print(clipforge.__version__)"
Set-Content -Path "$out\VERSION" -Value $ver -NoNewline
Copy-Item -Force LICENSE "$out\LICENSE"
Copy-Item -Force THIRD_PARTY_NOTICES.md "$out\THIRD_PARTY_NOTICES.md"
if (Test-Path CHANGELOG.md) { Copy-Item -Force CHANGELOG.md "$out\CHANGELOG.md" }
if (Test-Path licenses) {
  if (Test-Path "$out\licenses") { Remove-Item -Recurse -Force "$out\licenses" }
  Copy-Item -Recurse licenses "$out\licenses"
}

# Desktop shortcut helper (optional; users can pin the exe)
Write-Host "Built: $out\ClipForge.exe (version $ver)"
