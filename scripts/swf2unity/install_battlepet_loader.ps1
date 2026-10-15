$ErrorActionPreference = 'Stop'
$workspaceRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$releaseRoot = Join-Path $workspaceRoot 'outputs/battlepet_forge_lab/live'
$payload = Get-Content -LiteralPath (Join-Path $releaseRoot 'deployment_payload.json') -Raw | ConvertFrom-Json
$source = Join-Path $releaseRoot 'loader/InfinityLoader.dll'
$gameRoot = 'C:/Program Files (x86)/Steam/steamapps/common/AdventureQuest Worlds Unity Playtest'
$target = [IO.Path]::GetFullPath((Join-Path $gameRoot 'InfinityLoader.dll'))
if ([IO.Path]::GetDirectoryName($target) -ne [IO.Path]::GetFullPath($gameRoot)) { throw 'Unexpected game path' }
$beforeHash = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
$afterHash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant()
if ($afterHash -ne $payload.loader_sha256) { throw 'Release loader hash changed' }
if ($beforeHash -eq $afterHash) { Write-Output 'Loader already installed'; exit 0 }
if ($beforeHash -ne '9a42a2fe94060a0dceae7e73a80180873912e134a94ddf241b8105fd3d77f0d2') { throw 'Installed loader changed; inspect before replacing' }
$backup = Join-Path $releaseRoot ('InfinityLoader.before-' + $beforeHash + '.dll')
if (Test-Path -LiteralPath $backup) {
    if ((Get-FileHash -LiteralPath $backup -Algorithm SHA256).Hash.ToLowerInvariant() -ne $beforeHash) { throw 'Backup mismatch' }
} else { Copy-Item -LiteralPath $target -Destination $backup }
if ((Get-FileHash -LiteralPath $backup -Algorithm SHA256).Hash.ToLowerInvariant() -ne $beforeHash) { throw 'Backup mismatch' }
$stage = $target + '.battlepet-new'
if (Test-Path -LiteralPath $stage) {
    if ((Get-FileHash -LiteralPath $stage -Algorithm SHA256).Hash.ToLowerInvariant() -ne $afterHash) { throw 'Existing staging file differs from release' }
} else { Copy-Item -LiteralPath $source -Destination $stage }
if ((Get-FileHash -LiteralPath $stage -Algorithm SHA256).Hash.ToLowerInvariant() -ne $afterHash) { throw 'Staging mismatch' }
if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant() -ne $beforeHash) { throw 'Installed loader changed during copy' }
[IO.File]::Replace($stage, $target, $backup, $true)
if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant() -ne $afterHash) { throw 'Installed verification failed' }
@{status='installed';target=$target;backup=$backup;sha256=$afterHash;requires_client_restart=$true} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $releaseRoot 'local_loader_installation.json')
Get-Content -LiteralPath (Join-Path $releaseRoot 'local_loader_installation.json')
