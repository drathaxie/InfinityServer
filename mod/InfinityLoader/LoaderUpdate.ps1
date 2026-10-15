$ErrorActionPreference = 'Stop'
$target = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__TARGET__'))
$staged = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__STAGED__'))
$expectedHash = '__HASH__'
$gameProcessId = __PID__
$expectedStartTicks = __START_TICKS__
$logPath = $target + '.update.log'
try {
    $target = [IO.Path]::GetFullPath($target)
    $staged = [IO.Path]::GetFullPath($staged)
    if ($staged -ne ($target + '.update')) { throw 'Unexpected staging path' }
    $gameProcess = Get-Process -Id $gameProcessId -ErrorAction SilentlyContinue
    if ($null -ne $gameProcess -and $gameProcess.StartTime.ToUniversalTime().Ticks -eq $expectedStartTicks) {
        $gameProcess.WaitForExit()
    }
    if (-not (Test-Path -LiteralPath $staged)) { exit 0 }
    if ((Get-FileHash -LiteralPath $staged -Algorithm SHA256).Hash -ne $expectedHash) {
        throw 'Staged update hash does not match the verified download'
    }
    $applied = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        try {
            [IO.File]::Replace($staged, $target, ($target + '.previous'), $true)
            $applied = $true
            break
        } catch {
            if ($attempt -eq 59) { throw }
            Start-Sleep -Seconds 1
        }
    }
    if (-not $applied -or (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -ne $expectedHash) {
        throw 'Installed update verification failed'
    }
    'Update applied; previous build retained.' | Set-Content -LiteralPath $logPath
    # Only delete this generated helper after a verified installation.
    Remove-Item -LiteralPath $PSCommandPath -Force
} catch {
    $_.Exception.Message | Set-Content -LiteralPath $logPath
    exit 1  # keep both staged update and helper for diagnosis/retry
}
