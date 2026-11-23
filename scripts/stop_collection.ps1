$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
$taskResult = (& $taskPython -X utf8 (Join-Path $taskRoot 'run.py') record stop | ConvertFrom-Json)
if ($LASTEXITCODE -ne 0 -or -not $taskResult.ok -or $taskResult.completed_error) { throw 'Could not seal the recording. Leave the collector running and inspect the error.' }
Write-Output 'Recording stopped and saved. You can now shut down the computer.'
