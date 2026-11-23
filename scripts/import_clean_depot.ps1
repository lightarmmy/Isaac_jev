param([Parameter(Mandatory=$true)][string]$DepotDirectory)
$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$taskLab = [IO.Path]::GetFullPath((Join-Path $taskRoot '.local\isaac-lab\game'))
if (-not $taskLab.StartsWith($taskRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid lab path' }
$taskDepot = (Resolve-Path -LiteralPath $DepotDirectory).Path
if (-not (Test-Path -LiteralPath (Join-Path $taskDepot 'isaac-ng.exe'))) { throw 'The depot does not contain isaac-ng.exe; provide the Repentance+ Windows depot directory' }
if (-not (Test-Path -LiteralPath $taskLab)) { throw 'Run prepare_lab.ps1 first' }
# Copy official depot contents only into the disposable experiment.
Get-ChildItem -LiteralPath $taskDepot -Force | Copy-Item -Destination $taskLab -Recurse -Force
Write-Output ('Official EXE imported into lab: ' + (Get-FileHash -LiteralPath (Join-Path $taskLab 'isaac-ng.exe') -Algorithm SHA256).Hash)
