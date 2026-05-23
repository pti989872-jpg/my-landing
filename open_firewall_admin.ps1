$ErrorActionPreference = "Stop"

$ruleName = "RetroBoard Live 8080"

$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
  [Security.Principal.WindowsBuiltInRole]::Administrator
)

if (-not $isAdmin) {
  Write-Host "This script must be run as Administrator."
  Write-Host "Right-click the file and choose 'Run with PowerShell as Administrator', or run PowerShell as Administrator and execute it."
  exit 1
}

$existingRule = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue

if ($existingRule) {
  Set-NetFirewallRule -DisplayName $ruleName -Enabled True -Direction Inbound -Action Allow -Profile Domain,Private
  Set-NetFirewallRule -DisplayName $ruleName -Protocol TCP -LocalPort 8080
} else {
  New-NetFirewallRule `
    -DisplayName $ruleName `
    -Direction Inbound `
    -Action Allow `
    -Protocol TCP `
    -LocalPort 8080 `
    -Profile Domain,Private | Out-Null
}

Write-Host "Firewall rule is ready: $ruleName"
Write-Host "Share this link with colleagues in the same network/VPN:"
Write-Host "http://10.19.84.38:8080/"
