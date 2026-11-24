$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
$taskRun = Join-Path $taskRoot 'run.py'
Push-Location -LiteralPath $taskRoot
try {
    $taskReply = & $taskPython -X utf8 $taskRun status 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Output 'Local game bridge is already running. Use jev_connect / jev_record in the game console.'
    } else {
        & (Join-Path $PSScriptRoot 'start_bridge.ps1') -NoRecording
        Write-Output 'Local bridge started without recording. Use jev_record on to start capture.'
    }
} finally { Pop-Location }
