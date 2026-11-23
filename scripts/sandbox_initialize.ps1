param([Parameter(Mandatory=$true)][string]$ReportDirectory)
$ErrorActionPreference = 'Stop'
if (-not ([Diagnostics.Process]::GetCurrentProcess().Modules | Where-Object { $_.ModuleName -ieq 'SbieDll.dll' })) { throw 'Run this initializer inside the verified IsaacJevLab sandbox' }
$taskSave = Join-Path ([Environment]::GetFolderPath('UserProfile')) 'Documents\My Games\Binding of Isaac Repentance+'
$taskOptionsVisible = Test-Path -LiteralPath (Join-Path $taskSave 'options.ini')
$taskSavesVisible = @(Get-ChildItem -LiteralPath $taskSave -File -Filter '*.dat' -ErrorAction SilentlyContinue).Count
if ($taskOptionsVisible -or $taskSavesVisible) { throw 'Expected a fresh hidden-host save directory; refusing to replace existing experimental settings/saves' }
New-Item -ItemType Directory -Path $taskSave -Force | Out-Null
@'
[Options]
SteamCloud=0
EnableMods=1
EnableDebugConsole=1
Fullscreen=0
'@ | Set-Content -LiteralPath (Join-Path $taskSave 'options.ini') -Encoding ascii
New-Item -ItemType Directory -Path $ReportDirectory -Force | Out-Null
@{
    save_path = $taskSave
    existing_options_visible = $taskOptionsVisible
    existing_save_files_visible = $taskSavesVisible
    steam_cloud_setting = 0
    generated_at = (Get-Date).ToString('o')
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $ReportDirectory 'initialize-report.json') -Encoding utf8
