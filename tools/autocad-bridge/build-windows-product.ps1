#requires -Version 7.0
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$SourceManifest,
    [Parameter(Mandatory)][string]$SdkRoot,
    [Parameter(Mandatory)][string]$WebView2SdkRoot,
    [Parameter(Mandatory)][string]$VcRedistDirectory,
    [string]$AutoCADRoot,
    [ValidatePattern('^[a-fA-F0-9]{64}$')][string]$SdkPackageSha256,
    [Parameter(Mandatory)][string]$WebView2Installer,
    [Parameter(Mandatory)][ValidatePattern('^[a-fA-F0-9]{64}$')][string]$WebView2InstallerSha256,
    [string]$BuildPython = $env:GREEN_ATLAS_BUILD_PYTHON,
    [string]$SigningCertificateThumbprint,
    [switch]$ValidateOnly
)

$ErrorActionPreference = 'Stop'
$repository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
Push-Location $repository
$previousCorepackNetwork = [Environment]::GetEnvironmentVariable('COREPACK_ENABLE_NETWORK', 'Process')
try {
    . ./scripts/windows-env.ps1
    if (-not $BuildPython) { $BuildPython = Join-Path $repository 'apps/api/.venv/Scripts/python.exe' }
    $BuildPython = (Resolve-Path -LiteralPath $BuildPython).Path
    $SourceManifest = (Resolve-Path -LiteralPath $SourceManifest).Path
    . ./scripts/windows-env.ps1
    $sourceCheck = & $BuildPython scripts/desktop/package_preflight.py check-source --source-root $repository --source-manifest $SourceManifest
    $sourceCheckExit = $LASTEXITCODE
    $sourceCheck | ForEach-Object { Write-Host $_ }
    if ($sourceCheckExit -ne 0) { throw 'Source seal не соответствует текущим исходникам; сборка остановлена до компиляции.' }
    foreach ($path in @($SdkRoot, $WebView2SdkRoot, $VcRedistDirectory) + @($AutoCADRoot | Where-Object { $_ })) {
        if (-not (Test-Path -LiteralPath $path -PathType Container)) { throw "Не найден локальный сборочный каталог: $path" }
    }
    if (-not $AutoCADRoot -and -not $SdkPackageSha256) { throw 'Без AutoCADRoot нужен SHA256 официального SDK для проверки внешнего import-контракта.' }
    if (-not (Test-Path -LiteralPath $WebView2Installer -PathType Leaf)) { throw 'Предоставьте offline Evergreen installer x64, без bootstrapper загрузки.' }
    if ((Get-FileHash -LiteralPath $WebView2Installer -Algorithm SHA256).Hash -ne $WebView2InstallerSha256) { throw 'SHA offline installer не совпадает.' }
    # Сборка не устанавливает toolchain и не скачивает pnpm через Corepack.
    $env:COREPACK_ENABLE_NETWORK = '0'
    . ./scripts/windows-env.ps1
    $nodeIdentity = & node -p 'JSON.stringify({platform:process.platform,arch:process.arch})'
    if ($LASTEXITCODE -ne 0) { throw 'Нужен установленный Node.js x64 для Windows.' }
    $node = $nodeIdentity | ConvertFrom-Json
    if ($node.platform -ne 'win32' -or $node.arch -ne 'x64') { throw 'Нужен Node.js Windows x64.' }
    $manager = (Get-Content -LiteralPath (Join-Path $repository 'package.json') -Raw | ConvertFrom-Json).packageManager
    . ./scripts/windows-env.ps1
    $pnpmVersion = & pnpm --version
    if ($LASTEXITCODE -ne 0 -or ('pnpm@' + ([string]$pnpmVersion).Trim()) -ne $manager) { throw "Нужен заранее подготовленный $manager; сеть Corepack отключена." }
    if (-not (Test-Path -LiteralPath (Join-Path $repository 'node_modules') -PathType Container)) { throw 'Установите зависимости pnpm из lockfile до сборки.' }
    if ($ValidateOnly) {
        . ./scripts/windows-env.ps1
        & ./tools/autocad-bridge/build-windows-2027.ps1 -Target Gui -SdkRoot $SdkRoot -ValidateOnly
        . ./scripts/windows-env.ps1
        & ./tools/autocad-bridge/build-windows-2027.ps1 -Target Core -SdkRoot $SdkRoot -ValidateOnly
        . ./scripts/windows-env.ps1
        & ./tools/autocad-bridge/build-desktop-windows.ps1 -WebView2SdkRoot $WebView2SdkRoot -ValidateOnly
        . ./scripts/windows-env.ps1
        & ./tools/autocad-bridge/build-desktop-runtime-windows.ps1 -SourceManifest $SourceManifest -BuildPython $BuildPython -ValidateOnly
        Write-Host 'Предпосылки проверены. Компиляция, Setup и Windows приёмка не выполнялись.'
        return
    }
    # Каждый компонент создаёт новый каталог; старые локальные бинарные файлы не подставляются.
    . ./scripts/windows-env.ps1
    $gui = & ./tools/autocad-bridge/build-windows-2027.ps1 -Target Gui -SdkRoot $SdkRoot -SigningCertificateThumbprint $SigningCertificateThumbprint
    . ./scripts/windows-env.ps1
    $core = & ./tools/autocad-bridge/build-windows-2027.ps1 -Target Core -SdkRoot $SdkRoot -SigningCertificateThumbprint $SigningCertificateThumbprint
    . ./scripts/windows-env.ps1
    $hostBinary = & ./tools/autocad-bridge/build-desktop-windows.ps1 -WebView2SdkRoot $WebView2SdkRoot -SigningCertificateThumbprint $SigningCertificateThumbprint
    . ./scripts/windows-env.ps1
    $runtimeBinary = & ./tools/autocad-bridge/build-desktop-runtime-windows.ps1 -SourceManifest $SourceManifest -BuildPython $BuildPython -SigningCertificateThumbprint $SigningCertificateThumbprint
    $previousApi = [Environment]::GetEnvironmentVariable('VITE_API_URL', 'Process')
    try {
        $env:VITE_API_URL = ''
        . ./scripts/windows-env.ps1
        $webLog = & pnpm run build 2>&1
        $webExit = $LASTEXITCODE
        $webLog | ForEach-Object { Write-Host $_ }
        if ($webExit -ne 0) { throw 'Сборка Web завершилась с ошибкой.' }
    } finally { [Environment]::SetEnvironmentVariable('VITE_API_URL', $previousApi, 'Process') }
    . ./scripts/windows-env.ps1
    & $BuildPython scripts/desktop/windows_bundle.py web-receipt --directory (Join-Path $repository 'apps/web/dist') --source-manifest $SourceManifest
    if ($LASTEXITCODE -ne 0) { throw 'Не создана квитанция текущего Web.' }
    . ./scripts/windows-env.ps1
    $externalProvider = if ($AutoCADRoot) { @{ AutoCADRoot = $AutoCADRoot } } else { @{ ObjectArxSdkRoot = $SdkRoot; ObjectArxArchiveSha256 = $SdkPackageSha256 } }
    & ./tools/autocad-bridge/package-windows.ps1 -SourceManifest $SourceManifest -Plugin $gui -DesktopHost $hostBinary -RuntimeExecutable $runtimeBinary -CadWorker $core -WebDirectory (Join-Path $repository 'apps/web/dist') -VcRedistDirectory $VcRedistDirectory @externalProvider -WebView2Installer $WebView2Installer -WebView2InstallerSha256 $WebView2InstallerSha256 -BuildPython $BuildPython -SigningCertificateThumbprint $SigningCertificateThumbprint
} finally {
    [Environment]::SetEnvironmentVariable('COREPACK_ENABLE_NETWORK', $previousCorepackNetwork, 'Process')
    Pop-Location
}
