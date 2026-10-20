#requires -Version 7.0
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$SourceManifest,
    [string]$BuildPython = $env:GREEN_ATLAS_BUILD_PYTHON,
    [string]$SigningCertificateThumbprint,
    [switch]$ValidateOnly
)

$ErrorActionPreference = 'Stop'
$repository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
Push-Location $repository
try {
    . ./scripts/windows-env.ps1
    if (-not $BuildPython) { $BuildPython = Join-Path $repository 'apps/api/.venv/Scripts/python.exe' }
    $BuildPython = (Resolve-Path -LiteralPath $BuildPython).Path
    $SourceManifest = (Resolve-Path -LiteralPath $SourceManifest).Path
    . ./scripts/windows-env.ps1
    $runtimeCheck = & $BuildPython apps/api/scripts/check_runtime.py 2>&1
    $runtimeCheckExit = $LASTEXITCODE
    $runtimeCheck | ForEach-Object { Write-Host $_ }
    if ($runtimeCheckExit -ne 0) { throw 'API окружение не соответствует .python-version/uv.lock и native accelerators.' }
    . ./scripts/windows-env.ps1
    $description = & $BuildPython -c 'import json,sys,struct,PyInstaller; print(json.dumps(dict(platform=sys.platform,bits=8*struct.calcsize("P"),pyinstaller=PyInstaller.__version__)))'
    if ($LASTEXITCODE -ne 0) { throw 'Подготовьте qualified Python и desktop/build-requirements.txt.' }
    $identity = $description | ConvertFrom-Json
    if ($identity.platform -ne 'win32' -or $identity.bits -ne 64 -or $identity.pyinstaller -ne '6.22.0') {
        throw 'Нужны Windows Python x64 и PyInstaller 6.22.0 из проектного окружения.'
    }
    . ./scripts/windows-env.ps1
    $sourceCheck = & $BuildPython scripts/desktop/package_preflight.py check-source --source-root $repository --source-manifest $SourceManifest 2>&1
    $sourceCheckExit = $LASTEXITCODE
    $sourceCheck | ForEach-Object { Write-Host $_ }
    if ($sourceCheckExit -ne 0) { throw 'Исходники frozen API не соответствуют SourceManifest.' }
    if ($ValidateOnly) { Write-Host 'Предпосылки frozen API найдены; компиляция не выполнялась.'; return }
    $build = Join-Path $repository ('.runtime/autocad-bridge/runtime-windows/build-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $build | Out-Null
    $previous = @{}
    foreach ($name in @('PYTHONPATH', 'PYINSTALLER_CONFIG_DIR')) {
        $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    }
    try {
        $env:PYTHONPATH = Join-Path $repository 'apps/api'
        $env:PYINSTALLER_CONFIG_DIR = Join-Path $build 'cache'
        $log = Join-Path $build 'compile.log'
        . ./scripts/windows-env.ps1
        $compileArguments = @('-m', 'PyInstaller', '--noconfirm', '--distpath', (Join-Path $build 'dist'), '--workpath', (Join-Path $build 'work'), (Join-Path $PSScriptRoot 'desktop/runtime.spec'))
        if ($env:GITHUB_ACTIONS -eq 'true') {
            # Общий timeout job может оборвать выгрузку диагностики. Компилятор
            # получает собственный срок и Job Object, как остальные фазы CI.
            Write-Host 'Windows CI phase: frozen-api-compile'
            & $BuildPython -u scripts/desktop/windows_qa_command.py --log $log --timeout 1200 -- @compileArguments | ForEach-Object { Write-Host $_ }
        } else {
            & $BuildPython @compileArguments *> $log
        }
        $compileExit = $LASTEXITCODE
        if ($compileExit -ne 0) { Get-Content -LiteralPath $log -Tail 60 | ForEach-Object { Write-Host $_ }; throw "Ошибка frozen API; журнал: $log" }
        $binary = Join-Path $build 'dist/GreenAtlasRuntime/GreenAtlasRuntime.exe'
        if (-not (Test-Path -LiteralPath $binary -PathType Leaf)) { throw 'PyInstaller не создал Windows runtime.' }
        if ($SigningCertificateThumbprint) {
            $certificate = Get-Item -LiteralPath ('Cert:/CurrentUser/My/' + $SigningCertificateThumbprint)
            $signature = Set-AuthenticodeSignature -LiteralPath $binary -Certificate $certificate -HashAlgorithm SHA256
            if ($signature.Status -ne 'Valid') { throw 'Подпись frozen runtime не подтверждена.' }
        }
        . ./scripts/windows-env.ps1
        & $BuildPython scripts/desktop/frozen_provenance.py --runtime $binary --source-root $repository --source-manifest $SourceManifest --output (Join-Path (Split-Path $binary -Parent) 'frozen-api-provenance.json')
        if ($LASTEXITCODE -ne 0) { throw 'PYZ runtime не соответствует source seal.' }
        Write-Output $binary
    } finally {
        foreach ($name in $previous.Keys) { [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process') }
    }
} finally { Pop-Location }
