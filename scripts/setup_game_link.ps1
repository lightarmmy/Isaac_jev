$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
$taskDependencies = Join-Path $taskRoot '.local\serverdeps'
Push-Location -LiteralPath $taskRoot
try {
    & $taskPython -c 'import sys; sys.path.insert(0, ".local/serverdeps"); import paramiko; print("SSH dependencies ready:", paramiko.__version__)' 2>$null
    if ($LASTEXITCODE -ne 0) {
        & $taskPython -m pip install --target $taskDependencies 'paramiko==4.0.0'
        if ($LASTEXITCODE -ne 0) { throw 'SSH dependency installation failed' }
    }
    & (Join-Path $PSScriptRoot 'start_game_link.ps1')
} finally { Pop-Location }
