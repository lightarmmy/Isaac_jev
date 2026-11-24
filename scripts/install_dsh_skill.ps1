param([string]$DshSkillsRoot = '')
$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
& (Join-Path $taskRoot '.venv\Scripts\python.exe') -X utf8 (Join-Path $PSScriptRoot 'build_dsh_skill.py')
if ($LASTEXITCODE -ne 0) { throw 'Skill build failed' }
if (-not $DshSkillsRoot) { $DshSkillsRoot = Join-Path $env:USERPROFILE '.dsh\skills' }
$taskSkillsRoot = [IO.Path]::GetFullPath($DshSkillsRoot)
$taskTarget = [IO.Path]::GetFullPath((Join-Path $taskSkillsRoot 'isaac-play'))
if (-not $taskTarget.StartsWith($taskSkillsRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid skill target' }
$taskExisting = Join-Path $taskTarget 'SKILL.md'
if ((Test-Path -LiteralPath $taskExisting) -and -not (Select-String -LiteralPath $taskExisting -Pattern '^name: isaac-play$' -Quiet)) { throw 'Destination belongs to another skill' }
New-Item -ItemType Directory -Force -Path $taskTarget | Out-Null
Get-ChildItem -LiteralPath (Join-Path $taskRoot 'skills\isaac-play') -Force | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination $taskTarget -Recurse -Force
}
Write-Output ('Installed dsh skill: ' + $taskExisting)
Write-Output ('Tool entry: ' + (Join-Path $taskTarget 'scripts\isaac_tools.py'))
