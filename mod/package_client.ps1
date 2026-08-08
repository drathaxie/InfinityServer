param(
    [string]$ApiUrl = "https://divinityarts.mooo.com/",
    [string]$ContentUrl = "https://content.130-162-189-229.sslip.io/game/"
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$project = Join-Path $PSScriptRoot "InfinityLoader\InfinityLoader.csproj"
$builtDll = Join-Path $PSScriptRoot "InfinityLoader\bin\Release\InfinityLoader.dll"
$published = Join-Path $repo "data\mod"
$stage = Join-Path $repo "tmp\client_release_current"
$dist = Join-Path $repo "dist"
$zip = Join-Path $dist "InfinityServer-Client.zip"

dotnet build $project -c Release -v minimal
if ($LASTEXITCODE -ne 0) { throw "InfinityLoader build failed." }

New-Item -ItemType Directory -Force -Path $published, $dist | Out-Null
Copy-Item -LiteralPath $builtDll -Destination (Join-Path $published "InfinityLoader.dll") -Force
$hash = (Get-FileHash $builtDll -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath (Join-Path $published "InfinityLoader.dll.sha256") -Value $hash -NoNewline

if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
New-Item -ItemType Directory -Force -Path (Join-Path $stage "UserData\Beyond") | Out-Null
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "doorstop\winhttp.dll") -Destination $stage
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "doorstop\0Harmony.dll") -Destination $stage
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "doorstop\doorstop_config.ini") -Destination $stage
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "doorstop\.doorstop_version") -Destination $stage
Copy-Item -LiteralPath $builtDll -Destination $stage
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "assets\emoji.unity3d") -Destination (Join-Path $stage "UserData\Beyond")
Set-Content -LiteralPath (Join-Path $stage "UserData\infinity_api.txt") -Value $ApiUrl -NoNewline
Set-Content -LiteralPath (Join-Path $stage "UserData\infinity_content.txt") -Value $ContentUrl -NoNewline
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "INSTALL.txt") -Destination $stage

if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
Compress-Archive -Path (Join-Path $stage "*") -DestinationPath $zip -CompressionLevel Optimal
Write-Host "Published loader $hash"
Write-Host "Client pack: $zip"
