@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
title SimWorld - UE diagnostics
if not defined GA_SIMWORLD_PYTHON set "GA_SIMWORLD_PYTHON=%~dp0.venv\Scripts\python.exe"
if not defined GA_SIMWORLD_INSTALL_ROOT for %%I in ("%~dp0..") do set "GA_SIMWORLD_INSTALL_ROOT=%%~fI"
if not defined GA_SERVICES_CONFIG set "GA_SERVICES_CONFIG=%~dp0services.toml"
set "SW_RESULT=1"
if not exist "%GA_SIMWORLD_PYTHON%" (
    echo [ERROR] Python was not found: "%GA_SIMWORLD_PYTHON%"
    echo Create the project .venv or set GA_SIMWORLD_PYTHON to the installed service's python.exe.
    goto finish
)
set "SW_PORT="
set "SW_CONFIG_SCRIPT=%~dp0src\generative_agents\adapters\service_config.py"
rem Expand quoted paths in the inner cmd so a project path can contain shell metacharacters.
for /f "delims=" %%P in ('""%%GA_SIMWORLD_PYTHON%%" -I -B -X utf8 "%%SW_CONFIG_SCRIPT%%" simworld"') do set "SW_PORT=%%P"
if not defined SW_PORT (
    echo [ERROR] Cannot start a local SimWorld service for the configured address.
    echo Check GA_SERVICES_CONFIG, GA_SIMWORLD_URL and GA_SIMWORLD_PORT.
    goto finish
)
set "GA_SIMWORLD_PORT=%SW_PORT%"
"%GA_SIMWORLD_PYTHON%" -I -B -X utf8 -c "import ga_scene.windows_launcher" >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Cannot load the SimWorld service in "%GA_SIMWORLD_PYTHON%".
    echo Install the ga-simworld-service wheel with the windows-launcher extra. See README.md.
    goto finish
)
"%GA_SIMWORLD_PYTHON%" -I -B -X utf8 -u -m ga_scene.windows_launcher
set "SW_RESULT=%ERRORLEVEL%"
:finish
if "%SW_RESULT%"=="42" exit /b 0
if not "%GA_SIMWORLD_NO_PAUSE%"=="1" pause
exit /b %SW_RESULT%
