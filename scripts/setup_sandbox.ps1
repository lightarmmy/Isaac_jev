param([string]$SandboxieDirectory = 'D:\Program Files\Sandboxie-Plus')
$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$taskIni = Join-Path $SandboxieDirectory 'SbieIni.exe'
if (-not (Test-Path -LiteralPath $taskIni)) { throw 'Sandboxie SbieIni.exe is missing' }
$taskBox = 'IsaacJevLab'
$taskLab = Join-Path $taskRoot '.local\isaac-lab'
$taskContainer = [IO.Path]::GetFullPath((Join-Path $taskLab 'sandbox'))
if (-not $taskContainer.StartsWith($taskRoot + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Container must stay inside the workspace' }
$taskExisting = @(& $taskIni query /boxes '*')
if ($taskExisting -contains $taskBox) { throw 'IsaacJevLab already exists; refusing to replace its settings' }
New-Item -ItemType Directory -Path $taskLab -Force | Out-Null
Copy-Item -LiteralPath 'C:\Windows\Sandboxie.ini' -Destination (Join-Path $taskLab ('Sandboxie-before-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.ini'))
$taskSave = Join-Path ([Environment]::GetFolderPath('UserProfile')) 'Documents\My Games\Binding of Isaac Repentance+'
$taskSettings = [ordered]@{
    Enabled = 'y'
    ConfigLevel = '10'
    FileRootPath = $taskContainer
    AutoRecover = 'n'
    AutoDelete = 'n'
    BorderColor = '#00FF00,ttl'
    CopyLimitKb = '-1'
    BlockNetworkFiles = 'y'
    WriteFilePath = $taskSave
}
foreach ($taskEntry in $taskSettings.GetEnumerator()) {
    & $taskIni set $taskBox $taskEntry.Key $taskEntry.Value
    if ($LASTEXITCODE -ne 0) { throw ('SbieIni failed for ' + $taskEntry.Key) }
}
& $taskIni append $taskBox WriteFilePath ($taskSave + '\*')
foreach ($taskEntry in $taskSettings.GetEnumerator()) {
    $taskReadback = @(& $taskIni query $taskBox $taskEntry.Key)
    if ($taskReadback -notcontains $taskEntry.Value) { throw ('Driver readback differs for ' + $taskEntry.Key + ': ' + ($taskReadback -join ', ')) }
}
$taskConfigText = Get-Content -LiteralPath 'C:\Windows\Sandboxie.ini' -Raw
if ($taskConfigText -notmatch '\[IsaacJevLab\]') { throw 'Sandbox section was not persisted' }
@{
    box = $taskBox
    sandboxie_directory = $SandboxieDirectory
    container = $taskContainer
    host_save_directory = $taskSave
    configured_at = (Get-Date).ToString('o')
    file_isolation_verified = $false
    steam_isolation_verified = $false
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $taskLab 'sandbox-info.json') -Encoding utf8
Write-Output ('Created ' + $taskBox + ' at ' + $taskContainer)
Write-Output 'Configuration read back from the driver. File and Steam isolation remain unverified.'
