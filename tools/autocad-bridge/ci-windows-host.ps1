#requires -Version 7.0
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$WebView2SdkRoot,
    [Parameter(Mandatory)][ValidatePattern('^[0-9A-Fa-f]{64}$')][string]$WebView2PackageSha256,
    [string]$OutputDirectory = '.runtime/autocad-bridge/ci-host/build'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$transcribing = $false
$binary = $null
Push-Location $repository
try {
    . ./scripts/windows-env.ps1
    $sdk = (Resolve-Path -LiteralPath $WebView2SdkRoot).Path
    $destination = [IO.Path]::GetFullPath($OutputDirectory)
    $logs = Join-Path $repository '.runtime/autocad-bridge/ci-host/logs'
    New-Item -ItemType Directory -Path $logs -Force | Out-Null
    $journal = Join-Path $logs ('host-' + [guid]::NewGuid().ToString('N') + '.log')
    Start-Transcript -LiteralPath $journal -NoClobber | Out-Null
    $transcribing = $true
    Write-Host "Журнал CI host: $journal"
    # Workflow проверяет SHA скачанного nupkg до распаковки; здесь нет загрузки.
    Write-Host ('WebView2 package SHA256 из проверенного workflow: ' + $WebView2PackageSha256.ToLowerInvariant())
    $nuspec = Join-Path $sdk 'Microsoft.Web.WebView2.nuspec'
    [xml]$metadata = Get-Content -LiteralPath $nuspec -Raw
    $id = $metadata.SelectSingleNode("/*[local-name()='package']/*[local-name()='metadata']/*[local-name()='id']")
    $version = $metadata.SelectSingleNode("/*[local-name()='package']/*[local-name()='metadata']/*[local-name()='version']")
    if ($null -eq $id -or $id.InnerText -ne 'Microsoft.Web.WebView2' -or $null -eq $version -or $version.InnerText -ne '1.0.4191.47') {
        throw 'Распакован не тот Microsoft.Web.WebView2 SDK; требуется 1.0.4191.47.'
    }
    foreach ($relative in @('build/native/include/WebView2.h', 'build/native/x64/WebView2LoaderStatic.lib')) {
        if (-not (Test-Path -LiteralPath (Join-Path $sdk $relative) -PathType Leaf)) { throw "SDK не содержит $relative" }
    }
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio/Installer/vswhere.exe'
    if (-not (Test-Path -LiteralPath $vswhere -PathType Leaf)) { throw 'Runner не содержит vswhere/MSVC; установка из wrapper не выполняется.' }
    . ./scripts/windows-env.ps1
    $installation = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    if ($LASTEXITCODE -ne 0 -or -not $installation) { throw 'Не найдены установленные MSVC x64 build tools.' }
    $launcher = Join-Path ([string]$installation).Trim() 'Common7/Tools/Launch-VsDevShell.ps1'
    if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) { throw 'Не найден Launch-VsDevShell.ps1.' }
    . ./scripts/windows-env.ps1
    & $launcher -Arch amd64 -HostArch amd64 -SkipAutomaticLocation | ForEach-Object { Write-Host $_ }
    if ($env:VSCMD_ARG_TGT_ARCH -ne 'x64') { throw 'Developer shell не включил MSVC target x64.' }
    $compiler = Get-Command cl.exe -ErrorAction Stop
    Write-Host ('MSVC: ' + $compiler.Source)
    Write-Host ('MSVC file version: ' + (Get-Item -LiteralPath $compiler.Source).VersionInfo.FileVersion)
    . ./scripts/windows-env.ps1
    $result = @(& ./tools/autocad-bridge/build-desktop-windows.ps1 -WebView2SdkRoot $sdk -OutputDirectory $destination)
    if ($result.Count -ne 1 -or -not (Test-Path -LiteralPath ([string]$result[0]) -PathType Leaf)) {
        throw 'Host builder не вернул единственный путь собранного EXE.'
    }
    $binary = [string]$result[0]
    Write-Host 'MSVC host compile/link, portable policies и synthetic Win32 runtime tests завершены.'
    $profile = Join-Path $env:LOCALAPPDATA 'GreenAtlas'
    $cliReceipt = [ordered]@{ schema='green-atlas.host-cli-smoke/1'; passed=$false; profile_untouched=$false; checks=@(); error=$null }
    try {
        if (Test-Path -LiteralPath $profile) { throw 'Тест CLI требует отсутствующий GreenAtlas профиль runner.' }
        foreach ($switch in @('--self-test', '--build-info', '--status-json')) {
            $check = [ordered]@{ switch=$switch; exit_code=$null; json=$null; passed=$false }
            $cliReceipt.checks += $check
            . ./scripts/windows-env.ps1
            $start = [Diagnostics.ProcessStartInfo]::new()
            $start.FileName = $binary; $start.WorkingDirectory = Split-Path $binary -Parent
            $start.UseShellExecute = $false; $start.CreateNoWindow = $true
            $start.RedirectStandardOutput = $true; $start.RedirectStandardError = $true
            $start.StandardOutputEncoding = [Text.UTF8Encoding]::new($false, $true)
            $start.StandardErrorEncoding = [Text.UTF8Encoding]::new($false, $true)
            $start.ArgumentList.Add($switch)
            $process = [Diagnostics.Process]::new(); $process.StartInfo = $start; $started = $false
            try {
                $started = $process.Start()
                if (-not $started) { throw 'Host CLI не запущен.' }
                $stdout = $process.StandardOutput.ReadToEndAsync(); $stderr = $process.StandardError.ReadToEndAsync()
                if (-not $process.WaitForExit(30000)) {
                    $process.Kill($true)
                    if (-not $process.WaitForExit(5000)) { throw 'Дерево Host CLI не остановилось.' }
                    throw 'Host CLI превысил 30 секунд.'
                }
                $check.exit_code = $process.ExitCode
                if (-not $stdout.Wait(5000) -or -not $stderr.Wait(5000)) { throw 'Host CLI не закрыл UTF8 pipes.' }
                $text = $stdout.GetAwaiter().GetResult(); $errors = $stderr.GetAwaiter().GetResult()
                if ($text.Length -gt 32768 -or $errors.Length -ne 0 -or $text -match '(?i)"(?:origin|session_?token)"\s*:') {
                    throw 'Host CLI вернул лишний или приватный вывод.'
                }
                $value = ConvertFrom-Json -InputObject $text -AsHashtable
                if ($value -isnot [Collections.IDictionary]) { throw 'Host CLI не вернул JSON объект.' }
                $check.json = $value
                $exitCode = $process.ExitCode
                if ($switch -eq '--self-test') {
                    if ($exitCode -ne 0 -or $value.schema -ne 'green-atlas.desktop-self-test/1' -or $value.passed -isnot [bool] -or $value.passed -ne $true -or $value.real_windows -isnot [bool] -or $value.real_windows -ne $true -or $value.webview_launched -isnot [bool] -or $value.webview_launched -ne $false) { throw 'Host self-test не пройден.' }
                } else {
                    $schema = if ($switch -eq '--build-info') { 'green-atlas.desktop-build/1' } else { 'green-atlas.desktop-status/1' }
                    # EXE ещё не установлен в payload: status-json возвращает 2, build-info 0.
                    $expectedExit = if ($switch -eq '--status-json') { 2 } else { 0 }
                    if ($exitCode -ne $expectedExit -or $value.schema -ne $schema -or $value.qualification -ne 'not_claimed' -or $value.layout_verified -isnot [bool] -or $value.layout_verified -ne $false) { throw 'Host introspection не соответствует build-only layout.' }
                }
                if (Test-Path -LiteralPath $profile) { throw 'Read-only CLI создал GreenAtlas профиль.' }
                $check.passed = $true
            } finally {
                try {
                    if ($started -and -not $process.HasExited) { $process.Kill($true); $null = $process.WaitForExit(5000) }
                } finally { $process.Dispose() }
            }
        }
        $cliReceipt.profile_untouched = -not (Test-Path -LiteralPath $profile)
        $cliReceipt.passed = $true
    } catch {
        # Сырой вывод/исключение JSON parser могут содержать приватные сведения.
        $cliReceipt.error = 'Host read-only CLI checkpoint не пройден.'
        $cliReceipt.profile_untouched = -not (Test-Path -LiteralPath $profile)
        throw $cliReceipt.error
    } finally {
        $receiptPath = Join-Path $logs 'host-cli-receipt.json'
        [IO.File]::WriteAllText($receiptPath, ($cliReceipt | ConvertTo-Json -Depth 8) + "`n", [Text.UTF8Encoding]::new($false))
    }
    Write-Host 'Host native entry/UTF8 CLI smoke пройден; профиль не создан.'
} finally {
    if ($transcribing) { Stop-Transcript | Out-Null }
    Pop-Location
}
Write-Output $binary
