param([string]$SourceGame = 'D:\SteamLibrary\steamapps\common\The Binding of Isaac Rebirth')
$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$taskSource = (Resolve-Path -LiteralPath $SourceGame).Path
$taskLab = [IO.Path]::GetFullPath((Join-Path $taskRoot '.local\isaac-lab\game'))
if (-not $taskLab.StartsWith($taskRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Lab must stay inside this workspace' }
if (-not (Test-Path -LiteralPath (Join-Path $taskSource 'isaac-ng.exe'))) { throw 'Source executable is missing' }
if (Test-Path -LiteralPath $taskLab) { throw 'Lab already exists; refusing to overwrite an existing experiment' }
New-Item -ItemType Directory -Path $taskLab -Force | Out-Null
# Source is read-only. No Steam verification and no writes to the original installation.
Get-ChildItem -LiteralPath $taskSource -File | Copy-Item -Destination $taskLab
foreach ($taskName in @('data','resources','tools')) {
    $taskDir = Join-Path $taskSource $taskName
    if (Test-Path -LiteralPath $taskDir) { Copy-Item -LiteralPath $taskDir -Destination $taskLab -Recurse }
}
New-Item -ItemType Directory -Path (Join-Path $taskLab 'mods') -Force | Out-Null
$taskManifest = @{
    original_game = $taskSource
    lab_game = $taskLab
    original_exe_sha256 = (Get-FileHash -LiteralPath (Join-Path $taskSource 'isaac-ng.exe') -Algorithm SHA256).Hash
    created_at = (Get-Date).ToString('o')
    saves_isolated = $false
    note = 'Copying game files does not isolate Windows save data; do not launch until save handling is chosen.'
}
$taskManifest | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $taskRoot '.local\isaac-lab\lab.json') -Encoding utf8
Write-Output ('Prepared file-isolated lab: ' + $taskLab)
Write-Output 'The current copied EXE may still fail REPENTOGON compatibility. Import clean Steam depot files into this lab only.'
