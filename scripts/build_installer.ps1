# Build the Windows installer from dist\ClipForge (run build_windows.ps1 first). Needs Inno Setup 6;
# installs it with Chocolatey when ISCC.exe isn't found (GitHub's Windows runners have choco).
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not (Test-Path "dist\ClipForge\ClipForge.exe")) { Write-Error "Run scripts\build_windows.ps1 first"; exit 1 }
$ver = (Get-Content "dist\ClipForge\VERSION" -Raw).Trim()

$iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
          "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) {
  Write-Host "==> Installing Inno Setup"
  choco install innosetup -y --no-progress | Out-Null
  $iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
}
New-Item -ItemType Directory -Force dist_release | Out-Null
& $iscc "/DAppVersion=$ver" "installer\ClipForge.iss"
if ($LASTEXITCODE -ne 0) { Write-Error "ISCC failed ($LASTEXITCODE)"; exit 1 }
$setup = "dist_release\ClipForge-$ver-setup.exe"
if (-not (Test-Path $setup)) { Write-Error "Missing $setup"; exit 1 }

Write-Host "==> Installer test (silent install, self-test, silent uninstall)"
$dir = Join-Path $env:TEMP "cf_install_test"
if (Test-Path $dir) { Remove-Item -Recurse -Force $dir }
$i = Start-Process $setup -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CURRENTUSER", "/DIR=`"$dir`"" -PassThru -Wait
if ($i.ExitCode -ne 0 -or -not (Test-Path "$dir\ClipForge.exe")) { Write-Error "Silent install failed ($($i.ExitCode))"; exit 1 }
$rep = Join-Path $env:TEMP "cf_install_selftest.txt"
Remove-Item $rep -ErrorAction SilentlyContinue
$p = Start-Process "$dir\ClipForge.exe" -ArgumentList "--selftest", "`"$rep`"" -PassThru
$null = $p.Handle
if (-not $p.WaitForExit(180000)) { $p.Kill(); Write-Error "Installed ClipForge self-test timed out"; exit 1 }
if ($p.ExitCode -ne 0) { Get-Content $rep -ErrorAction SilentlyContinue; Write-Error "Installed ClipForge self-test failed"; exit 1 }
$u = Start-Process "$dir\unins000.exe" -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART" -PassThru -Wait
Start-Sleep 3
if (Test-Path "$dir\ClipForge.exe") { Write-Error "Uninstall left ClipForge.exe behind"; exit 1 }
$mb = [math]::Round((Get-Item $setup).Length / 1MB)
$msg = "Installer: $setup ($mb MB) installs, passes self-test, uninstalls cleanly"
Write-Host $msg
if ($env:GITHUB_ACTIONS) { Write-Host "::notice title=Installer::$msg" }
