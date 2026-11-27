param(
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $root "_local\launcher"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Test-Listening([int]$port) {
    try {
        return [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction Stop)
    } catch {
        return $false
    }
}

function Wait-Url([string]$url, [int]$seconds) {
    for ($i = 0; $i -lt $seconds; $i++) {
        try {
            $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) { return $true }
        } catch {
            Start-Sleep -Seconds 1
        }
    }
    return $false
}

Write-Host "正在启动湛知..." -ForegroundColor Cyan

if (-not (Test-Listening 8000)) {
    $python = Join-Path $root ".venv\Scripts\python.exe"
    if (-not (Test-Path $python)) {
        Write-Host "找不到 Python 环境：$python" -ForegroundColor Red
        Write-Host "请先在项目根目录执行 uv sync --locked。"
        exit 1
    }
    $backend = Start-Process -FilePath $python `
        -ArgumentList "-m", "uvicorn", "study_companion.main:app", "--host", "127.0.0.1", "--port", "8000" `
        -WorkingDirectory $root -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logDir "backend.out.log") `
        -RedirectStandardError (Join-Path $logDir "backend.err.log") `
        -PassThru
    Set-Content -Path (Join-Path $logDir "backend.pid") -Value $backend.Id -Encoding ascii
    Write-Host "后端已启动（端口 8000）。"
} else {
    Write-Host "后端已在运行（端口 8000），直接复用。"
}

if (-not (Test-Listening 5173)) {
    $npm = (Get-Command npm.cmd -ErrorAction SilentlyContinue).Source
    if (-not $npm) {
        Write-Host "找不到 npm，请先安装 Node.js 或在 frontend 目录执行 npm ci。" -ForegroundColor Red
        exit 1
    }
    $frontend = Start-Process -FilePath $npm `
        -ArgumentList "run", "dev", "--", "--host", "127.0.0.1", "--port", "5173" `
        -WorkingDirectory (Join-Path $root "frontend") -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logDir "frontend.out.log") `
        -RedirectStandardError (Join-Path $logDir "frontend.err.log") `
        -PassThru
    Set-Content -Path (Join-Path $logDir "frontend.pid") -Value $frontend.Id -Encoding ascii
    Write-Host "前端已启动（端口 5173）。"
} else {
    Write-Host "前端已在运行（端口 5173），直接复用。"
}

Write-Host "等待服务就绪..."
$backendReady = Wait-Url "http://127.0.0.1:8000/api/v1/health" 60
$frontendReady = Wait-Url "http://127.0.0.1:5173/" 60

if (-not $backendReady) {
    Write-Host "后端没有在 60 秒内就绪，请查看日志：$logDir\backend.err.log" -ForegroundColor Red
    exit 1
}
if (-not $frontendReady) {
    Write-Host "前端没有在 60 秒内就绪，请查看日志：$logDir\frontend.err.log" -ForegroundColor Red
    exit 1
}

if (-not $NoBrowser) {
    Start-Process "http://127.0.0.1:5173"
    Write-Host "已在浏览器打开 http://127.0.0.1:5173" -ForegroundColor Green
} else {
    Write-Host "服务已就绪：http://127.0.0.1:5173" -ForegroundColor Green
}
Write-Host "关闭本窗口不会停止服务；需要停止请双击“停止湛知.bat”。"
Start-Sleep -Seconds 3
