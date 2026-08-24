@echo off
setlocal EnableExtensions

rem Always run from the repository that contains this script.
cd /d "%~dp0"

set "GA_RESTART_PROJECT_ROOT=%~dp0"
set "GA_RESTART_PORT=8000"
set "GA_RESTART_VAR_DIR=%~dp0var"
set "GA_RESTART_LOG_DIR=%~dp0var\logs"
set "GA_RESTART_DATABASE_URL=sqlite:///%~dp0var/generative-agents.db"
set "GA_RESTART_DATABASE_URL=%GA_RESTART_DATABASE_URL:\=/%"
set "GA_RESTART_STDOUT=%~dp0var\logs\web.stdout.log"
set "GA_RESTART_STDERR=%~dp0var\logs\web.stderr.log"

if not exist "%GA_RESTART_LOG_DIR%" mkdir "%GA_RESTART_LOG_DIR%"

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python was not found in PATH.
    exit /b 1
)

echo [INFO] Checking port %GA_RESTART_PORT%...

rem Stop only the Web entry point listening on the configured port. Simulation
rem and artifact workers use different module names and are intentionally kept.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$ErrorActionPreference = 'Stop';" ^
    "$connections = @(Get-NetTCPConnection -LocalPort ([int]$env:GA_RESTART_PORT) -State Listen -ErrorAction SilentlyContinue);" ^
    "$seen = @{};" ^
    "foreach ($connection in $connections) {" ^
    "  $processId = [int]$connection.OwningProcess;" ^
    "  if ($seen.ContainsKey($processId)) { continue };" ^
    "  $seen[$processId] = $true;" ^
    "  $processInfo = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + $processId);" ^
    "  if ($null -eq $processInfo) { continue };" ^
    "  if ($processInfo.CommandLine -notmatch 'generative_agents\.web\.main') {" ^
    "    throw ('Port ' + $env:GA_RESTART_PORT + ' is occupied by another program. PID=' + $processId + '; command=' + $processInfo.CommandLine)" ^
    "  };" ^
    "  Write-Host ('[INFO] Stopping old Web service. PID=' + $processId);" ^
    "  Stop-Process -Id $processId -Force;" ^
    "  try { Wait-Process -Id $processId -Timeout 10 -ErrorAction Stop } catch { }" ^
    "}"

if errorlevel 1 (
    echo [ERROR] The old service could not be stopped safely.
    exit /b 1
)

echo [INFO] Starting Web service in the background...

set "GA_RESTART_WEB_PID="
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$ErrorActionPreference = 'Stop';" ^
    "$pythonPath = (Get-Command python).Source;" ^
    "$arguments = @(" ^
    "  '-m', 'generative_agents.web.main'," ^
    "  '--database-url', $env:GA_RESTART_DATABASE_URL," ^
    "  '--var-dir', $env:GA_RESTART_VAR_DIR," ^
    "  '--host', '127.0.0.1'," ^
    "  '--port', $env:GA_RESTART_PORT," ^
    "  '--max-concurrent-runs', '2'" ^
    ");" ^
    "$webProcess = Start-Process -FilePath $pythonPath -ArgumentList $arguments -WorkingDirectory $env:GA_RESTART_PROJECT_ROOT -WindowStyle Hidden -RedirectStandardOutput $env:GA_RESTART_STDOUT -RedirectStandardError $env:GA_RESTART_STDERR -PassThru;" ^
    "Set-Content -LiteralPath (Join-Path $env:GA_RESTART_VAR_DIR 'web.pid') -Value $webProcess.Id -Encoding ascii"

if errorlevel 1 (
    echo [ERROR] Failed to launch the Web process.
    exit /b 1
)

if exist "%GA_RESTART_VAR_DIR%\web.pid" set /p GA_RESTART_WEB_PID=<"%GA_RESTART_VAR_DIR%\web.pid"

if not defined GA_RESTART_WEB_PID (
    echo [ERROR] Failed to start the Web service.
    if exist "%GA_RESTART_STDERR%" powershell -NoProfile -Command "Get-Content -LiteralPath $env:GA_RESTART_STDERR -Tail 30"
    exit /b 1
)

echo [INFO] New Web process started. PID=%GA_RESTART_WEB_PID%
echo [INFO] Waiting for the health endpoint...

for /L %%I in (1,1,30) do (
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
        "try { Invoke-RestMethod -Uri ('http://127.0.0.1:' + $env:GA_RESTART_PORT + '/api/v1/health/ready') -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }"
    if not errorlevel 1 goto service_ready
    timeout /t 1 /nobreak >nul
)

echo [ERROR] The service did not pass its health check within 30 seconds.
echo [ERROR] Log: %GA_RESTART_STDERR%
if exist "%GA_RESTART_STDERR%" powershell -NoProfile -Command "Get-Content -LiteralPath $env:GA_RESTART_STDERR -Tail 30"
exit /b 1

:service_ready
echo [OK] Web service restarted successfully: http://127.0.0.1:%GA_RESTART_PORT%/
exit /b 0
