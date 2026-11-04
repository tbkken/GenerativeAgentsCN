# Follow the two files created for this Web process, including its startup output.
$ErrorActionPreference = 'Stop'
$webProcess = Get-Process -Id ([int]$env:GA_RESTART_WEB_PID) -ErrorAction SilentlyContinue
$readers = @()
try {
    foreach ($path in $env:GA_RESTART_STDOUT, $env:GA_RESTART_STDERR) {
        $stream = [IO.File]::Open($path, 'Open', 'Read', 'ReadWrite, Delete')
        $readers += [IO.StreamReader]::new($stream, [Text.Encoding]::UTF8)
    }
    if ([Console]::IsOutputRedirected) {
        [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
    }
    do {
        # Check before reading so the last pass also drains output written at exit.
        $stopped = $null -eq $webProcess -or $webProcess.HasExited
        foreach ($reader in $readers) {
            $Host.UI.Write($reader.ReadToEnd())
        }
        if (-not $stopped) { Start-Sleep -Milliseconds 200 }
    } until ($stopped)
    Write-Host "[INFO] Web process exited. PID=$env:GA_RESTART_WEB_PID"
} finally {
    foreach ($reader in $readers) { $reader.Dispose() }
    if ($webProcess) { $webProcess.Dispose() }
}
