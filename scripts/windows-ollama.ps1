[CmdletBinding()]
param(
    [ValidateSet('Setup', 'Start', 'Status', 'Unload', 'Stop')]
    [string]$Action = 'Status',
    [string]$RuntimeRoot = $env:GREEN_ATLAS_RUNTIME_ROOT
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if (-not $RuntimeRoot) {
    $RuntimeRoot = Join-Path (Split-Path $PSScriptRoot -Parent) '../green-atlas-runtime'
}
$RuntimeRoot = [IO.Path]::GetFullPath($RuntimeRoot)
$version = '0.33.3'
$model = 'qwen3.5:4b'
$archiveSha256 = '52cb36a62e7e501f61514f60212dec7117b6c098811357585e02fffe32d2fcd7'
$binaryRoot = Join-Path $RuntimeRoot "ollama-$version"
$executable = Join-Path $binaryRoot 'ollama.exe'
$statePath = Join-Path $RuntimeRoot 'server-process.json'
$endpoint = 'http://127.0.0.1:11434'

function Get-ManagedServer {
    if (-not (Test-Path -LiteralPath $statePath)) { return $null }
    $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
    $server = Get-Process -Id $state.processId -ErrorAction SilentlyContinue
    if ($server -and $server.Path -eq $executable -and
        $server.StartTime.ToUniversalTime().Ticks -eq ([datetime]$state.startedAt).ToUniversalTime().Ticks) {
        return $server
    }
    return $null
}

function Stop-ManagedTree($process) {
    foreach ($child in @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $($process.Id)")) {
        $childProcess = Get-Process -Id $child.ProcessId -ErrorAction SilentlyContinue
        if ($childProcess -and $childProcess.StartTime -ge $process.StartTime) {
            Stop-ManagedTree $childProcess
        }
    }
    Stop-Process -InputObject $process -ErrorAction SilentlyContinue
}

function Start-LocalServer {
    if (-not (Test-Path -LiteralPath $executable)) {
        throw 'Ollama is not installed at this RuntimeRoot. Run -Action Setup first.'
    }
    $listener = @(Get-NetTCPConnection -State Listen -LocalPort 11434 -ErrorAction SilentlyContinue)
    if ($listener.Count -gt 0) {
        $managed = Get-ManagedServer
        if (-not $managed -or @($listener | Where-Object {
            $_.OwningProcess -ne $managed.Id -or $_.LocalAddress -ne '127.0.0.1'
        }).Count -gt 0) {
            throw 'Port 11434 belongs to another service; this script will not replace it.'
        }
        return
    }
    New-Item -ItemType Directory -Path $RuntimeRoot, (Join-Path $RuntimeRoot 'models') -Force | Out-Null
    $settings = @{
        OLLAMA_HOST = '127.0.0.1:11434'
        OLLAMA_MODELS = (Join-Path $RuntimeRoot 'models')
        OLLAMA_NO_CLOUD = '1'
        OLLAMA_NUM_PARALLEL = '1'
        OLLAMA_MAX_LOADED_MODELS = '1'
        OLLAMA_CONTEXT_LENGTH = '8192'
        OLLAMA_KEEP_ALIVE = '10m'
    }
    $previous = @{}
    try {
        foreach ($name in $settings.Keys) {
            $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
            [Environment]::SetEnvironmentVariable($name, $settings[$name], 'Process')
        }
        $server = Start-Process -FilePath $executable -ArgumentList 'serve' -WindowStyle Hidden -PassThru `
            -WorkingDirectory $RuntimeRoot `
            -RedirectStandardOutput (Join-Path $RuntimeRoot 'server.stdout.log') `
            -RedirectStandardError (Join-Path $RuntimeRoot 'server.stderr.log')
    } finally {
        foreach ($name in $previous.Keys) {
            [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process')
        }
    }
    @{ processId = $server.Id; startedAt = $server.StartTime.ToUniversalTime().ToString('o') } |
        ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding utf8
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        if ($server.HasExited) { throw "Ollama exited. See $RuntimeRoot\server.stderr.log" }
        try {
            Invoke-RestMethod -Uri "$endpoint/api/version" -TimeoutSec 2 | Out-Null
            Write-Host "Ollama ready at $endpoint (PID $($server.Id))."
            return
        } catch { Start-Sleep -Seconds 1 }
    }
    throw "Ollama did not become ready. See $RuntimeRoot\server.stderr.log"
}

switch ($Action) {
    'Setup' {
        New-Item -ItemType Directory -Path (Join-Path $RuntimeRoot 'downloads') -Force | Out-Null
        $archive = Join-Path $RuntimeRoot "downloads/ollama-windows-amd64-v$version.zip"
        $marker = Join-Path $binaryRoot 'archive-sha256.txt'
        if (-not (Test-Path -LiteralPath $marker)) {
            $drive = Get-PSDrive -Name ([IO.Path]::GetPathRoot($RuntimeRoot).TrimEnd('\', ':'))
            if ($drive.Free -lt 10GB) {
                throw 'Setup needs 10 GiB free. Choose another disk with -RuntimeRoot.'
            }
            if (-not (Test-Path -LiteralPath $archive) -or
                (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $archiveSha256) {
                & curl.exe --fail --location --retry 3 --continue-at - --output $archive `
                    "https://github.com/ollama/ollama/releases/download/v$version/ollama-windows-amd64.zip"
                if ($LASTEXITCODE -ne 0) { throw 'Ollama archive download failed.' }
            }
            if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $archiveSha256) {
                throw 'Ollama archive checksum differs from the official release digest.'
            }
            Expand-Archive -LiteralPath $archive -DestinationPath $binaryRoot -Force
            Set-Content -LiteralPath $marker -Value $archiveSha256 -Encoding ascii
        }
        if ((Get-Content -LiteralPath $marker -Raw).Trim() -ne $archiveSha256) {
            throw 'The installation marker differs from the pinned official release.'
        }
        Start-LocalServer
        Write-Host "Downloading $model if missing. Model data stays under $RuntimeRoot."
        Invoke-RestMethod -Uri "$endpoint/api/pull" -Method Post -ContentType 'application/json' `
            -Body (@{ model = $model; stream = $false } | ConvertTo-Json) -TimeoutSec 3600
    }
    'Start' { Start-LocalServer }
    'Status' {
        Invoke-RestMethod -Uri "$endpoint/api/version" -TimeoutSec 3
        Invoke-RestMethod -Uri "$endpoint/api/tags" -TimeoutSec 3
        Invoke-RestMethod -Uri "$endpoint/api/ps" -TimeoutSec 3
    }
    'Unload' {
        if (-not (Get-ManagedServer)) { throw 'No managed Ollama server found for this RuntimeRoot.' }
        Invoke-RestMethod -Uri "$endpoint/api/generate" -Method Post -ContentType 'application/json' `
            -Body (@{ model = $model; keep_alive = 0; stream = $false } | ConvertTo-Json) -TimeoutSec 60
    }
    'Stop' {
        $server = Get-ManagedServer
        if ($server) {
            Stop-ManagedTree $server
            $server.WaitForExit(10000) | Out-Null
            Write-Host 'Managed Ollama server stopped.'
        } else { Write-Host 'No managed Ollama server is running.' }
    }
}
