[CmdletBinding()]
param(
    [ValidateSet('Start', 'Status', 'Stop')]
    [string]$Action = 'Status',
    [ValidateRange(1024, 65535)][int]$ApiPort = 8000,
    [ValidateRange(1024, 65535)][int]$WebPort = 5173,
    [string]$DatabasePath,
    [switch]$EnableAgent
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repoRoot = Split-Path $PSScriptRoot -Parent
$stateRoot = Join-Path $repoRoot 'apps/api/data/windows-dev'
$statePath = Join-Path $stateRoot "processes-$ApiPort-$WebPort.json"
$python = Join-Path $repoRoot 'apps/api/.venv/Scripts/python.exe'
$vite = Join-Path $repoRoot 'apps/web/node_modules/vite/bin/vite.js'
if (-not $DatabasePath) { $DatabasePath = Join-Path $stateRoot 'preview.sqlite3' }
$DatabasePath = [IO.Path]::GetFullPath($DatabasePath)

function Get-OwnedProcess($entry) {
    $process = Get-Process -Id $entry.processId -ErrorAction SilentlyContinue
    if ($process -and $process.Path -eq $entry.path -and
        $process.StartTime.ToUniversalTime().Ticks -eq ([datetime]$entry.startedAt).ToUniversalTime().Ticks) {
        return $process
    }
    return $null
}

function Stop-OwnedTree($process) {
    # Python's Windows venv redirector and Vite/esbuild may create children.
    $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $($process.Id)")
    foreach ($child in $children) {
        $childProcess = Get-Process -Id $child.ProcessId -ErrorAction SilentlyContinue
        if ($childProcess -and $childProcess.StartTime -ge $process.StartTime) {
            Stop-OwnedTree $childProcess
        }
    }
    Stop-Process -InputObject $process -ErrorAction SilentlyContinue
}

function Save-Process($process) {
    return @{
        processId = $process.Id
        path = $process.Path
        startedAt = $process.StartTime.ToUniversalTime().ToString('o')
    }
}

switch ($Action) {
    'Start' {
        if ($ApiPort -eq $WebPort) { throw 'API and web ports must be different.' }
        if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath $vite)) {
            throw 'Install dependencies first: npx.cmd --yes pnpm@11.9.0 install --frozen-lockfile; uv sync --project apps/api --frozen --group dev'
        }
        foreach ($port in @($ApiPort, $WebPort)) {
            if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) {
                throw "Port $port is already in use. Inspect -Action Status or choose unused ports."
            }
        }
        New-Item -ItemType Directory -Path $stateRoot, (Split-Path $DatabasePath -Parent) -Force | Out-Null
        $node = (Get-Command node.exe -ErrorAction Stop).Source
        $settings = @{
            GREEN_ATLAS_DB_PATH = $DatabasePath
            GREEN_ATLAS_LOCAL_MODEL = $(if ($EnableAgent) { 'qwen3.5:4b' } else { '' })
            VITE_API_URL = "http://127.0.0.1:$ApiPort"
        }
        $previous = @{}
        $launched = @()
        try {
            . (Join-Path $PSScriptRoot 'windows-env.ps1') `
                -PreviousEnvironment $previous
            foreach ($name in $settings.Keys) {
                $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
                [Environment]::SetEnvironmentVariable($name, $settings[$name], 'Process')
            }
            $api = Start-Process -FilePath $python -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', $ApiPort) `
                -WorkingDirectory (Join-Path $repoRoot 'apps/api') -WindowStyle Hidden -PassThru `
                -RedirectStandardOutput (Join-Path $stateRoot "api-$ApiPort.stdout.log") `
                -RedirectStandardError (Join-Path $stateRoot "api-$ApiPort.stderr.log")
            $launched += $api
            $web = Start-Process -FilePath $node -ArgumentList @(('"{0}"' -f $vite), '--host', '127.0.0.1', '--port', $WebPort, '--strictPort') `
                -WorkingDirectory (Join-Path $repoRoot 'apps/web') -WindowStyle Hidden -PassThru `
                -RedirectStandardOutput (Join-Path $stateRoot "web-$WebPort.stdout.log") `
                -RedirectStandardError (Join-Path $stateRoot "web-$WebPort.stderr.log")
            $launched += $web
            @{
                api = (Save-Process $api); web = (Save-Process $web)
                database = $DatabasePath; agentEnabled = [bool]$EnableAgent
            } | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $statePath -Encoding utf8
            for ($attempt = 0; $attempt -lt 30; $attempt++) {
                if ($api.HasExited -or $web.HasExited) { throw "A server exited; inspect logs in $stateRoot" }
                try {
                    Invoke-WebRequest -Uri "http://127.0.0.1:$ApiPort/openapi.json" -TimeoutSec 2 -UseBasicParsing | Out-Null
                    Invoke-WebRequest -Uri "http://127.0.0.1:$WebPort" -TimeoutSec 2 -UseBasicParsing | Out-Null
                    Write-Host "API: http://127.0.0.1:$ApiPort | Web: http://127.0.0.1:$WebPort | DB: $DatabasePath"
                    return
                } catch { Start-Sleep -Seconds 1 }
            }
            throw "Servers did not become ready; inspect logs in $stateRoot"
        } catch {
            foreach ($process in $launched) { Stop-OwnedTree $process }
            throw
        } finally {
            foreach ($name in $previous.Keys) {
                if ($null -eq $previous[$name]) {
                    Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
                } else {
                    [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process')
                }
            }
        }
    }
    'Status' {
        $listeners = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
            Where-Object LocalPort -in $ApiPort, $WebPort |
            Select-Object LocalAddress, LocalPort, OwningProcess)
        if (-not (Test-Path -LiteralPath $statePath)) {
            @{ managed = $false; listeners = $listeners } | ConvertTo-Json -Depth 4
            return
        }
        $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
        $services = @{}
        foreach ($name in @('api', 'web')) {
            $entry = $state.$name
            $owned = Get-OwnedProcess $entry
            $port = if ($name -eq 'api') { $ApiPort } else { $WebPort }
            $services[$name] = @{
                running = [bool]$owned
                portListening = [bool]@($listeners | Where-Object LocalPort -eq $port).Count
                processId = $entry.processId
                startedAt = $entry.startedAt
            }
        }
        @{
            managed = $true; database = $state.database; agentEnabled = $state.agentEnabled
            services = $services; listeners = $listeners
        } | ConvertTo-Json -Depth 5
    }
    'Stop' {
        if (-not (Test-Path -LiteralPath $statePath)) { Write-Host 'No managed app processes found.'; return }
        $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
        foreach ($entry in @($state.web, $state.api)) {
            $process = Get-OwnedProcess $entry
            if ($process) { Stop-OwnedTree $process }
        }
        Write-Host 'Managed API and web processes stopped; the preview database is retained.'
    }
}
