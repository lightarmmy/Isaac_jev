param([string]$LabGame = '')
$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$taskLabRoot = [IO.Path]::GetFullPath((Join-Path $taskRoot '.local\isaac-lab'))
if (-not $LabGame) { $LabGame = Join-Path $taskLabRoot 'game' }
$taskTargetGame = (Resolve-Path -LiteralPath $LabGame).Path
if (-not $taskTargetGame.StartsWith($taskLabRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'This installer only writes to the project lab, never the original Steam game' }
$taskModDir = Join-Path $taskTargetGame 'mods\isaac_jev_bridge'
New-Item -ItemType Directory -Path $taskModDir -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $taskRoot 'mods\isaac_jev_bridge\main.lua') -Destination $taskModDir -Force
Copy-Item -LiteralPath (Join-Path $taskRoot 'mods\isaac_jev_bridge\metadata.xml') -Destination $taskModDir -Force
Write-Output ('Bridge mod copied to ' + $taskModDir)
