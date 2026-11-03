param(
    [string]$PythonPath = 'C:\Python314\python.exe',
    [string]$EditorPath = 'C:\Program Files\Unity\Hub\Editor\6000.3.18f1\Editor\Unity.exe',
    [string]$TrialDirectory = '',
    [double]$PixelsPerUnit = (100 / 1.2),
    [string]$PrefabName = 'OmegaRapier_FlashTrial',
    [string]$BundleName = 'omega_rapier_flash_trial.unity3d',
    [string]$AssetPrefix = 'OmegaRapier',
    [string]$PreviewName = 'Omega Rapier',
    [int]$FrameCount = 0
)
$ErrorActionPreference = 'Stop'
if (!$TrialDirectory) {
    $repo = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
    $TrialDirectory = Join-Path $repo 'outputs\flash_converter_trial\OmegaRapier'
}
$trial = (Resolve-Path -LiteralPath $TrialDirectory).Path
$project = Join-Path $trial 'conversion\UnityProject'
$output = Join-Path $trial 'conversion\bundle'
$capture = Join-Path $trial 'conversion\source_capture\capture_manifest.json'
$log = Join-Path $trial 'conversion\unity_build.log'
if (!(Test-Path -LiteralPath $EditorPath -PathType Leaf)) { throw "Unity Editor not found: $EditorPath" }
$prepareArguments = @((Join-Path $PSScriptRoot 'prepare_project.py'), '--capture', $capture,
    '--project', $project, '--output', $output, '--pixels-per-unit', $PixelsPerUnit,
    '--prefab-name', $PrefabName, '--bundle-name', $BundleName,
    '--asset-prefix', $AssetPrefix, '--preview-name', $PreviewName)
if ($FrameCount -lt 0) { throw 'FrameCount must be positive when provided.' }
if ($FrameCount -gt 0) { $prepareArguments += @('--frame-count', $FrameCount) }
& $PythonPath @prepareArguments
if ($LASTEXITCODE -ne 0) { throw "Project preparation failed: $LASTEXITCODE" }
$arguments = '-batchmode -quit -projectPath "' + $project + '" -executeMethod FlashTrialBuilder.Build -logFile "' + $log + '"'
$process = Start-Process -FilePath $EditorPath -ArgumentList $arguments -WindowStyle Hidden -PassThru
# Wait for the Editor itself. Start-Process -Wait also waits for persistent license helpers.
$process.WaitForExit()
if ($process.ExitCode -ne 0) { throw "Unity exited with $($process.ExitCode). See $log (code 198 usually means no activated Editor license)." }
if (!(Select-String -LiteralPath $log -Pattern 'FLASH_TRIAL_BUILD_SUCCESS' -Quiet)) { throw "Unity did not report success: $log" }
& $PythonPath (Join-Path $PSScriptRoot 'validate_trial.py') --trial $trial --require-unity
if ($LASTEXITCODE -ne 0) { throw "Render validation failed: $LASTEXITCODE" }
Write-Output "Validated bundle: $(Join-Path $output $BundleName)"
