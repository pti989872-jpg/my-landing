$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$pidFile = Join-Path $root "data\server.pid"

if (!(Test-Path -LiteralPath $pidFile)) {
  Write-Host "No RetroBoard pid file found."
  exit 0
}

$pidValue = Get-Content -LiteralPath $pidFile -Raw
$process = Get-Process -Id $pidValue -ErrorAction SilentlyContinue

if ($process) {
  Stop-Process -Id $process.Id
  Write-Host "RetroBoard stopped."
} else {
  Write-Host "RetroBoard was not running."
}

Remove-Item -LiteralPath $pidFile -Force
