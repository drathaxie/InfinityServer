<#
  scripts/db.ps1 — query / edit the LIVE InfinityServer Postgres with nothing installed locally.

  The database listens ONLY on the VM's localhost (by design, never exposed to the internet),
  so this runs `psql` ON the VM over SSH and streams the result back.

  Usage (from the repo root, in PowerShell):
    ./scripts/db.ps1                                             # interactive psql shell
    ./scripts/db.ps1 "SELECT id,name,access_level FROM characters ORDER BY id"
    ./scripts/db.ps1 -File .\scripts\sql\whoami.sql              # run a .sql file
    ./scripts/db.ps1 -Csv "SELECT * FROM characters" > chars.csv # CSV export

  Inside the interactive shell:  \dt  (list tables) · \d characters  (describe) · \q  (quit)

  Overrides (optional):  $env:INFINITY_SSH_KEY   $env:INFINITY_VM
#>
[CmdletBinding()]
param(
  [Parameter(Position = 0, ValueFromRemainingArguments = $true)] [string[]] $Query,
  [Parameter()] [string] $File,
  [switch] $Csv
)
$ErrorActionPreference = 'Stop'

$Key = if ($env:INFINITY_SSH_KEY) { $env:INFINITY_SSH_KEY } else { "$env:USERPROFILE\Downloads\ssh-key-2026-06-19.key" }
$Vm  = if ($env:INFINITY_VM)      { $env:INFINITY_VM }      else { 'ubuntu@130.162.189.229' }
if (-not (Test-Path $Key)) { throw "SSH key not found: $Key  (set `$env:INFINITY_SSH_KEY to override)" }

# Remote prelude: load creds from .pg.env, export PGPASSWORD, then connect psql to the local socket.
# No double-quotes anywhere in the remote string so PowerShell 5.1 doesn't mangle native-arg quoting;
# all the values (host/port/user/db/password) are space-free so they're safe unquoted in the remote sh.
$prelude = 'cd /opt/infinity/server && set -a && . ./.pg.env && set +a && export PGPASSWORD=$INFINITY_PG_PASSWORD && '
$psql    = 'psql -h $INFINITY_PG_HOST -p $INFINITY_PG_PORT -U $INFINITY_PG_USER -d $INFINITY_PG_DB'

$sqlText = $null
if     ($File)  { $sqlText = Get-Content -Raw -LiteralPath $File }
elseif ($Query) { $sqlText = ($Query -join ' ') }

if ($null -ne $sqlText) {
  $flags = '-v ON_ERROR_STOP=1 -P pager=off'
  if ($Csv) { $flags = "$flags --csv" }
  $sqlText | & ssh -i $Key -o StrictHostKeyChecking=no $Vm "$prelude$psql $flags"
} else {
  & ssh -t -i $Key -o StrictHostKeyChecking=no $Vm "$prelude exec $psql"
}
