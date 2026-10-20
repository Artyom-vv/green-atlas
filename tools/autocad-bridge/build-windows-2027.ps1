#requires -Version 7.0
[CmdletBinding()]
param(
    [ValidateSet('Core', 'Gui')][string]$Target = 'Core',
    [string]$SdkRoot = $env:OBJECTARX_SDK_ROOT,
    [string]$VisualStudioRoot = $env:GREEN_ATLAS_VS_ROOT,
    [string]$OutputDirectory,
    [string]$SigningCertificateThumbprint,
    [switch]$ValidateOnly
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$utf8 = [Text.UTF8Encoding]::new($false)

function Write-JsonFile($Path, $Value) {
    $json = ($Value | ConvertTo-Json -Depth 16).Replace("`r`n", "`n") + "`n"
    [IO.File]::WriteAllText($Path, $json, $utf8)
}

function Resolve-OwnedPath([string]$Relative) {
    if ([IO.Path]::IsPathRooted($Relative) -or $Relative -match '(^|[/\\])\.\.([/\\]|$)') {
        throw "Путь выходит из checkout: $Relative"
    }
    $path = [IO.Path]::GetFullPath((Join-Path $repository $Relative))
    if (-not $path.StartsWith($repository + [IO.Path]::DirectorySeparatorChar,
            [StringComparison]::OrdinalIgnoreCase)) {
        throw "Путь выходит из checkout: $Relative"
    }
    return $path
}

function Assert-NoReparseAncestor([string]$Path) {
    $cursor = [IO.Path]::GetFullPath($Path)
    while ($cursor -and $cursor.StartsWith($repository, [StringComparison]::OrdinalIgnoreCase)) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Путь checkout содержит ссылку или reparse point: $cursor"
            }
        }
        if ($cursor -eq $repository) { break }
        $cursor = [IO.Path]::GetDirectoryName($cursor)
    }
}

function File-Identity([string]$Path, [string]$Relative) {
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.PSIsContainer) { throw "Ожидался файл: $Path" }
    return [ordered]@{
        path = $Relative.Replace('\', '/')
        size = $item.Length
        sha256 = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    }
}

function Initialize-Msvc {
    # Меняется только окружение текущего процесса; установки и системных правок нет.
    $compiler = Get-Command cl.exe -ErrorAction SilentlyContinue
    if ($null -eq $compiler -or -not $env:VCToolsVersion -or $VisualStudioRoot) {
        $installation = $VisualStudioRoot
        if (-not $installation) {
            $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio/Installer/vswhere.exe'
            if (-not (Test-Path -LiteralPath $vswhere -PathType Leaf)) {
                throw 'Не найдены MSVC C++ Build Tools. Укажите -VisualStudioRoot или Developer PowerShell x64.'
            }
            $installation = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
            if ($LASTEXITCODE -ne 0 -or -not $installation) { throw 'MSVC x64 не найден через vswhere.' }
            $installation = ([string]$installation).Trim()
        }
        $toolsRoot = Join-Path $installation 'VC/Tools/MSVC'
        $versions = @(Get-ChildItem -LiteralPath $toolsRoot -Directory | ForEach-Object {
            $parsed = $null
            if ([version]::TryParse($_.Name, [ref]$parsed) -and $parsed -ge $minimumTools) { $parsed }
        } | Sort-Object -Descending)
        if ($versions.Count -eq 0) { throw "Нужны MSVC tools $minimumTools или новее для SDK 2027." }
        $selectedVersion = $versions[0].ToString()
        $batch = Join-Path $installation 'VC/Auxiliary/Build/vcvarsall.bat'
        if (-not (Test-Path -LiteralPath $batch -PathType Leaf)) { throw "Не найден $batch" }
        if ($batch.Contains('"')) { throw 'Путь MSVC содержит недопустимую кавычку.' }
        $process = [Diagnostics.Process]::new()
        $process.StartInfo = [Diagnostics.ProcessStartInfo]::new()
        $process.StartInfo.FileName = $env:ComSpec
        $process.StartInfo.Arguments = '/u /d /s /c ' + ('""{0}" x64 -vcvars_ver={1} >nul && set"' -f $batch, $selectedVersion)
        $process.StartInfo.UseShellExecute = $false
        $process.StartInfo.RedirectStandardOutput = $true
        $process.StartInfo.RedirectStandardError = $true
        $process.StartInfo.StandardOutputEncoding = [Text.Encoding]::Unicode
        try {
            if (-not $process.Start()) { throw 'Не удалось запустить окружение MSVC.' }
            $errorsTask = $process.StandardError.ReadToEndAsync()
            $environmentText = $process.StandardOutput.ReadToEnd()
            $process.WaitForExit()
            $errorsText = $errorsTask.GetAwaiter().GetResult()
            if ($process.ExitCode -ne 0) { throw "Ошибка vcvarsall: $errorsText" }
        } finally { $process.Dispose() }
        foreach ($line in ($environmentText -split "`r?`n")) {
            if ($line -match '^(PATH|INCLUDE|LIB|LIBPATH|VCToolsInstallDir|VCToolsVersion|WindowsSdkDir|WindowsSDKVersion|VSCMD_ARG_TGT_ARCH)=(.*)$') {
                [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], 'Process')
            }
        }
    }
    if (-not $env:VCToolsVersion -or [version]$env:VCToolsVersion -lt $minimumTools) {
        throw "Нужны MSVC tools $minimumTools или новее; выбран $env:VCToolsVersion."
    }
    if ($env:VSCMD_ARG_TGT_ARCH -ne 'x64') { throw 'Требуется окружение MSVC с целевой архитектурой x64.' }
    if (-not $env:WindowsSdkDir -or -not $env:WindowsSDKVersion) { throw 'Windows SDK не найден в окружении MSVC.' }
    $compiler = Get-Command cl.exe -ErrorAction Stop
    # Не допускаем случайный cl.exe из другого каталога PATH.
    $expected = [IO.Path]::GetFullPath((Join-Path $env:VCToolsInstallDir 'bin/Hostx64/x64/cl.exe'))
    if (-not $compiler.Source.Equals($expected, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Выбран другой компилятор: $($compiler.Source). Ожидался $expected"
    }
    return $compiler.Source
}

function Response-Argument([string]$Value) {
    if ($Value -match '["\r\n]') { throw 'Аргумент сборки содержит недопустимую кавычку или перевод строки.' }
    return '"' + $Value + '"'
}

function Assert-Amd64Pe([string]$Path) {
    $stream = [IO.File]::OpenRead($Path)
    $reader = [IO.BinaryReader]::new($stream)
    try {
        if ($reader.ReadUInt16() -ne 0x5a4d -or $stream.Length -lt 64) { throw 'Сборка не создала PE файл.' }
        $stream.Position = 0x3c
        $header = $reader.ReadUInt32()
        if ($header -gt $stream.Length - 24) { throw 'Повреждён заголовок PE.' }
        $stream.Position = $header
        if ($reader.ReadUInt32() -ne 0x4550 -or $reader.ReadUInt16() -ne 0x8664) {
            throw 'Ожидался PE AMD64, совместимый с AutoCAD Windows x64.'
        }
    } finally { $reader.Dispose() }
}

Push-Location $repository
try {
    . ./scripts/windows-env.ps1
    $planPath = Join-Path $PSScriptRoot 'windows-2027/build-plan.json'
    $plan = Get-Content -LiteralPath $planPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($plan.schema -ne 'green-atlas.native-build-plan/1') { throw 'Неизвестный контракт плана сборки.' }
    $targetName = $Target.ToLowerInvariant()
    $component = $plan.targets.$targetName
    $minimumTools = [version]$plan.msvc.minimum_tools_version
    if ($component.version -notmatch '^\d+\.\d+\.\d+$') { throw 'Некорректная версия компонента.' }
    if (-not $OutputDirectory) { $OutputDirectory = ".runtime/autocad-bridge/windows-2027/$targetName" }
    if ([IO.Path]::IsPathRooted($OutputDirectory)) {
        $fullOutput = [IO.Path]::GetFullPath($OutputDirectory)
        if (-not $fullOutput.StartsWith($repository + [IO.Path]::DirectorySeparatorChar,
                [StringComparison]::OrdinalIgnoreCase)) { throw 'OutputDirectory должен находиться в checkout.' }
    } else { $fullOutput = Resolve-OwnedPath $OutputDirectory }
    Assert-NoReparseAncestor $fullOutput
    New-Item -ItemType Directory -Path $fullOutput -Force | Out-Null

    # Реестр не содержит абсолютных путей, времени сборки и зависимостей разработчика.
    $inventoryPaths = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    foreach ($path in $component.sources) { [void]$inventoryPaths.Add($path) }
    if ($targetName -eq 'gui') {
        foreach ($path in $component.macos_adapters) { [void]$inventoryPaths.Add($path) }
    }
    foreach ($directory in @('native', 'query-worker', 'vendor', 'windows')) {
        Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot $directory) -Recurse -File | Where-Object { $_.Extension -in @('.h', '.hpp') -or $_.Name -in @('LICENSE.MIT', 'README.md') } | ForEach-Object {
            $relative = [IO.Path]::GetRelativePath($repository, $_.FullName).Replace('\', '/')
            [void]$inventoryPaths.Add($relative)
        }
    }
    foreach ($path in @('tools/autocad-bridge/windows-2027/build-plan.json',
            'tools/autocad-bridge/build-windows-2027.ps1',
            'tools/autocad-bridge/build-macos.sh', 'tools/autocad-bridge/build-query-worker-macos.sh')) {
        [void]$inventoryPaths.Add($path)
    }
    $sortedPaths = [string[]]@($inventoryPaths)
    [Array]::Sort($sortedPaths, [StringComparer]::Ordinal)
    $inventoryFiles = @()
    $failures = [Collections.Generic.List[string]]::new()
    foreach ($relative in $sortedPaths) {
        $path = Resolve-OwnedPath $relative
        Assert-NoReparseAncestor $path
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            $failures.Add("Отсутствует исходник: $relative")
        } else { $inventoryFiles += File-Identity $path $relative }
    }
    $inventoryPath = Join-Path $fullOutput 'source-inventory.json'
    Write-JsonFile $inventoryPath ([ordered]@{
        schema = 'green-atlas.native-source-inventory/1'
        target = $targetName
        component_version = $component.version
        compile_units = @($component.sources)
        files = @($inventoryFiles)
    })
    Write-Host "Реестр исходников: $inventoryPath"

    if (-not $SdkRoot) { $SdkRoot = Join-Path $repository '.runtime/tools/objectarx-2027/CDROM1' }
    $SdkRoot = [IO.Path]::GetFullPath($SdkRoot)
    $sdkFiles = @($plan.sdk.required_headers)
    $sdkFiles += @($plan.sdk.common_libraries | ForEach-Object { "lib-x64/$_" })
    $sdkFiles += @($plan.sdk.brep_libraries | ForEach-Object { "utils/brep/lib-x64/$_" })
    $sdkFiles += @($component.sdk_additional_libraries | ForEach-Object { "lib-x64/$_" })
    foreach ($directory in @($plan.sdk.include_directories) + @($plan.sdk.library_directories)) {
        if (-not (Test-Path -LiteralPath (Join-Path $SdkRoot $directory) -PathType Container)) {
            $failures.Add("Нет каталога ObjectARX 2027 Windows SDK: $directory в $SdkRoot")
        }
    }
    foreach ($relative in $sdkFiles) {
        if (-not (Test-Path -LiteralPath (Join-Path $SdkRoot $relative) -PathType Leaf)) {
            $failures.Add("Нет файла ObjectARX 2027 Windows SDK: $relative в $SdkRoot")
        } elseif ((Get-Item -LiteralPath (Join-Path $SdkRoot $relative)).Length -eq 0) {
            $failures.Add("Пустой файл ObjectARX 2027 Windows SDK: $relative")
        }
    }
    if ($targetName -eq 'gui') {
        foreach ($blocker in $component.blockers) { $failures.Add("GUI: $($blocker.path): $($blocker.reason)") }
    }
    $compiler = $null
    if ($failures.Count -eq 0) {
        try {
            $compiler = Initialize-Msvc
            . ./scripts/windows-env.ps1
        } catch { $failures.Add($_.Exception.Message) }
    }
    $validationPath = Join-Path $fullOutput 'validation-receipt.json'
    Write-JsonFile $validationPath ([ordered]@{
        schema = 'green-atlas.native-build-validation/1'
        target = $targetName
        component_version = $component.version
        source_inventory_sha256 = (Get-FileHash -LiteralPath $inventoryPath -Algorithm SHA256).Hash.ToLowerInvariant()
        prerequisites_valid = ($failures.Count -eq 0)
        compiled = $false
        autocad_execution_qualified = $false
        errors = @($failures.ToArray())
    })
    if ($failures.Count -gt 0) { throw ("Сборка не готова:`n" + ($failures -join "`n")) }
    if ($ValidateOnly) { Write-Output $validationPath; return }
    if ($component.build_state -ne 'candidate') { throw 'Цель пока запрещена для сборки.' }

    $buildRoot = Join-Path $fullOutput ('build-' + [Guid]::NewGuid().ToString('N'))
    $packageRoot = Join-Path $buildRoot 'package'
    New-Item -ItemType Directory -Path $packageRoot -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $buildRoot 'objects') -Force | Out-Null
    Copy-Item -LiteralPath $inventoryPath -Destination (Join-Path $buildRoot 'source-inventory.json')
    $sourceInventorySha = (Get-FileHash -LiteralPath (Join-Path $buildRoot 'source-inventory.json') -Algorithm SHA256).Hash.ToLowerInvariant()
    $nativeRoot = Join-Path $PSScriptRoot 'native'
    $arguments = @('/nologo', '/std:c++20', '/permissive-', '/EHsc', '/utf-8', '/MD', '/LD', '/O2', '/W3',
        '/DUNICODE', '/D_UNICODE', '/DWIN32', '/DWIN', '/DNDEBUG', '/DNOMINMAX',
        '/D_WIN32_WINNT=0x0A00', '/DWINVER=0x0A00',
        '/D_CRT_STDIO_ISO_WIDE_SPECIFIERS=1', '/DDLLNAME_ACBR=acbr26.dbx', '/Foobjects/',
        "/FI$nativeRoot/windows_prefix.h", "/I$nativeRoot", "/I$PSScriptRoot/windows", "/I$PSScriptRoot/vendor")
    foreach ($relative in $plan.sdk.include_directories) { $arguments += '/I' + (Join-Path $SdkRoot $relative) }
    foreach ($relative in $component.sources) { $arguments += Resolve-OwnedPath $relative }
    $responseLines = @($arguments | ForEach-Object { Response-Argument $_ })
    # Версия Core не выводится из версии редакторского плагина.
    if ($targetName -eq 'core') {
        $responseLines += ('/DGA_QUERY_WORKER_VERSION=\"{0}\"' -f $component.version)
    }
    $binaryBase = [IO.Path]::GetFileNameWithoutExtension($component.binary)
    $linkArguments = @('/MACHINE:X64', ('/OUT:package/' + $component.binary),
        ('/IMPLIB:objects/' + $binaryBase + '.lib'), ('/PDB:objects/' + $binaryBase + '.pdb'),
        ('/DEF:' + (Join-Path $SdkRoot $plan.sdk.definition_file)))
    foreach ($relative in $plan.sdk.library_directories) { $linkArguments += '/LIBPATH:' + (Join-Path $SdkRoot $relative) }
    $linkArguments += @($plan.sdk.common_libraries) + @($plan.sdk.brep_libraries) + @($component.sdk_additional_libraries) + @($component.system_libraries)
    # CL применяет /link только к остатку той же строки response file.
    # Иначе /OUT и /LIBPATH ошибочно разбираются как параметры компилятора.
    $responseLines += '/link ' + (@($linkArguments | ForEach-Object { Response-Argument $_ }) -join ' ')
    $responsePath = Join-Path $buildRoot 'compile.rsp'
    # BOM позволяет CL и LINK прочитать пути checkout с кириллицей.
    [IO.File]::WriteAllText($responsePath, ($responseLines -join "`r`n") + "`r`n", [Text.UTF8Encoding]::new($true))
    Push-Location $buildRoot
    try {
        . (Join-Path $repository 'scripts/windows-env.ps1')
        $logPath = Join-Path $buildRoot 'compile.log'
        & $compiler ('@' + $responsePath) *> $logPath
        $compilerExit = $LASTEXITCODE
        Get-Content -LiteralPath $logPath | ForEach-Object { Write-Host $_ }
        if ($compilerExit -ne 0) { throw "MSVC завершился с ошибкой; файлы сохранены в $buildRoot" }
    } finally { Pop-Location }
    $binary = Join-Path $packageRoot $component.binary
    Assert-Amd64Pe $binary
    if ($SigningCertificateThumbprint) {
        $certificate = Get-Item -LiteralPath ('Cert:/CurrentUser/My/' + $SigningCertificateThumbprint)
        $signature = Set-AuthenticodeSignature -LiteralPath $binary -Certificate $certificate -HashAlgorithm SHA256
        if ($signature.Status -ne 'Valid') { throw 'Подпись native кандидата не подтверждена.' }
    }
    foreach ($identity in $inventoryFiles) {
        $current = File-Identity (Resolve-OwnedPath $identity.path) $identity.path
        if ($current.sha256 -ne $identity.sha256 -or $current.size -ne $identity.size) {
            throw "Исходник изменился во время сборки: $($identity.path). Кандидат не опубликован."
        }
    }
    $binaryIdentity = File-Identity $binary $component.binary
    if ($targetName -eq 'core') {
        $metadata = [ordered]@{
            schema = 'green-atlas.query-worker/1'; target = 'windows-x86_64'
            binary = $component.binary; plugin_version = $component.version
            sha256 = $binaryIdentity.sha256
            face_protocols = @($component.face_protocols)
            topology_shapes = @($component.topology_shapes)
        }
        Write-JsonFile (Join-Path $packageRoot 'worker.json') $metadata
    } else {
        $metadata = [ordered]@{
            schema = 'green-atlas.gui-producer/1'; target = 'windows-x86_64'
            binary = $component.binary; plugin_version = $component.version
            sha256 = $binaryIdentity.sha256
        }
        Write-JsonFile (Join-Path $packageRoot 'producer.json') $metadata
    }
    $sdkIdentities = @($sdkFiles | ForEach-Object { File-Identity (Join-Path $SdkRoot $_) $_ })
    Write-JsonFile (Join-Path $buildRoot 'build-receipt.json') ([ordered]@{
        schema = 'green-atlas.native-build-receipt/1'
        state = 'built-candidate'
        autocad_release = '2027'
        target = $targetName
        component_version = $component.version
        source_inventory_sha256 = $sourceInventorySha
        compiler = $compiler
        msvc_tools_version = $env:VCToolsVersion
        windows_sdk_version = $env:WindowsSDKVersion
        objectarx_files = $sdkIdentities
        binary = $binaryIdentity
        autocad_execution_qualified = $false
    })
    Write-Output $binary
} finally { Pop-Location }
