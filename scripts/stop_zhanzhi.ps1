param()

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $root "_local\launcher"

function Stop-ProjectPort([int]$port, [string]$pattern) {
    $connections = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    foreach ($connection in $connections) {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($connection.OwningProcess)" -ErrorAction SilentlyContinue
        if ($null -eq $process) { continue }
        $command = [string]$process.CommandLine
        if ($command -match $pattern) {
            Stop-Process -Id $connection.OwningProcess -Force -ErrorAction SilentlyContinue
            Write-Host "已停止端口 $port 上的湛知进程。"
        } else {
            Write-Host "端口 $port 被其他程序占用，未停止：$command" -ForegroundColor Yellow
        }
    }
}

Stop-ProjectPort 8000 "study_companion|uvicorn"
Stop-ProjectPort 5173 "vite|交付产品-dsh|frontend"

foreach ($name in @("backend.pid", "frontend.pid")) {
    $path = Join-Path $logDir $name
    if (Test-Path $path) {
        $processId = Get-Content $path -ErrorAction SilentlyContinue
        if ($processId) {
            Stop-Process -Id ([int]$processId) -Force -ErrorAction SilentlyContinue
        }
        Remove-Item $path -Force -ErrorAction SilentlyContinue
    }
}

Write-Host "湛知已停止。" -ForegroundColor Green
Start-Sleep -Seconds 2
