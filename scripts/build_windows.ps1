# Build the Windows onedir release folder (run on Windows).
# Usage: powershell -ExecutionPolicy Bypass -File scripts/build_windows.ps1
$ErrorActionPreference = "Stop"
# CI captures output through a pipe, where Windows Python defaults to cp1252 and crashes on any
# non-ASCII character it prints. UTF-8 mode avoids that for every python call below.
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
Set-Location (Split-Path $PSScriptRoot -Parent)
function Note($msg) {
  Write-Host $msg
  if ($env:GITHUB_ACTIONS) { Write-Host ("::notice title=Build::" + $msg) }
}
function Fail($msg) {
  if ($env:GITHUB_ACTIONS) { Write-Host ("::error title=Build::" + ($msg -replace "`r?`n", "%0A")) }
  Write-Error $msg
  exit 1
}

Note "==> Fetching pinned FFmpeg"
python scripts/fetch_ffmpeg.py
if ($LASTEXITCODE -ne 0) { Fail "fetch_ffmpeg.py failed ($LASTEXITCODE)" }

Note "==> Installing build deps"
python -m pip install --upgrade pip
python -m pip install -r requirements.lock
python -m pip install pyinstaller

Note "==> PyInstaller"
python -m PyInstaller --noconfirm ClipForge.spec
if ($LASTEXITCODE -ne 0) { Fail "PyInstaller failed ($LASTEXITCODE)" }

$out = "dist\ClipForge"
if (-not (Test-Path "$out\ClipForge.exe")) {
  Write-Error "Missing $out\ClipForge.exe"
  exit 1
}

Note "==> Vendor ffmpeg next to exe"
if (Test-Path "$out\ffmpeg") { Remove-Item -Recurse -Force "$out\ffmpeg" }
Copy-Item -Recurse "ffmpeg" "$out\ffmpeg"

Note "==> Version + licenses"
$ver = python -c "import clipforge; print(clipforge.__version__)"
Set-Content -Path "$out\VERSION" -Value $ver -NoNewline
Copy-Item -Force LICENSE "$out\LICENSE"
Copy-Item -Force THIRD_PARTY_NOTICES.md "$out\THIRD_PARTY_NOTICES.md"
if (Test-Path CHANGELOG.md) { Copy-Item -Force CHANGELOG.md "$out\CHANGELOG.md" }
if (Test-Path licenses) {
  if (Test-Path "$out\licenses") { Remove-Item -Recurse -Force "$out\licenses" }
  Copy-Item -Recurse licenses "$out\licenses"
}

Note "==> Self-test (window, video playback, bundled FFmpeg)"
$report = Join-Path $env:TEMP "clipforge-selftest.txt"
Remove-Item $report -ErrorAction SilentlyContinue
$p = Start-Process -FilePath "$out\ClipForge.exe" -ArgumentList "--selftest", "`"$report`"" -PassThru
$null = $p.Handle   # keeps ExitCode readable after the process ends
if (-not $p.WaitForExit(180000)) {
  $p.Kill()
  $sofar = if (Test-Path $report) { (Get-Content $report -Raw) } else { "(no report written - it never started)" }
  Fail ("Self-test timed out (3 min). Progress:`n" + $sofar)
}
$text = if (Test-Path $report) { (Get-Content $report -Raw) } else { "(no report written)" }
Write-Host $text
if ($p.ExitCode -ne 0) {
  $logs = ""
  foreach ($f in "crash.log", "clipforge.log") {
    $lp = Join-Path $env:APPDATA "ClipForge\$f"
    if (Test-Path $lp) { $logs += "`n--- $f ---`n" + ((Get-Content $lp -Tail 25) -join "`n") }
  }
  Fail ("Self-test failed (exit $($p.ExitCode)):`n" + $text + $logs)
}
Note ("Self-test: " + ((($text -split "`n") | Where-Object { $_ -match '^(ok|PASS)' }) -join " | "))

Note "==> Update test (old copy updates itself from a zip while running)"
$old = Join-Path $env:TEMP "cf_update_old"
$pkg = Join-Path $env:TEMP "cf_update_pkg"
foreach ($d in $old, $pkg) { if (Test-Path $d) { Remove-Item -Recurse -Force $d } }
Copy-Item -Recurse $out $old
Set-Content -Path "$old\VERSION" -Value "0.0.1" -NoNewline
New-Item -ItemType Directory -Force "$pkg\ClipForge-9.9.9-win64" | Out-Null
Copy-Item -Recurse "$out\*" "$pkg\ClipForge-9.9.9-win64\"
Set-Content -Path "$pkg\ClipForge-9.9.9-win64\VERSION" -Value "9.9.9" -NoNewline
Compress-Archive -Path "$pkg\ClipForge-9.9.9-win64" -DestinationPath "$pkg\update.zip" -Force
$report2 = Join-Path $env:TEMP "clipforge-selftest-after-update.txt"
Remove-Item $report2 -ErrorAction SilentlyContinue
$u = Start-Process -FilePath "$old\ClipForge.exe" -ArgumentList "--install-update", "`"$pkg\update.zip`"", "--then", "--selftest", "`"$report2`"" -PassThru
$null = $u.Handle
if (-not $u.WaitForExit(120000)) { $u.Kill(); Fail "Update test: --install-update hung" }
if ($u.ExitCode -ne 0) { Fail "Update test: --install-update exited $($u.ExitCode)" }
$deadline = (Get-Date).AddSeconds(240)
while ((Get-Date) -lt $deadline -and -not ((Test-Path $report2) -and ((Get-Content $report2 -Raw) -match '^(PASS|FAIL)'))) { Start-Sleep 2 }
$after = if (Test-Path $report2) { Get-Content $report2 -Raw } else { "(the updated ClipForge never ran)" }
$newver = (Get-Content "$old\VERSION" -Raw).Trim()
$result = Join-Path $env:APPDATA "ClipForge\update_result.txt"
$res = if (Test-Path $result) { (Get-Content $result -Raw).Trim() } else { "(no result file)" }
if ($newver -ne "9.9.9" -or $after -notmatch '^PASS') {
  Fail ("Update test failed: VERSION after update = $newver, helper said: $res`n" + $after)
}
Note "Update test: swapped to $newver while running, restarted, self-test PASS ($res)"

$mb = [math]::Round(((Get-ChildItem $out -Recurse -File | Measure-Object Length -Sum).Sum) / 1MB)
Note "Built: $out\ClipForge.exe (version $ver, $mb MB unpacked)"
