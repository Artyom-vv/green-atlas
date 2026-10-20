#requires -Version 7.0
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$SdkRoot,
    [Parameter(Mandatory)][ValidatePattern('^[0-9A-Fa-f]{64}$')][string]$SdkPackageSha256,
    [string]$OutputDirectory = '.runtime/autocad-bridge/ci-native'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$utf8 = [Text.UTF8Encoding]::new($false)
$transcribing = $false
$failed = $false

function Assert-PlainAncestors([string]$Path) {
    $cursor = [IO.Path]::GetFullPath($Path)
    while ($cursor) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Путь содержит ссылку/reparse point: $cursor"
            }
        }
        $parent = [IO.Path]::GetDirectoryName($cursor)
        if ($parent -eq $cursor) { break }
        $cursor = $parent
    }
}

function Copy-OwnedBuildEvidence([string]$Work, [string]$Target) {
    if (-not (Test-Path -LiteralPath $Work -PathType Container)) { return }
    # В artifacts нет SDK, import libraries, объектов или отладочных symbols.
    $names = @('compile.log', 'build-receipt.json', 'source-inventory.json',
        'validation-receipt.json', 'GreenAtlasQuery.crx', 'worker.json',
        'GreenAtlasBridge.arx', 'producer.json')
    foreach ($file in Get-ChildItem -LiteralPath $Work -Recurse -File -Force) {
        if ($file.Name -notin $names) { continue }
        Assert-PlainAncestors $file.FullName
        $relative = [IO.Path]::GetRelativePath($Work, $file.FullName)
        $destination = Join-Path $Target $relative
        New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($destination)) -Force | Out-Null
        Copy-Item -LiteralPath $file.FullName -Destination $destination
    }
}

Push-Location $repository
try {
    . ./scripts/windows-env.ps1
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) { throw 'Нужна настоящая Windows x64.' }
    $output = if ([IO.Path]::IsPathRooted($OutputDirectory)) {
        [IO.Path]::GetFullPath($OutputDirectory)
    } else { [IO.Path]::GetFullPath((Join-Path $repository $OutputDirectory)) }
    if (-not $output.StartsWith($repository + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Вывод CI должен находиться внутри checkout.'
    }
    Assert-PlainAncestors $output
    if (Test-Path -LiteralPath $output) { throw 'Для native CI нужен новый OutputDirectory.' }
    New-Item -ItemType Directory -Path $output | Out-Null
    $artifact = Join-Path $output 'artifact'
    New-Item -ItemType Directory -Path $artifact | Out-Null
    Start-Transcript -LiteralPath (Join-Path $artifact 'native-ci.log') -NoClobber | Out-Null
    $transcribing = $true
    $receipt = [ordered]@{
        schema = 'green-atlas.native-ci/1'; purpose = 'compile-and-link-only'
        source_commit = $env:GITHUB_SHA
        sdk_package_sha256 = $SdkPackageSha256.ToLowerInvariant()
        sdk_package_sha256_verification = 'caller_asserted'
        sdk_extraction_verified_by = 'not_verified_by_wrapper'
        compiler = $null; compiler_version = $null; targets = @()
        passed = $false; module_identity_test = $false; autocad_executed = $false; product_qualified = $false; error = $null
    }
    try {
        Assert-PlainAncestors $SdkRoot
        $sdk = (Resolve-Path -LiteralPath $SdkRoot).Path
        $plan = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'windows-2027/build-plan.json') -Raw | ConvertFrom-Json
        if ($plan.schema -ne 'green-atlas.native-build-plan/1') { throw 'Неизвестный native build plan.' }
        foreach ($directory in @($plan.sdk.include_directories) + @($plan.sdk.library_directories)) {
            if (-not (Test-Path -LiteralPath (Join-Path $sdk $directory) -PathType Container)) { throw "SDK не содержит $directory" }
        }
        $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio/Installer/vswhere.exe'
        if (-not (Test-Path -LiteralPath $vswhere -PathType Leaf)) { throw 'Нет vswhere/MSVC. Wrapper ничего не устанавливает.' }
        . ./scripts/windows-env.ps1
        $installation = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
        if ($LASTEXITCODE -ne 0 -or -not $installation) { throw 'Не найдены MSVC x64 build tools.' }
        $launcher = Join-Path ([string]$installation).Trim() 'Common7/Tools/Launch-VsDevShell.ps1'
        if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) { throw 'Нет Launch-VsDevShell.ps1.' }
        . ./scripts/windows-env.ps1
        & $launcher -Arch amd64 -HostArch amd64 -SkipAutomaticLocation | ForEach-Object { Write-Host $_ }
        if ($env:VSCMD_ARG_TGT_ARCH -ne 'x64' -or -not $env:VCToolsVersion -or [version]$env:VCToolsVersion -lt [version]$plan.msvc.minimum_tools_version) {
            throw "Нужны MSVC x64 tools не ниже $($plan.msvc.minimum_tools_version)."
        }
        $compiler = (Get-Command cl.exe -ErrorAction Stop).Source
        $dumpbin = Join-Path ([IO.Path]::GetDirectoryName($compiler)) 'dumpbin.exe'
        if (-not (Test-Path -LiteralPath $dumpbin -PathType Leaf)) { throw 'Нет dumpbin из выбранного MSVC.' }
        $receipt.compiler = $compiler
        $receipt.compiler_version = (Get-Item -LiteralPath $compiler).VersionInfo.FileVersion
        $identityWork = Join-Path $output 'work/module-identity'
        New-Item -ItemType Directory -Path $identityWork | Out-Null
        Push-Location $identityWork
        try {
            $identitySources = @(
                (Join-Path $repository 'tools/autocad-bridge/windows/test_installed_module.cpp')
                (Join-Path $repository 'tools/autocad-bridge/windows/installed_module.cpp')
                (Join-Path $repository 'tools/autocad-bridge/native/file_io.cpp')
                (Join-Path $repository 'tools/autocad-bridge/native/local_platform.cpp')
            )
            & $compiler /nologo /std:c++20 /EHsc /MD $identitySources /Fe:test_installed_module.exe /link bcrypt.lib advapi32.lib shell32.lib ole32.lib
            if ($LASTEXITCODE -ne 0) { throw 'MSVC не собрал тест идентичности ARX.' }
            & ./test_installed_module.exe (Join-Path $identityWork 'fixture')
            if ($LASTEXITCODE -ne 0) { throw 'Тест идентичности ARX завершился с ошибкой.' }
            $receipt.module_identity_test = $true
        } finally { Pop-Location }
        foreach ($target in @('Core', 'Gui')) {
            $name = $target.ToLowerInvariant()
            $work = Join-Path $output ('work/' + $name)
            $targetArtifacts = Join-Path $artifact $name
            New-Item -ItemType Directory -Path $targetArtifacts | Out-Null
            $result = [ordered]@{ target = $name; passed = $false; binary = $null; sha256 = $null; exports_verified = $false; error = $null }
            $receipt.targets += $result
            try {
                . ./scripts/windows-env.ps1
                $paths = @(& ./tools/autocad-bridge/build-windows-2027.ps1 -Target $target -SdkRoot $sdk -OutputDirectory $work)
                if ($paths.Count -ne 1 -or -not (Test-Path -LiteralPath ([string]$paths[0]) -PathType Leaf)) {
                    throw 'Native builder не вернул единственный путь кандидата.'
                }
                $binary = [IO.Path]::GetFullPath([string]$paths[0])
                if (-not $binary.StartsWith($work + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Native binary находится вне собственного каталога сборки.' }
                Assert-PlainAncestors $binary
                . ./scripts/windows-env.ps1
                $pe = & $dumpbin /nologo /headers /exports /dependents $binary 2>&1
                $peExit = $LASTEXITCODE
                $pe | Set-Content -LiteralPath (Join-Path $targetArtifacts 'dumpbin.log') -Encoding utf8
                $text = $pe -join "`n"
                if ($peExit -ne 0) { throw 'dumpbin не прочитал PE кандидата.' }
                foreach ($entry in @('acrxEntryPoint', 'acrxGetApiVersion')) {
                    if ($text -notmatch ('(?m)^\s+\d+\s+[0-9A-Fa-f]+\s+[0-9A-Fa-f]+\s+' + $entry + '(?:\s|$)')) { throw "PE не экспортирует $entry" }
                }
                $result.binary = [IO.Path]::GetRelativePath($work, $binary).Replace('\', '/')
                $result.sha256 = (Get-FileHash -LiteralPath $binary -Algorithm SHA256).Hash.ToLowerInvariant()
                $result.exports_verified = $true
                $result.passed = $true
            } catch {
                $failed = $true
                $result.error = $_.Exception.Message
                Write-Host "Native $target отказ: $($result.error)"
            } finally {
                try { Copy-OwnedBuildEvidence $work $targetArtifacts }
                catch {
                    $failed = $true
                    $result.passed = $false
                    $result.error = "Не удалось сохранить собственные результаты: $($_.Exception.Message)"
                    Write-Host "Native ${target}: $($result.error)"
                }
            }
        }
        $receipt.passed = -not $failed
    } catch {
        $receipt.error = $_.Exception.Message
        throw
    } finally {
        [IO.File]::WriteAllText((Join-Path $artifact 'native-ci-receipt.json'), ($receipt | ConvertTo-Json -Depth 12) + "`n", $utf8)
    }
} finally {
    if ($transcribing) { Stop-Transcript | Out-Null }
    Pop-Location
}
if ($failed) { throw 'Одна или обе native цели не собраны; см. отдельные журналы/квитанции Core и Gui.' }
Write-Output $artifact
