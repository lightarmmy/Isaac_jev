$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$taskLab = Join-Path $taskRoot '.local\isaac-lab'
$taskInfo = Get-Content -LiteralPath (Join-Path $taskLab 'sandbox-info.json') -Raw | ConvertFrom-Json
if (-not $taskInfo.file_isolation_verified -or -not $taskInfo.steam_offline_user_confirmed) { throw 'Verify file isolation and switch the sandbox Steam client to offline mode first' }
$taskStart = Join-Path $taskInfo.sandboxie_directory 'Start.exe'
$taskPidOutput = Join-Path $taskLab 'box-pids.txt'
Start-Process -FilePath $taskStart -ArgumentList @(('/box:' + $taskInfo.box),'/listpids') -RedirectStandardOutput $taskPidOutput -WindowStyle Hidden -Wait
$taskPidLines = @(Get-Content -LiteralPath $taskPidOutput | ForEach-Object { $_.Trim() } | Where-Object { $_ -match '^\d+$' })
if (-not $taskPidLines.Count -or [int]$taskPidLines[0] -ne $taskPidLines.Count - 1) { throw 'Cannot verify the Sandboxie process list' }
$taskBoxPids = @($taskPidLines | Select-Object -Skip 1)
$taskSteam = @(Get-Process -Name steam -ErrorAction SilentlyContinue)
if (-not $taskSteam.Count) { throw 'Start Steam inside the experiment sandbox first' }
foreach ($taskProcess in $taskSteam) {
    if ($taskBoxPids -notcontains ([string]$taskProcess.Id)) { throw 'A Steam process is running outside the experiment sandbox' }
}
py -3.11 -X utf8 (Join-Path $PSScriptRoot 'host_snapshot.py') verify
if ($LASTEXITCODE -ne 0) { throw 'Host fingerprints changed; inspect before continuing' }
$taskExe = Join-Path $taskRoot '.local\REPENTOGONLauncher\REPENTOGONLauncher.exe'
$taskGame = Join-Path $taskLab 'game\isaac-ng.exe'
$taskConfig = Join-Path $taskLab 'launcher.ini'
$taskArgs = @(('/box:' + $taskInfo.box),('"'+$taskExe+'"'),('"--configuration-file='+$taskConfig+'"'),'--skip-repentogon-update','--skip-wizard',('"--isaac='+$taskGame+'"'))
Start-Process -FilePath $taskStart -ArgumentList $taskArgs -WorkingDirectory (Split-Path $taskExe) -WindowStyle Normal | Out-Null
Write-Output 'Opened the official experimental launcher inside IsaacJevLab. Game waits for Launch.'
