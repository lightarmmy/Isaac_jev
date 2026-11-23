$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
$taskRun = Join-Path $taskRoot 'run.py'
$taskLocal = Join-Path $taskRoot '.local'
New-Item -ItemType Directory -Path $taskLocal -Force | Out-Null
$taskStatus = $null
try {
    $taskReply = & $taskPython -X utf8 $taskRun status 2> (Join-Path $taskLocal 'collection-start-error.log')
    if ($LASTEXITCODE -eq 0) { $taskStatus = ($taskReply | ConvertFrom-Json).status }
} catch { $taskStatus = $null }
if (-not $taskStatus) {
    & (Join-Path $PSScriptRoot 'start_bridge.ps1')
    for ($taskAttempt = 0; $taskAttempt -lt 20; $taskAttempt++) {
        Start-Sleep -Milliseconds 250
        try {
            $taskReply = & $taskPython -X utf8 $taskRun status 2> (Join-Path $taskLocal 'collection-start-error.log')
            if ($LASTEXITCODE -eq 0) { $taskStatus = ($taskReply | ConvertFrom-Json).status; break }
        } catch { $taskStatus = $null }
    }
    if (-not $taskStatus) { throw 'Collector did not start. See .local/collection-start-error.log.' }
} else {
    $taskIdentity = Get-Content -LiteralPath (Join-Path $taskLocal 'bridge-process.json') -Raw | ConvertFrom-Json
    $taskProcess = Get-Process -Id $taskIdentity.pid -ErrorAction SilentlyContinue
    if (-not $taskProcess) { throw 'Cannot verify the collector process.' }
    if ($taskProcess.Path -and $taskProcess.Path -ne $taskIdentity.python) { throw 'Collector process identity changed.' }
    if ([Math]::Abs(($taskProcess.StartTime - [DateTimeOffset]::Parse($taskIdentity.started_at).LocalDateTime).TotalSeconds) -gt 3) { throw 'Collector PID was reused.' }
}
if ($taskStatus.recording_error -or $taskStatus.recording_dropped) { throw 'Collector reports an error; inspect before starting another session.' }
if ($taskStatus.connected) {
    $taskResult = (& $taskPython -X utf8 $taskRun mode manual | ConvertFrom-Json)
    if ($LASTEXITCODE -ne 0 -or -not $taskResult.ok) { throw 'Cannot restore manual mode.' }
}
if (-not $taskStatus.recording_enabled) {
    $taskResult = (& $taskPython -X utf8 $taskRun record rotate --purpose demonstration | ConvertFrom-Json)
    if ($LASTEXITCODE -ne 0 -or -not $taskResult.ok) { throw 'Cannot enable recording.' }
}
Write-Output 'Recording enabled. Play the experimental Isaac normally; new runs and game restarts are handled automatically.'
