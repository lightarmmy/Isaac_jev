param([Parameter(Mandatory=$true)][string]$ReportDirectory)
$ErrorActionPreference = 'Stop'
if (-not ([Diagnostics.Process]::GetCurrentProcess().Modules | Where-Object { $_.ModuleName -ieq 'SbieDll.dll' })) { throw 'Run the network probe inside Sandboxie' }
$taskClient = [Net.Sockets.TcpClient]::new()
try {
    $taskClient.Connect('127.0.0.1',9527)
    $taskClient.ReceiveTimeout = 5000
    $taskClient.SendTimeout = 5000
    $taskStream = $taskClient.GetStream()
    $taskWriter = [IO.StreamWriter]::new($taskStream,[Text.UTF8Encoding]::new($false),1024,$true)
    $taskWriter.WriteLine('{"protocol_version":1,"type":"control","payload":{"command":"get_status"}}')
    $taskWriter.Flush()
    $taskReader = [IO.StreamReader]::new($taskStream)
    $taskResult = $taskReader.ReadLine() | ConvertFrom-Json
    if (-not $taskResult.ok) { throw 'Host bridge rejected the status query' }
    New-Item -ItemType Directory -Path $ReportDirectory -Force | Out-Null
    $taskResult | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $ReportDirectory 'network-probe-report.json') -Encoding utf8
} finally {
    $taskClient.Dispose()
}
