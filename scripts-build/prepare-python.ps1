# ---------------------------------------------------------------------
# prepare-python.ps1 -- Windows companion to prepare-python.sh
#
# Downloads a portable Windows Python (python-build-standalone) and
# pre-installs WillettBot's runtime deps (pyautogui, pynput, pywin32)
# into it. The resulting bundled-python\python\ tree is packaged into
# the .exe by electron-builder via extraResources, so end users get a
# fully-working Python environment with ZERO setup -- no pip, no venv,
# no OS Python required on their machine.
#
# Run automatically before `npm run dist:win`. To force a rebuild,
# delete the bundled-python\ folder and re-run.
#
# Requirements:
#   - PowerShell 5.1 or later (default on Windows 10+).
#   - Windows 10 1803+ for built-in tar.exe (used to unpack tar.gz).
#   - Network access to GitHub (to download python-build-standalone).
#
# NOTE: keep this file PURE ASCII. PowerShell 5.1 reads scripts in the
# system codepage (typically Windows-1252) unless they have a UTF-8 BOM
# -- non-ASCII characters in comments will corrupt the parse and cause
# bizarre "missing closing brace" errors far away from the actual line.
# ---------------------------------------------------------------------
$ErrorActionPreference = 'Stop'

$BundleDir = "bundled-python"
$PyVersion = "3.12.7"
$PbsTag    = "20241008"
$Arch      = "x86_64"

$TarballName = "cpython-$PyVersion+$PbsTag-$Arch-pc-windows-msvc-install_only.tar.gz"
$TarballUrl  = "https://github.com/astral-sh/python-build-standalone/releases/download/$PbsTag/$TarballName"

# Idempotency: skip if the bundle is already in place AND the deps are
# pre-installed. To force a fresh download, delete bundled-python\ first.
$pythonExe       = Join-Path $BundleDir 'python\python.exe'
$pyautoguiMarker = Join-Path $BundleDir 'python\Lib\site-packages\pyautogui'
$pywin32Marker   = Join-Path $BundleDir 'python\Lib\site-packages\win32'

if ((Test-Path $pythonExe) -and (Test-Path $pyautoguiMarker) -and (Test-Path $pywin32Marker)) {
  Write-Host "[prepare-python] Bundle already exists at $BundleDir\python - skipping."
  Write-Host "[prepare-python] (Delete $BundleDir\ to force a fresh download.)"
  exit 0
}

# Clean slate -- wipe any partial extract from a previous failed run.
if (Test-Path $BundleDir) {
  Remove-Item -Recurse -Force (Join-Path $BundleDir 'python') -ErrorAction SilentlyContinue
}
New-Item -ItemType Directory -Force -Path $BundleDir | Out-Null

$tarballPath = Join-Path $BundleDir 'python.tar.gz'

Write-Host "[prepare-python] Downloading Python $PyVersion ($Arch, ~40MB) ..."
Write-Host "[prepare-python] $TarballUrl"
$prevProgress = $ProgressPreference
$ProgressPreference = 'SilentlyContinue'
try {
  Invoke-WebRequest -Uri $TarballUrl -OutFile $tarballPath -UseBasicParsing
} finally {
  $ProgressPreference = $prevProgress
}

Write-Host "[prepare-python] Extracting ..."
& tar.exe -xzf $tarballPath -C $BundleDir
if ($LASTEXITCODE -ne 0) {
  throw "tar.exe failed extracting $tarballPath (exit $LASTEXITCODE)"
}
Remove-Item $tarballPath

if (-not (Test-Path $pythonExe)) {
  Write-Error "[prepare-python] Expected $pythonExe after extract but it's missing."
  Get-ChildItem $BundleDir
  exit 1
}

Write-Host "[prepare-python] Installing pyautogui + pynput + pywin32 into bundled Python ..."
& $pythonExe -m pip install --quiet --disable-pip-version-check `
    pyautogui pynput pywin32
if ($LASTEXITCODE -ne 0) {
  throw "pip install failed (exit $LASTEXITCODE)"
}

# pywin32's post-install script registers the COM DLLs. Newer pywin32
# wheels handle this on import so the script may be missing -- only run
# when present, and don't fail the build if it hiccups.
$postInstall = Join-Path $BundleDir 'python\Scripts\pywin32_postinstall.py'
if (Test-Path $postInstall) {
  Write-Host "[prepare-python] Running pywin32_postinstall ..."
  & $pythonExe $postInstall -install -silent | Out-Null
}

Write-Host "[prepare-python] Done."
$total = (Get-ChildItem -Recurse (Join-Path $BundleDir 'python') | Measure-Object -Property Length -Sum).Sum
$mb = [math]::Round($total / 1MB, 1)
Write-Host "[prepare-python] Bundled Python tree: $mb MB"
Write-Host "[prepare-python] Ready at $pythonExe"
