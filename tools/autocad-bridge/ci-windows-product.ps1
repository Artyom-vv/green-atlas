#requires -Version 7.0
[CmdletBinding()]
param(
    [string]$BuildPython,
    [string]$OutputDirectory = '.runtime/autocad-bridge/ci-product'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
Push-Location $repository
try {
    . ./scripts/windows-env.ps1
    if (-not $BuildPython) { $BuildPython = Join-Path $repository 'apps/api/.venv/Scripts/python.exe' }
    $BuildPython = (Resolve-Path -LiteralPath $BuildPython).Path
    $productVersion = & ./tools/autocad-bridge/windows-product-version.ps1
    $output = if ([IO.Path]::IsPathRooted($OutputDirectory)) { [IO.Path]::GetFullPath($OutputDirectory) } else { [IO.Path]::GetFullPath((Join-Path $repository $OutputDirectory)) }
    if (-not $output.StartsWith($repository + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Вывод должен оставаться внутри checkout.' }
    if (Test-Path -LiteralPath $output) { throw 'Нужен новый OutputDirectory для полного продукта.' }
    $evidence = Join-Path $output 'evidence'
    $deliverable = Join-Path $output 'deliverable'
    $tools = Join-Path $output 'tools'
    New-Item -ItemType Directory -Path $evidence, $deliverable, $tools | Out-Null
    $inputs = Get-Content tools/autocad-bridge/windows-product-inputs.json -Raw | ConvertFrom-Json
    $shared = Get-Content tools/autocad-bridge/windows-ci-inputs.json -Raw | ConvertFrom-Json
    if ($inputs.schema -ne 'green-atlas.windows-product-inputs/1' -or $shared.schema -ne 'green-atlas.windows-ci-inputs/1') { throw 'Неизвестные закреплённые входы сборки.' }

    function Receive-Pinned([string]$Url, [string]$Expected, [string]$Destination, [string]$HostPattern) {
        $uri = [uri]$Url
        if (-not $uri.IsAbsoluteUri -or $uri.Scheme -ne 'https' -or $uri.UserInfo -or $uri.Fragment -or $uri.DnsSafeHost -notmatch $HostPattern) { throw 'Неофициальный URL сборочного входа.' }
        if ($Expected -notmatch '^[a-fA-F0-9]{64}$') { throw 'Нужен закреплённый SHA256.' }
        . ./scripts/windows-env.ps1
        Invoke-WebRequest -Uri $uri -OutFile $Destination -MaximumRedirection 0
        $observed = (Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($observed -ne $Expected.ToLowerInvariant()) { throw "SHA256 не совпадает: $Destination" }
        [ordered]@{ url=$Url; sha256=$observed; bytes=(Get-Item -LiteralPath $Destination).Length }
    }

    $sdkArchive = Join-Path $tools 'objectarx-2027.archive'
    $innoArchive = Join-Path $tools 'inno-setup.exe'
    . ./scripts/windows-env.ps1
    Invoke-WebRequest -Uri $inputs.inno_setup.url -OutFile $innoArchive -MaximumRedirection 5
    if ((Get-FileHash -LiteralPath $innoArchive -Algorithm SHA256).Hash -ne $inputs.inno_setup.sha256) { throw 'SHA компилятора Inno Setup не совпадает.' }
    $innoSignature = Get-AuthenticodeSignature -LiteralPath $innoArchive
    if ($innoSignature.Status -ne 'Valid' -or $innoSignature.SignerCertificate.Subject -notmatch 'Pyrsys') { throw 'Не подтверждена подпись официального Inno Setup.' }
    $innoRoot = Join-Path $tools 'inno'
    . ./scripts/windows-env.ps1
    $innoProcess = Start-Process -FilePath $innoArchive -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/CURRENTUSER', '/NOICONS', ('/DIR="' + $innoRoot + '"')) -Wait -PassThru
    if ($innoProcess.ExitCode -ne 0) { throw 'Не подготовлен закреплённый Inno Setup.' }
    # Проверяем Pascal/ISS до длительной сборки SDK/API. Этот образец не запускается
    # и не попадает в deliverable; действующий мастер позже собирается с exact backend.
    $syntaxRoot = Join-Path $tools 'wizard-syntax'
    New-Item -ItemType Directory -Path $syntaxRoot | Out-Null
    . ./scripts/windows-env.ps1
    & (Join-Path $innoRoot 'ISCC.exe') ('/DBackend=' + $innoArchive) ('/DBackendSHA=' + $inputs.inno_setup.sha256) ('/DOutput=' + $syntaxRoot) ('/DProductVersion=' + $productVersion) tools/autocad-bridge/installer/windows/wizard.iss *> (Join-Path $evidence 'wizard-syntax.log')
    if ($LASTEXITCODE -ne 0) { Get-Content (Join-Path $evidence 'wizard-syntax.log') -Tail 50 | ForEach-Object { Write-Host $_ }; throw 'Native мастер не компилируется.' }
    Remove-Item -LiteralPath $syntaxRoot -Recurse
    $sdkDownload = Receive-Pinned $inputs.objectarx.url $inputs.objectarx.sha256 $sdkArchive '(^|\.)autodesk\.(com|net)$'
    $sevenzip = (Get-Command 7z.exe -ErrorAction Stop).Source
    $unpacked = Join-Path $tools 'objectarx'
    . ./scripts/windows-env.ps1
    & $sevenzip x -y ('-o' + $unpacked) -- $sdkArchive *> (Join-Path $evidence 'sdk-extraction.log')
    if ($LASTEXITCODE -ne 0) { throw 'Не распакован SDK; SDK EXE не исполняется.' }
    $items = @(Get-ChildItem -LiteralPath $unpacked -Recurse -Force)
    if (@($items | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }).Count) { throw 'SDK содержит reparse point.' }
    $roots = @($items | Where-Object { -not $_.PSIsContainer -and $_.Name -eq 'AcRxDefault.def' -and $_.Directory.Name -eq 'inc' } | ForEach-Object { $_.Directory.Parent.FullName } | Where-Object { Test-Path -LiteralPath (Join-Path $_ 'lib-x64') -PathType Container } | Sort-Object -Unique)
    if ($roots.Count -ne 1) { throw 'Нужен один корень ObjectARX SDK 2027.' }
    $sdk = $roots[0]
    $webviewPackage = Join-Path $tools 'webview2.nupkg'
    $webviewDownload = Receive-Pinned $shared.webview2.url $shared.webview2.sha256 $webviewPackage '^api\.nuget\.org$'
    $webviewSdk = Join-Path $tools 'webview2-sdk'
    [IO.Compression.ZipFile]::ExtractToDirectory($webviewPackage, $webviewSdk)
    $offline = Join-Path $tools 'MicrosoftEdgeWebView2RuntimeInstallerX64.exe'
    $offlineDownload = Receive-Pinned $inputs.webview_runtime.url $inputs.webview_runtime.sha256 $offline '^msedge\.sf\.dl\.delivery\.mp\.microsoft\.com$'
    if ((Get-Item -LiteralPath $offline).Length -lt 50MB) { throw 'Для автономной установки нужен полный Runtime, не bootstrapper.' }
    $signature = Get-AuthenticodeSignature -LiteralPath $offline
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch '(^|,\s*)O=Microsoft Corporation(,|$)') { throw 'Не подтверждена подпись Microsoft Runtime.' }
    [ordered]@{ schema='green-atlas.product-downloads/1'; objectarx=$sdkDownload; sdk_executed=$false; webview2_sdk=$webviewDownload; webview2_runtime=$offlineDownload; runtime_authenticode=[string]$signature.Status; runtime_version=$inputs.webview_runtime.version } | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $evidence 'downloads.json') -Encoding utf8

    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio/Installer/vswhere.exe'
    . ./scripts/windows-env.ps1
    $installation = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    if ($LASTEXITCODE -ne 0 -or -not $installation) { throw 'Нет MSVC x64 toolchain.' }
    $installation = ([string]$installation).Trim()
    . ./scripts/windows-env.ps1
    & (Join-Path $installation 'Common7/Tools/Launch-VsDevShell.ps1') -Arch amd64 -HostArch amd64 -SkipAutomaticLocation | ForEach-Object { Write-Host $_ }
    if ($env:VSCMD_ARG_TGT_ARCH -ne 'x64') { throw 'Не активировано окружение MSVC x64.' }
    $redistRoot = Join-Path $installation 'VC/Redist/MSVC'
    $crtCandidates = @(Get-ChildItem -LiteralPath $redistRoot -Directory -Recurse | Where-Object { $_.Name -eq 'Microsoft.VC143.CRT' -and $_.Parent.Name -eq 'x64' -and $_.FullName -notmatch '(?i)[\\/](onecore|debug_nonredist|spectre)[\\/]' -and (Test-Path -LiteralPath (Join-Path $_.FullName 'vcruntime140_1.dll')) } | Sort-Object FullName -Descending)
    if (-not $crtCandidates.Count) { throw 'Нет локального Microsoft.VC143.CRT x64 redist.' }
    $crt = $crtCandidates[0].FullName
    . ./scripts/windows-env.ps1
    & $BuildPython scripts/desktop/seal_git_source.py $repository $evidence *> (Join-Path $evidence 'source-seal.log')
    if ($LASTEXITCODE -ne 0) { throw 'Git source seal не создан.' }
    $manifest = Join-Path $evidence 'source-manifest.json'
    $options = @{
        SourceManifest=$manifest; SdkRoot=$sdk; SdkPackageSha256=$inputs.objectarx.sha256
        WebView2SdkRoot=$webviewSdk; VcRedistDirectory=$crt
        WebView2Installer=$offline; WebView2InstallerSha256=$inputs.webview_runtime.sha256
        BuildPython=$BuildPython
    }
    . ./scripts/windows-env.ps1
    $setupPaths = @(& ./tools/autocad-bridge/build-windows-product.ps1 @options)
    if ($setupPaths.Count -ne 1 -or -not (Test-Path -LiteralPath ([string]$setupPaths[0]) -PathType Leaf)) { throw 'Product builder не вернул единственный Setup.exe.' }
    $setup = [IO.Path]::GetFullPath([string]$setupPaths[0])
    $build = [IO.Directory]::GetParent([IO.Path]::GetDirectoryName($setup)).FullName
    Copy-Item -LiteralPath (Join-Path $build 'setup-build-receipt.json') -Destination (Join-Path $evidence 'setup-build-receipt.json')
    Copy-Item -LiteralPath (Join-Path $build 'setup-resources.json') -Destination (Join-Path $evidence 'setup-resources.json')
    . ./scripts/windows-env.ps1
    $wizard = & ./tools/autocad-bridge/build-windows-installer-wizard.ps1 -Backend $setup -BackendReceipt (Join-Path $build 'setup-build-receipt.json') -Compiler (Join-Path $innoRoot 'ISCC.exe') -OutputDirectory (Join-Path $build 'wizard')
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $wizard -PathType Leaf)) { throw 'Нет итогового Windows мастера.' }
    Copy-Item -LiteralPath (Join-Path $build 'wizard-build-receipt.json') -Destination (Join-Path $evidence 'wizard-build-receipt.json')
    Copy-Item -LiteralPath (Join-Path $build 'wizard/wizard-compile.log') -Destination (Join-Path $evidence 'wizard-compile.log')
    $bundle = Join-Path $build 'payload/GreenAtlas.bundle'
    $layout = Join-Path $bundle 'Contents/Windows'
    Copy-Item -LiteralPath (Join-Path $bundle 'distribution-manifest.json') -Destination (Join-Path $evidence 'distribution-manifest.json')
    Copy-Item -LiteralPath (Join-Path $layout 'dependency-inventory.json') -Destination (Join-Path $evidence 'dependency-inventory.json')
    Copy-Item -LiteralPath (Join-Path $layout 'Runtime/frozen-api-provenance.json') -Destination (Join-Path $evidence 'frozen-api-provenance.json')
    Copy-Item -LiteralPath (Join-Path $layout 'BuildEvidence') -Destination (Join-Path $evidence 'BuildEvidence') -Recurse
    . ./scripts/windows-env.ps1
    & $BuildPython scripts/desktop/package_preflight.py check-source --source-root $repository --source-manifest $manifest *> (Join-Path $evidence 'source-recheck.log')
    if ($LASTEXITCODE -ne 0) { throw 'Исходники изменились во время полной сборки.' }
    $binary = Join-Path $deliverable ('GreenAtlas-' + $productVersion + '-Windows-x64-Setup-2.exe')
    Copy-Item -LiteralPath $wizard -Destination $binary
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'windows-2027/INSTALL-WINDOWS.md') -Destination (Join-Path $deliverable 'README-Windows.md')
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'windows-2027/render-resources.example.json') -Destination (Join-Path $deliverable 'render-resources.example.json')
    $receipt = [ordered]@{
        schema='green-atlas.windows-product-build/1'; state='built-candidate'; setup=[IO.Path]::GetFileName($binary)
        product_version=$productVersion
        git_commit=(& git rev-parse HEAD).Trim(); source_manifest_sha256=(Get-FileHash -LiteralPath $manifest -Algorithm SHA256).Hash.ToLowerInvariant()
        sha256=(Get-FileHash -LiteralPath $binary -Algorithm SHA256).Hash.ToLowerInvariant(); bytes=(Get-Item -LiteralPath $binary).Length
        every_component_built=$true; full_web_built=$true; offline_webview2_included=$true; crt_included=$true
        autocad_provider_resolution='objectarx-sdk-import-contract'; sdk_included=$false; autodesk_modules_included=$false
        install_execution_qualified=$false; autocad_execution_qualified=$false; blender_execution_qualified=$false
        product_qualified=$false; authenticode=[string](Get-AuthenticodeSignature -LiteralPath $binary).Status
        installer_revision=2; native_wizard=$true; backend=$setup; backend_sha256=(Get-FileHash -LiteralPath $setup -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    $receipt | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $deliverable 'product-build-receipt.json') -Encoding utf8
    Write-Host "Полный Setup собран: $binary"
} finally { Pop-Location }
