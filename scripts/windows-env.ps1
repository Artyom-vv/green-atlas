[CmdletBinding()]
param(
    [System.Collections.IDictionary]$PreviousEnvironment
)

# Dot-source this script before launching a development command. The child
# scope keeps path variables out of the caller; only Process env is changed.
& {
    param($ScriptDirectory, $Previous)

    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        throw 'windows-env.ps1 is intended for Windows development sessions.'
    }

    $repository = [IO.Path]::GetFullPath((Split-Path $ScriptDirectory -Parent))
    $runtime = Join-Path $repository '.runtime'
    $cache = Join-Path ([IO.Path]::GetPathRoot($repository)) '.dev-cache'
    $temporary = Join-Path $runtime 'tmp'
    $environment = @{
        TEMP = $temporary
        TMP = $temporary
        TMPDIR = $temporary
        NODE_COMPILE_CACHE = Join-Path $runtime 'cache/node-compile'
        npm_config_cache = Join-Path $cache 'npm-cache'
        PIP_CACHE_DIR = Join-Path $cache 'pip-cache'
        UV_CACHE_DIR = Join-Path $cache 'uv-cache'
        PYTEST_DEBUG_TEMPROOT = Join-Path $runtime 'pytest'
    }

    foreach ($directory in ($environment.Values | Select-Object -Unique)) {
        New-Item -ItemType Directory -Path $directory -Force `
            -ErrorAction Stop | Out-Null
    }

    foreach ($name in $environment.Keys) {
        if ($null -ne $Previous -and -not $Previous.Contains($name)) {
            $Previous[$name] = [Environment]::GetEnvironmentVariable(
                $name, 'Process'
            )
        }
        [Environment]::SetEnvironmentVariable(
            $name, $environment[$name], 'Process'
        )
    }
} $PSScriptRoot $PreviousEnvironment
