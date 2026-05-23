$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$data = Join-Path $root "data"
$pidFile = Join-Path $data "server.pid"
$outLog = Join-Path $root "server.out.log"
$errLog = Join-Path $root "server.err.log"

if (!(Test-Path -LiteralPath $data)) {
  New-Item -ItemType Directory -Path $data | Out-Null
}

if (Test-Path -LiteralPath $pidFile) {
  $oldPid = Get-Content -LiteralPath $pidFile -Raw
  $oldProcess = Get-Process -Id $oldPid -ErrorAction SilentlyContinue
  if ($oldProcess) {
    Write-Host "RetroBoard is already running on http://localhost:8080"
    exit 0
  }
}

$python = (Get-Command python).Source
$process = Start-Process -FilePath $python -ArgumentList "-u", "server.py" -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput $outLog -RedirectStandardError $errLog -PassThru
$process.Id | Set-Content -LiteralPath $pidFile

Start-Sleep -Seconds 1
Write-Host "RetroBoard started"
Write-Host "Local:   http://localhost:8080"
Write-Host "Network: check server.out.log for the current IP address"
Write-Host "Code:    retro2026"
