# Build the Windows onedir release folder (run on Windows).
# Usage: powershell -ExecutionPolicy Bypass -File scripts/build_windows.ps1
$ErrorActionPreference = "Stop"
# CI captures output through a pipe, where Windows Python defaults to cp1252 and crashes on any
# non-ASCII character it prints. UTF-8 mode avoids that for every python call below.
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
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

Write-Host "==> Self-test (window, video playback, bundled FFmpeg)"
$report = Join-Path $env:TEMP "clipforge-selftest.txt"
Remove-Item $report -ErrorAction SilentlyContinue
$p = Start-Process -FilePath "$out\ClipForge.exe" -ArgumentList "--selftest", "`"$report`"" -PassThru
$null = $p.Handle   # keeps ExitCode readable after the process ends
if (-not $p.WaitForExit(180000)) { $p.Kill(); Write-Error "Self-test timed out"; exit 1 }
if (Test-Path $report) { Get-Content $report; if ($env:GITHUB_ACTIONS) { "::notice title=Self-test::" + ((Get-Content $report | Where-Object { $_ -match '^(ok|FAIL|PASS)' }) -join ' | ') } }
if ($p.ExitCode -ne 0) { Write-Error "Self-test failed (exit $($p.ExitCode))"; exit 1 }

$mb = [math]::Round(((Get-ChildItem $out -Recurse -File | Measure-Object Length -Sum).Sum) / 1MB)
Write-Host "Built: $out\ClipForge.exe (version $ver, $mb MB unpacked)"
