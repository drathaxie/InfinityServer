<#
  scripts/db-tunnel.ps1 — open an SSH tunnel so a LOCAL GUI tool (DBeaver, pgAdmin, TablePlus,
  DataGrip) can browse/edit the live Postgres with a real table UI.

  Forwards  127.0.0.1:5433 (your PC)  ->  127.0.0.1:5432 (the VM's Postgres).
  Leave this window open while you use the GUI; Ctrl+C closes the tunnel.

  Then point your GUI at:
      Host: 127.0.0.1   Port: 5433   Database: infinity   User: infinity
      Password: run  ./scripts/db.ps1 "SHOW server_version"  works without it, but the GUI needs
                the password from the VM's /opt/infinity/server/.pg.env  (INFINITY_PG_PASSWORD=...)

  Overrides: $env:INFINITY_SSH_KEY, $env:INFINITY_VM, -LocalPort
#>
param([int]$LocalPort = 5433)
$ErrorActionPreference = 'Stop'
$Key = if ($env:INFINITY_SSH_KEY) { $env:INFINITY_SSH_KEY } else { "$env:USERPROFILE\Downloads\ssh-key-2026-06-19.key" }
$Vm  = if ($env:INFINITY_VM)      { $env:INFINITY_VM }      else { 'ubuntu@130.162.189.229' }
if (-not (Test-Path $Key)) { throw "SSH key not found: $Key" }

Write-Host "Tunnel up: 127.0.0.1:$LocalPort  ->  live Postgres (infinity).  Ctrl+C to close." -ForegroundColor Cyan
Write-Host "GUI connect ->  host=127.0.0.1  port=$LocalPort  db=infinity  user=infinity" -ForegroundColor Cyan
Write-Host "Password is INFINITY_PG_PASSWORD in /opt/infinity/server/.pg.env on the VM." -ForegroundColor DarkGray
& ssh -i $Key -o StrictHostKeyChecking=no -N -L "${LocalPort}:127.0.0.1:5432" $Vm
