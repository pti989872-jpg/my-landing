@echo off
net session >nul 2>&1
if %errorlevel% neq 0 (
  echo This file must be run as Administrator.
  echo Right-click it and choose "Run as administrator".
  pause
  exit /b 1
)

netsh advfirewall firewall show rule name="RetroBoard Live 8080" >nul 2>&1
if %errorlevel% equ 0 (
  netsh advfirewall firewall set rule name="RetroBoard Live 8080" new enable=yes >nul
) else (
  netsh advfirewall firewall add rule name="RetroBoard Live 8080" dir=in action=allow protocol=TCP localport=8080 profile=domain,private >nul
)

echo Firewall rule is ready: RetroBoard Live 8080
echo Share this link with colleagues in the same network/VPN:
echo http://10.19.84.38:8080/
pause
