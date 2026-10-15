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
set "GA_RESTART_PREFLIGHT_LOG=%~dp0var\logs\web.preflight.log"

if not exist "%GA_RESTART_LOG_DIR%" mkdir "%GA_RESTART_LOG_DIR%"

rem Resolve one interpreter for both the import check and the background service.
set "GA_RESTART_PYTHON=%~dp0.venv\Scripts\python.exe"
if exist "%GA_RESTART_PYTHON%" goto python_ready
set "GA_RESTART_PYTHON="
for /f "delims=" %%P in ('where python 2^>nul') do if not defined GA_RESTART_PYTHON set "GA_RESTART_PYTHON=%%P"
if defined GA_RESTART_PYTHON goto python_ready
echo [ERROR] No Python interpreter was found in .venv or PATH.
echo [ERROR] Install Python 3.11 or newer, then follow the installation steps in README.md.
exit /b 1

:python_ready
echo [INFO] Using Python: "%GA_RESTART_PYTHON%"
echo [INFO] Checking the installed Web entry point...
"%GA_RESTART_PYTHON%" -m generative_agents.adapters.web.main --help >"%GA_RESTART_PREFLIGHT_LOG%" 2>&1
if errorlevel 1 goto import_failed

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
    "  if ($processInfo.CommandLine -notmatch 'generative_agents\.(?:adapters\.)?web\.main') {" ^
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
    "$arguments = @(" ^
    "  '-m', 'generative_agents.adapters.web.main'," ^
    "  '--database-url', ([char]34 + $env:GA_RESTART_DATABASE_URL + [char]34)," ^
    "  '--var-dir', ([char]34 + $env:GA_RESTART_VAR_DIR + [char]34)," ^
    "  '--host', '127.0.0.1'," ^
    "  '--port', $env:GA_RESTART_PORT," ^
    "  '--max-concurrent-runs', '2'" ^
    ");" ^
    "$webProcess = Start-Process -FilePath $env:GA_RESTART_PYTHON -ArgumentList $arguments -WorkingDirectory $env:GA_RESTART_PROJECT_ROOT -WindowStyle Hidden -RedirectStandardOutput $env:GA_RESTART_STDOUT -RedirectStandardError $env:GA_RESTART_STDERR -PassThru;" ^
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
echo [INFO] Health URL: http://127.0.0.1:%GA_RESTART_PORT%/api/studio/health

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$ErrorActionPreference = 'Stop';" ^
    "$healthUrl = 'http://127.0.0.1:' + $env:GA_RESTART_PORT + '/api/studio/health';" ^
    "$lastHealthError = 'No health response was received.';" ^
    "$watch = [Diagnostics.Stopwatch]::StartNew();" ^
    "while ($watch.Elapsed.TotalSeconds -lt 30) {" ^
    "  if (-not (Get-Process -Id ([int]$env:GA_RESTART_WEB_PID) -ErrorAction SilentlyContinue)) { Write-Host ('[ERROR] Web process exited before becoming healthy. PID=' + $env:GA_RESTART_WEB_PID); exit 1 };" ^
    "  $remaining = 30 - $watch.Elapsed.TotalSeconds; if ($remaining -lt 1) { break };" ^
    "  try {" ^
    "    $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec ([int][Math]::Min(2, [Math]::Floor($remaining)));" ^
    "    if ($health.status -eq 'ok' -and $health.runtime_truth -eq 'files' -and $health.database_owner -eq 'ga_studio' -and (Get-Process -Id ([int]$env:GA_RESTART_WEB_PID) -ErrorAction SilentlyContinue)) { exit 0 };" ^
    "    $lastHealthError = 'Unexpected health response: ' + ($health | ConvertTo-Json -Compress -Depth 3)" ^
    "  } catch { $lastHealthError = $_.Exception.Message };" ^
    "  Start-Sleep -Milliseconds 250" ^
    "};" ^
    "Write-Host '[ERROR] The service did not pass its health check within 30 seconds.';" ^
    "Write-Host ('[ERROR] Health URL: ' + $healthUrl);" ^
    "$lastHealthError = $lastHealthError -replace '\s+', ' ';" ^
    "Write-Host ('[ERROR] Last health failure: ' + $lastHealthError.Substring(0, [Math]::Min(500, $lastHealthError.Length))); exit 1"
if not errorlevel 1 goto service_ready

echo [ERROR] stdout log: "%GA_RESTART_STDOUT%"
echo [ERROR] stderr log: "%GA_RESTART_STDERR%"
if exist "%GA_RESTART_STDERR%" powershell -NoProfile -Command "Get-Content -LiteralPath $env:GA_RESTART_STDERR -Tail 30"
exit /b 1

:import_failed
echo [ERROR] The Web entry point could not be imported by this Python interpreter:
echo [ERROR] "%GA_RESTART_PYTHON%"
echo [ERROR] Log: "%GA_RESTART_PREFLIGHT_LOG%"
if exist "%GA_RESTART_PREFLIGHT_LOG%" type "%GA_RESTART_PREFLIGHT_LOG%"
echo [ERROR] Install the project and Web dependencies in Command Prompt ^(cmd.exe^):
echo   cd /d "%GA_RESTART_PROJECT_ROOT%"
echo   "%GA_RESTART_PYTHON%" -m pip install -e ".[runtime,studio,web]"
exit /b 1

:service_ready
echo [OK] Web service restarted successfully: http://127.0.0.1:%GA_RESTART_PORT%/
exit /b 0
