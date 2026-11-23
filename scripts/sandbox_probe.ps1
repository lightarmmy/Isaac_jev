param([Parameter(Mandatory=$true)][string]$ReportDirectory)
$ErrorActionPreference = 'Stop'
# Run ONLY through Sandboxie Start.exe /box:IsaacJevLab. The host-side caller
# verifies that the marker files occur in the container and not on the host.
if (-not ([Diagnostics.Process]::GetCurrentProcess().Modules | Where-Object { $_.ModuleName -ieq 'SbieDll.dll' })) { throw 'This probe must run inside Sandboxie' }
$taskSave = Join-Path ([Environment]::GetFolderPath('UserProfile')) 'Documents\My Games\Binding of Isaac Repentance+'
$taskMarker = 'isaac-jev-isolation-probe.txt'
New-Item -ItemType Directory -Path $taskSave -Force | Out-Null
'IsaacJevLab file virtualization probe' | Set-Content -LiteralPath (Join-Path $taskSave $taskMarker) -Encoding utf8
# Exercise a real Steam userdata path without modifying any real Steam file.
$taskSteamProbe = 'C:\Program Files (x86)\Steam\userdata\isaac-jev-isolation-probe.txt'
'IsaacJevLab Steam userdata virtualization probe' | Set-Content -LiteralPath $taskSteamProbe -Encoding utf8
New-Item -ItemType Directory -Path $ReportDirectory -Force | Out-Null
@{
    save_path = $taskSave
    save_probe = Join-Path $taskSave $taskMarker
    steam_probe = $taskSteamProbe
    generated_at = (Get-Date).ToString('o')
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $ReportDirectory 'probe-report.json') -Encoding utf8
