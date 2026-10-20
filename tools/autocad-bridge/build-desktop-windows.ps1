#requires -Version 7.0
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$WebView2SdkRoot,
    [string]$OutputDirectory,
    [string]$Version,
    [string]$SigningCertificateThumbprint,
    [switch]$ValidateOnly
)
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
Push-Location $root
try {
    . ./scripts/windows-env.ps1
    $productVersion = & ./tools/autocad-bridge/windows-product-version.ps1
    if (-not $Version) { $Version = $productVersion }
    if ($Version -ne $productVersion) { throw 'Desktop должен собираться с версией текущего продукта.' }
    if ($Version -notmatch '^[0-9A-Za-z][0-9A-Za-z.-]{0,63}$') { throw 'Некорректная версия desktop.' }
    if ($SigningCertificateThumbprint -and $SigningCertificateThumbprint -notmatch '^[0-9A-Fa-f]{40}$') { throw 'Некорректный thumbprint сертификата подписи.' }
    $sdk = (Resolve-Path -LiteralPath $WebView2SdkRoot).Path
    $header = Join-Path $sdk 'build/native/include/WebView2.h'
    $loader = Join-Path $sdk 'build/native/x64/WebView2LoaderStatic.lib'
    $nuspec = Join-Path $sdk 'Microsoft.Web.WebView2.nuspec'
    if (-not (Test-Path -LiteralPath $header -PathType Leaf) -or -not (Test-Path -LiteralPath $loader -PathType Leaf)) {
        throw 'Нужен локальный Microsoft.Web.WebView2 1.0.4191.47 SDK x64; сборка ничего не скачивает.'
    }
    if (-not (Test-Path -LiteralPath $nuspec -PathType Leaf)) { throw 'Нет NuGet metadata WebView2 SDK.' }
    [xml]$packageMetadata = Get-Content -LiteralPath $nuspec -Raw
    if ([string]$packageMetadata.package.metadata.version -ne '1.0.4191.47') { throw 'Нужен точный WebView2 SDK 1.0.4191.47.' }
    $relativeFiles = @(
        'tools/autocad-bridge/build-desktop-windows.ps1',
        'tools/autocad-bridge/windows-product-version.ps1',
        'tools/autocad-bridge/PackageContents.xml',
        'tools/autocad-bridge/desktop/Info.plist',
        'tools/autocad-bridge/desktop/windows-main.cpp',
        'tools/autocad-bridge/desktop/windows-platform.cpp',
        'tools/autocad-bridge/desktop/windows-platform.h',
        'tools/autocad-bridge/desktop/windows-policies.cpp',
        'tools/autocad-bridge/desktop/windows-policies.h',
        'tools/autocad-bridge/desktop/windows-app.manifest',
        'tools/autocad-bridge/desktop/contracts.cpp',
        'tools/autocad-bridge/desktop/contracts.h',
        'tools/autocad-bridge/desktop/test_windows_policies.cpp',
        'tools/autocad-bridge/desktop/test_windows_runtime.cpp',
        'tools/autocad-bridge/vendor/nlohmann/json.hpp',
        'tools/autocad-bridge/vendor/nlohmann/LICENSE.MIT'
    )
    $files = @($relativeFiles | ForEach-Object {
        $file = Get-Item -LiteralPath (Join-Path $root $_)
        if ($file.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Исходник содержит reparse point: $_" }
        [ordered]@{ path = $_; sha256 = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant(); bytes = $file.Length }
    })
    if (-not $OutputDirectory) { $OutputDirectory = Join-Path $root ('.runtime/windows-desktop-host/' + [guid]::NewGuid().ToString('N')) }
    $destination = [IO.Path]::GetFullPath($OutputDirectory)
    if (-not [IO.Path]::GetPathRoot($destination).Equals([IO.Path]::GetPathRoot($root), [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Временные файлы сборки должны оставаться на диске checkout.'
    }
    $plan = [ordered]@{
        schema = 'green-atlas.desktop-build/1'; version = $Version; target = 'windows-x86_64';
        sdk = '1.0.4191.47'; loader = 'static'; output = (Join-Path $destination 'GreenAtlasDesktop.exe');
        files = $files; windows_gui_execution = $false; qualification = 'not_claimed';
        signed = $false; signing_certificate_thumbprint = $SigningCertificateThumbprint
    }
    if ($ValidateOnly) { $plan | ConvertTo-Json -Depth 8; return }
    # Выбранное Developer PowerShell x64 окружение принадлежит только сборщику.
    $compiler = Get-Command cl.exe -ErrorAction Stop
    if ($env:VSCMD_ARG_TGT_ARCH -ne 'x64') { throw 'Откройте Developer PowerShell x64 для MSVC.' }
    if (Test-Path -LiteralPath $destination) {
        if (@(Get-ChildItem -LiteralPath $destination -Force).Count) { throw 'Каталог сборки должен быть новым или пустым.' }
    } else { New-Item -ItemType Directory -Path $destination -Force | Out-Null }
    $versionHeader = Join-Path $destination 'host-build-version.h'
    [IO.File]::WriteAllText($versionHeader, ('#define GREEN_ATLAS_DESKTOP_VERSION "{0}"' -f $Version) + "`n", [Text.UTF8Encoding]::new($false))
    $common = @('/nologo', '/std:c++17', '/EHsc', '/utf-8', '/MD', '/O2', '/W4', '/DUNICODE', '/D_UNICODE', '/DNOMINMAX', '/D_WIN32_WINNT=0x0A00', '/DWINVER=0x0A00', "/FI$versionHeader", "/I$(Join-Path $sdk 'build/native/include')")
    Push-Location $destination
    try {
        $policy = Join-Path $root 'tools/autocad-bridge/desktop/windows-policies.cpp'
        $contracts = Join-Path $root 'tools/autocad-bridge/desktop/contracts.cpp'
        $test = Join-Path $root 'tools/autocad-bridge/desktop/test_windows_policies.cpp'
        & $compiler.Source @common $policy $contracts $test '/link' '/SUBSYSTEM:CONSOLE' '/OUT:test-windows-policies.exe' 2>&1 |
            Tee-Object -FilePath 'policy-build.log' | ForEach-Object { Write-Host $_ }
        if ($LASTEXITCODE -ne 0) { throw 'Не удалось собрать portable policy tests.' }
        $selfTestText = & ./test-windows-policies.exe
        if ($LASTEXITCODE -ne 0 -or -not ($selfTestText | ConvertFrom-Json).passed) { throw 'Portable policy tests не прошли.' }
        $main = Join-Path $root 'tools/autocad-bridge/desktop/windows-main.cpp'
        $platform = Join-Path $root 'tools/autocad-bridge/desktop/windows-platform.cpp'
        $manifest = Join-Path $root 'tools/autocad-bridge/desktop/windows-app.manifest'
        $runtimeTest = Join-Path $root 'tools/autocad-bridge/desktop/test_windows_runtime.cpp'
        & $compiler.Source @common $runtimeTest $platform $policy $contracts '/link' '/SUBSYSTEM:CONSOLE' '/ENTRY:wmainCRTStartup' `
            'user32.lib' 'ole32.lib' 'shell32.lib' 'advapi32.lib' 'winhttp.lib' 'bcrypt.lib' 'uuid.lib' '/OUT:test-windows-runtime.exe' 2>&1 |
            Tee-Object -FilePath 'runtime-test-build.log' | ForEach-Object { Write-Host $_ }
        if ($LASTEXITCODE -ne 0) { throw 'Не удалось собрать Win32 synthetic runtime tests.' }
        $runtimeTestText = & ./test-windows-runtime.exe (Join-Path $destination 'синтетический профиль')
        if ($LASTEXITCODE -ne 0 -or -not ($runtimeTestText | ConvertFrom-Json).passed) { throw 'Win32 synthetic runtime tests не прошли.' }
        & $compiler.Source @common $main $platform $policy $contracts '/link' '/SUBSYSTEM:WINDOWS' '/ENTRY:wWinMainCRTStartup' `
            "/LIBPATH:$(Join-Path $sdk 'build/native/x64')" 'WebView2LoaderStatic.lib' 'user32.lib' 'ole32.lib' 'shell32.lib' `
            'advapi32.lib' 'winhttp.lib' 'comdlg32.lib' 'gdi32.lib' 'bcrypt.lib' 'version.lib' 'uuid.lib' '/MANIFEST:EMBED' "/MANIFESTINPUT:$manifest" `
            '/OUT:GreenAtlasDesktop.exe' 2>&1 | Tee-Object -FilePath 'host-build.log' | ForEach-Object { Write-Host $_ }
        if ($LASTEXITCODE -ne 0) { throw 'Не удалось собрать Windows WebView2 host; см. host-build.log.' }
    } finally { Pop-Location }
    $binary = Join-Path $destination 'GreenAtlasDesktop.exe'
    if ($SigningCertificateThumbprint) {
        $certificate = Get-Item -LiteralPath ("Cert:\CurrentUser\My\" + $SigningCertificateThumbprint)
        if (-not $certificate.HasPrivateKey) { throw 'У сертификата нет доступного private key.' }
        $signature = Set-AuthenticodeSignature -FilePath $binary -Certificate $certificate -HashAlgorithm SHA256
        if ($signature.Status -ne 'Valid') { throw "Подпись host не подтверждена: $($signature.Status)." }
        $plan['signed'] = $true
    }
    $plan['binary_sha256'] = (Get-FileHash -LiteralPath $binary -Algorithm SHA256).Hash.ToLowerInvariant()
    $plan['binary_bytes'] = (Get-Item -LiteralPath $binary).Length
    $plan['generated_version_header_sha256'] = (Get-FileHash -LiteralPath $versionHeader -Algorithm SHA256).Hash.ToLowerInvariant()
    $plan['external_inputs'] = @($header, $loader, $nuspec) | ForEach-Object {
        [ordered]@{ filename = [IO.Path]::GetFileName($_); sha256 = (Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash.ToLowerInvariant() }
    }
    $plan['portable_self_test'] = $selfTestText | ConvertFrom-Json
    $plan['synthetic_runtime_self_test'] = $runtimeTestText | ConvertFrom-Json
    [IO.File]::WriteAllText((Join-Path $destination 'build-receipt.json'), ($plan | ConvertTo-Json -Depth 8) + "`n", [Text.UTF8Encoding]::new($false))
    Write-Output $binary
} finally { Pop-Location }
