param(
  [ValidateSet('onedir','onefile')]
  [string]$Mode = 'onedir',
  [string]$OutDir = '',
  [string]$WorkDir = '',
  [switch]$NoClean,
  [bool]$Zip = $true,
  [string]$ZipNamePrefix = 'DBMEditor'
)

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path .\.venv\Scripts\python.exe)) {
  throw "Missing .venv. Create venv and install deps first."
}

$python = Resolve-Path .\.venv\Scripts\python.exe

& $python -m pip install -U pip
& $python -m pip install -U pyinstaller

if ([string]::IsNullOrWhiteSpace($OutDir)) {
  $OutDir = Join-Path $root 'dist'
} elseif (-not [System.IO.Path]::IsPathRooted($OutDir)) {
  $OutDir = Join-Path $root $OutDir
}

if ([string]::IsNullOrWhiteSpace($WorkDir)) {
  $WorkDir = Join-Path $root 'build'
} elseif (-not [System.IO.Path]::IsPathRooted($WorkDir)) {
  $WorkDir = Join-Path $root $WorkDir
}

New-Item -ItemType Directory -Force $OutDir | Out-Null
New-Item -ItemType Directory -Force $WorkDir | Out-Null

if (-not $NoClean) {
  Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $WorkDir
  Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $OutDir
  New-Item -ItemType Directory -Force $OutDir | Out-Null
  New-Item -ItemType Directory -Force $WorkDir | Out-Null
}

if ($Mode -eq 'onefile') {
  & $python -m PyInstaller -y --distpath $OutDir --workpath $WorkDir .\DBMEditor-onefile.spec
  $expected = Join-Path $OutDir 'DBMEditor.exe'
} else {
  & $python -m PyInstaller -y --distpath $OutDir --workpath $WorkDir .\DBMEditor.spec
  $expected = Join-Path $OutDir 'DBMEditor\DBMEditor.exe'
}

if (Test-Path $expected) {
  Write-Host "Done. Output: $expected"

  if ($Zip) {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $zipPath = Join-Path $OutDir ("{0}-{1}-{2}.zip" -f $ZipNamePrefix, $Mode, $stamp)
    if (Test-Path $zipPath) { Remove-Item -Force $zipPath }

    if ($Mode -eq 'onefile') {
      Compress-Archive -Path $expected -DestinationPath $zipPath
    } else {
      Compress-Archive -Path (Join-Path $OutDir 'DBMEditor') -DestinationPath $zipPath
    }

    Write-Host "Zip created: $zipPath"
  }
} else {
  Write-Warning "Build finished but expected exe missing: $expected"
  Write-Warning "If PyInstaller logs show it was created, Defender/AV may have quarantined it."
}
