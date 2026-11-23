param([ValidateSet('diagnostic','demonstration')][string]$RecordPurpose = 'demonstration', [switch]$NoRecording)
$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$taskLocal = Join-Path $taskRoot '.local'
$taskPidFile = Join-Path $taskLocal 'bridge-process.json'
if (Test-Path -LiteralPath $taskPidFile) {
    $taskExisting = Get-Content -LiteralPath $taskPidFile -Raw | ConvertFrom-Json
    $taskProcess = Get-Process -Id $taskExisting.pid -ErrorAction SilentlyContinue
    if ($taskProcess -and $taskProcess.Path -eq $taskExisting.python) { throw 'Recorded bridge process is already running' }
}
$taskPython = (& py -3.11 -c 'import sys; print(sys.executable)').Trim()
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $taskPython)) { throw 'Python 3.11 is unavailable' }
New-Item -ItemType Directory -Path $taskLocal -Force | Out-Null
$taskArgs = @('-X','utf8',('"'+(Join-Path $taskRoot 'run.py')+'"'),'bridge','--record','recordings','--record-purpose',$RecordPurpose)
if ($NoRecording) { $taskArgs += '--no-record' }
$taskProcess = Start-Process -FilePath $taskPython -ArgumentList $taskArgs -WorkingDirectory $taskRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $taskLocal 'bridge.log') -RedirectStandardError (Join-Path $taskLocal 'bridge-error.log') -PassThru
$taskActualPython = (Get-Process -Id $taskProcess.Id -ErrorAction Stop).Path
@{ pid = $taskProcess.Id; python = $taskActualPython; launched_python = $taskPython; started_at = (Get-Date).ToString('o') } | ConvertTo-Json | Set-Content -LiteralPath $taskPidFile -Encoding utf8
Write-Output ('Started host bridge PID ' + $taskProcess.Id + ', localhost:9527; manual mode on game connection')
