#requires -Version 7.0
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$SourceManifest,
    [Parameter(Mandatory)][string]$Plugin,
    [Parameter(Mandatory)][string]$DesktopHost,
    [Parameter(Mandatory)][string]$RuntimeExecutable,
    [Parameter(Mandatory)][string]$CadWorker,
    [Parameter(Mandatory)][string]$WebDirectory,
    [Parameter(Mandatory)][string]$VcRedistDirectory,
    [string]$AutoCADRoot,
    [string]$ObjectArxSdkRoot,
    [ValidatePattern('^[a-fA-F0-9]{64}$')][string]$ObjectArxArchiveSha256,
    [Parameter(Mandatory)][string]$WebView2Installer,
    [Parameter(Mandatory)][ValidatePattern('^[a-fA-F0-9]{64}$')][string]$WebView2InstallerSha256,
    [string]$BuildPython = $env:GREEN_ATLAS_BUILD_PYTHON,
    [string]$SigningCertificateThumbprint
)

$ErrorActionPreference = 'Stop'
$repository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
function Assert-MicrosoftSignature([string]$Path) {
    $signature = Get-AuthenticodeSignature -LiteralPath $Path
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch '(^|,\s*)O=Microsoft Corporation(,|$)') {
        throw "Не подтверждена Microsoft Authenticode подпись: $Path"
    }
}
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
    if ($runtimeCheckExit -ne 0) { throw 'Для Setup требуется qualified API Python из проектного окружения.' }
    . ./scripts/windows-env.ps1
    $builderJson = & $BuildPython -c 'import json,sys,struct,PyInstaller; print(json.dumps(dict(platform=sys.platform,bits=8*struct.calcsize("P"),pyinstaller=PyInstaller.__version__)))'
    if ($LASTEXITCODE -ne 0) { throw 'Нужны desktop/build-requirements.txt в qualified API окружении.' }
    $builder = $builderJson | ConvertFrom-Json
    if ($builder.platform -ne 'win32' -or $builder.bits -ne 64 -or $builder.pyinstaller -ne '6.22.0') { throw 'Setup собирается только Windows Python x64 с PyInstaller 6.22.0.' }
    $WebView2Installer = (Resolve-Path -LiteralPath $WebView2Installer).Path
    if ([bool]$AutoCADRoot -eq [bool]$ObjectArxSdkRoot) { throw 'Укажите ровно один источник внешних AutoCAD зависимостей: установленный AutoCADRoot или ObjectArxSdkRoot.' }
    if ($ObjectArxSdkRoot -and -not $ObjectArxArchiveSha256) { throw 'Для SDK import-контракта нужен SHA256 официального архива.' }
    if ((Get-FileHash -LiteralPath $WebView2Installer -Algorithm SHA256).Hash -ne $WebView2InstallerSha256) {
        throw 'SHA offline WebView2 installer не совпадает с заданным.'
    }
    Assert-MicrosoftSignature $WebView2Installer
    $build = Join-Path $repository ('.runtime/autocad-bridge/windows-distribution/build-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $build | Out-Null
    $prereq = [ordered]@{ schema='green-atlas.offline-webview2/1'; file='MicrosoftEdgeWebView2RuntimeInstallerX64.exe'; sha256=$WebView2InstallerSha256.ToLowerInvariant(); publisher='Microsoft Corporation'; authenticode='Valid' }
    $prereqReceipt = Join-Path $build 'webview2-installer.json'
    [IO.File]::WriteAllText($prereqReceipt, ($prereq | ConvertTo-Json) + "`n", [Text.UTF8Encoding]::new($false))
    . ./scripts/windows-env.ps1
    $providerArguments = if ($AutoCADRoot) { @('--autocad', $AutoCADRoot) } else { @('--objectarx-sdk', $ObjectArxSdkRoot, '--objectarx-archive-sha256', $ObjectArxArchiveSha256) }
    $bundle = & $BuildPython scripts/desktop/windows_bundle.py assemble --source-manifest $SourceManifest --gui $Plugin --host $DesktopHost --runtime $RuntimeExecutable --worker $CadWorker --web $WebDirectory --crt $VcRedistDirectory @providerArguments --webview-installer $WebView2Installer --webview-receipt $prereqReceipt --output (Join-Path $build 'payload')
    if ($LASTEXITCODE -ne 0) { throw 'Сборка payload или preflight завершились с ошибкой.' }
    $bundle = ([string]$bundle).Trim()
    $dependency = Get-Content -LiteralPath (Join-Path $bundle 'Contents/Windows/dependency-inventory.json') -Raw | ConvertFrom-Json
    foreach ($entry in $dependency.copied_crt.PSObject.Properties) {
        Assert-MicrosoftSignature (Join-Path $bundle $entry.Name)
    }
    $previousPythonPath = $env:PYTHONPATH
    $previousConfig = $env:PYINSTALLER_CONFIG_DIR
    try {
        $env:PYTHONPATH = Join-Path $repository 'apps/api'
        $env:PYINSTALLER_CONFIG_DIR = Join-Path $build 'setup-cache'
        $log = Join-Path $build 'setup-build.log'
        . ./scripts/windows-env.ps1
        $resourceJson = & $BuildPython scripts/desktop/setup_resources.py arguments --repository $repository
        if ($LASTEXITCODE -ne 0) { throw 'Не удалось перечислить ресурсы самого frozen Setup.' }
        $resourceArguments = @($resourceJson | ConvertFrom-Json)
        . ./scripts/windows-env.ps1
        & $BuildPython -m PyInstaller --noconfirm --onefile --windowed --name GreenAtlasSetup --distpath (Join-Path $build 'dist') --workpath (Join-Path $build 'setup-work') --specpath (Join-Path $build 'setup-spec') --paths (Join-Path $repository 'apps/api') --collect-submodules app.distribution @resourceArguments --add-data "$bundle;payload/GreenAtlas.bundle" --manifest (Join-Path $PSScriptRoot 'installer/windows/setup.manifest') (Join-Path $PSScriptRoot 'installer/windows/setup_entry.py') *> $log
        if ($LASTEXITCODE -ne 0) { Get-Content -LiteralPath $log -Tail 60 | ForEach-Object { Write-Host $_ }; throw "Ошибка Setup.exe; журнал: $log" }
    } finally {
        [Environment]::SetEnvironmentVariable('PYTHONPATH', $previousPythonPath, 'Process')
        [Environment]::SetEnvironmentVariable('PYINSTALLER_CONFIG_DIR', $previousConfig, 'Process')
    }
    $setup = Join-Path $build 'dist/GreenAtlasSetup.exe'
    . ./scripts/windows-env.ps1
    & $BuildPython scripts/desktop/setup_resources.py verify --repository $repository --setup $setup > (Join-Path $build 'setup-resources.json')
    if ($LASTEXITCODE -ne 0) { throw 'Архив frozen Setup не содержит точные ресурсы проверяющего модуля.' }
    if ($SigningCertificateThumbprint) {
        $certificate = Get-Item -LiteralPath ('Cert:/CurrentUser/My/' + $SigningCertificateThumbprint)
        $signature = Set-AuthenticodeSignature -LiteralPath $setup -Certificate $certificate -HashAlgorithm SHA256
        if ($signature.Status -ne 'Valid') { throw 'Подпись конечного Setup.exe не подтверждена.' }
    }
    $receipt = [ordered]@{
        schema='green-atlas.windows-setup-build/1'; state='built-candidate'; setup='GreenAtlasSetup.exe'
        product_version=(Get-Content -LiteralPath (Join-Path $bundle 'distribution-manifest.json') -Raw | ConvertFrom-Json).layout.version
        sha256=(Get-FileHash -LiteralPath $setup -Algorithm SHA256).Hash.ToLowerInvariant()
        bytes=(Get-Item -LiteralPath $setup).Length
        source_manifest_sha256=(Get-FileHash -LiteralPath $SourceManifest -Algorithm SHA256).Hash.ToLowerInvariant()
        distribution_manifest_sha256=(Get-FileHash -LiteralPath (Join-Path $bundle 'distribution-manifest.json') -Algorithm SHA256).Hash.ToLowerInvariant()
        authenticode=[string](Get-AuthenticodeSignature -LiteralPath $setup).Status
        windows_install_execution_qualified=$false; user_profile_touched=$false
    }
    [IO.File]::WriteAllText((Join-Path $build 'setup-build-receipt.json'), ($receipt | ConvertTo-Json -Depth 8) + "`n", [Text.UTF8Encoding]::new($false))
    Write-Output $setup
} finally { Pop-Location }
