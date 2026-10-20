#requires -Version 7.0
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$SetupPath,
    [string]$SetupReceipt,
    [string]$BuildPython,
    [string]$HelperScript = 'scripts/desktop/windows_product_install_smoke.py',
    [string]$OutputDirectory = '.runtime/autocad-bridge/ci-product-install',
    [ValidateRange(300, 1200)][int]$TimeoutSeconds = 900
)

$ErrorActionPreference = 'Stop'
$repository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$createdUser = $null
$child = $null
$output = $null
$cleanupErrors = [Collections.Generic.List[string]]::new()
$passed = $false
$primaryError = $null

function Remove-CiExplicitGrant([string]$Path, [string]$Sid) {
    # Только явные allow ACE одноразового SID на двух ранее изменённых каталогах.
    # Тип SecurityIdentifier исключает перевод имени удалённой учётной записи.
    $acl = Get-Acl -LiteralPath $Path
    $rules = @($acl.GetAccessRules($true, $false, [Security.Principal.SecurityIdentifier]) | Where-Object {
        -not $_.IsInherited -and $_.IdentityReference.Value -eq $Sid -and $_.AccessControlType -eq [Security.AccessControl.AccessControlType]::Allow
    })
    foreach ($rule in $rules) { $acl.RemoveAccessRuleSpecific($rule) }
    if ($rules.Count) { Set-Acl -LiteralPath $Path -AclObject $acl }
    $remaining = @((Get-Acl -LiteralPath $Path).GetAccessRules($true, $false, [Security.Principal.SecurityIdentifier]) | Where-Object {
        -not $_.IsInherited -and $_.IdentityReference.Value -eq $Sid -and $_.AccessControlType -eq [Security.AccessControl.AccessControlType]::Allow
    })
    if ($remaining.Count) { throw ('Явный временный grant остался после удаления: ' + $Path) }
    [ordered]@{ path=$Path; removed_explicit_allow_rules=$rules.Count; verified_absent=$true }
}

Push-Location $repository
try {
    . ./scripts/windows-env.ps1
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) { throw 'Нужна настоящая Windows x64.' }
    if (-not ([Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Только изолированный административный CI harness создаёт временного обычного пользователя.'
    }
    if (-not $env:GITHUB_ACTIONS -or $env:GITHUB_ACTIONS -ne 'true') { throw 'Создание тестовой учётной записи разрешено только на одноразовом GitHub Actions runner.' }
    if (-not $BuildPython) { $BuildPython = Join-Path $repository 'apps/api/.venv/Scripts/python.exe' }
    $BuildPython = (Resolve-Path -LiteralPath $BuildPython).Path
    $SetupPath = (Resolve-Path -LiteralPath $SetupPath).Path
    $HelperScript = (Resolve-Path -LiteralPath $HelperScript).Path
    if (-not $HelperScript.StartsWith((Join-Path $repository 'scripts/desktop/'), [StringComparison]::OrdinalIgnoreCase)) { throw 'Неизвестный CI helper.' }
    if (-not $SetupReceipt) { $SetupReceipt = Join-Path (Split-Path (Split-Path $SetupPath -Parent) -Parent) 'setup-build-receipt.json' }
    if (-not (Test-Path -LiteralPath $SetupReceipt -PathType Leaf)) { throw 'Нужна production setup-build-receipt.json.' }
    $SetupReceipt = (Resolve-Path -LiteralPath $SetupReceipt).Path
    $output = if ([IO.Path]::IsPathRooted($OutputDirectory)) { [IO.Path]::GetFullPath($OutputDirectory) } else { [IO.Path]::GetFullPath((Join-Path $repository $OutputDirectory)) }
    $runtimeRoot = Join-Path $repository '.runtime'
    foreach ($path in @($BuildPython, $SetupPath, $SetupReceipt, $output)) {
        if (-not $path.StartsWith($repository + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Все входы и результаты CI должны находиться в checkout.' }
        $cursor = if (Test-Path -LiteralPath $path) { $path } else { Split-Path $path -Parent }
        while ($cursor -and $cursor.StartsWith($repository, [StringComparison]::OrdinalIgnoreCase)) {
            if ((Test-Path -LiteralPath $cursor) -and ((Get-Item -LiteralPath $cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'CI путь содержит reparse point.' }
            if ($cursor -eq $repository) { break }
            $cursor = Split-Path $cursor -Parent
        }
    }
    if (-not $output.StartsWith($runtimeRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Тестовая установка разрешена только внутри .runtime checkout.' }
    if (Test-Path -LiteralPath $output) { throw 'Нужен новый OutputDirectory без результатов предыдущей установки.' }
    New-Item -ItemType Directory -Path $output | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $output 'Temp') | Out-Null
    $accountName = 'ga-ci-' + [guid]::NewGuid().ToString('N').Substring(0, 12)
    # Пароль существует только в памяти процесса и не попадает в argv, файл или журнал.
    $password = ConvertTo-SecureString ('GA!a1' + [Convert]::ToBase64String([Security.Cryptography.RandomNumberGenerator]::GetBytes(48))) -AsPlainText -Force
    $createdUser = New-LocalUser -Name $accountName -Password $password -Description 'Временная обычная учётная запись Green Atlas CI' -AccountNeverExpires -PasswordNeverExpires
    if (-not (Get-LocalGroupMember -SID 'S-1-5-32-545' | Where-Object { $_.SID.Value -eq $createdUser.SID.Value })) {
        Add-LocalGroupMember -SID 'S-1-5-32-545' -Member $createdUser
    }
    if (Get-LocalGroupMember -SID 'S-1-5-32-544' | Where-Object { $_.SID.Value -eq $createdUser.SID.Value }) { throw 'Тестовая учётная запись неожиданно получила Administrator.' }
    $sid = $createdUser.SID.Value
    $sidBytes = [byte[]]::new($createdUser.SID.BinaryLength)
    $createdUser.SID.GetBinaryForm($sidBytes, 0)
    $sidHex = [Convert]::ToHexString($sidBytes).ToLowerInvariant()
    # Исходники и сборка доступны только на чтение; запись разрешена в новом каталоге smoke.
    & icacls.exe $repository /grant ('*' + $sid + ':(OI)(CI)(RX)') *> (Join-Path $output 'checkout-acl.log')
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось предоставить чтение checkout временному пользователю.' }
    & icacls.exe $output /grant ('*' + $sid + ':(OI)(CI)(M)') *> (Join-Path $output 'output-acl.log')
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось предоставить запись только в smoke output.' }

    function Quote-Ps([string]$Value) { return "'" + $Value.Replace("'", "''") + "'" }
    $launcher = Join-Path $output 'ordinary-user-launch.ps1'
    $childOutput = Join-Path $output 'user-checks'
    $launcherText = @"
`$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $(Quote-Ps $repository)
. ./scripts/windows-env.ps1
`$env:PYTHONDONTWRITEBYTECODE = '1'
`$env:PYTHONUTF8 = '1'
`$env:PYTHONPATH = $(Quote-Ps (Join-Path $repository 'apps/api'))
`$env:TEMP = $(Quote-Ps (Join-Path $output 'Temp'))
`$env:TMP = `$env:TEMP
`$env:TMPDIR = `$env:TEMP
& $(Quote-Ps $BuildPython) $(Quote-Ps $HelperScript) --setup $(Quote-Ps $SetupPath) --setup-receipt $(Quote-Ps $SetupReceipt) --output $(Quote-Ps $childOutput) --expected-sid-hex $(Quote-Ps $sidHex) *> $(Quote-Ps (Join-Path $output 'ordinary-user.log'))
exit `$LASTEXITCODE
"@
    [IO.File]::WriteAllText($launcher, $launcherText, [Text.UTF8Encoding]::new($false))
    $credential = [Management.Automation.PSCredential]::new(($env:COMPUTERNAME + '\' + $accountName), $password)
    # Загружается настоящий HKCU нового SID; дочерний helper сам отвергает повышенный token.
    $child = Start-Process -FilePath (Get-Command pwsh -ErrorAction Stop).Source -Credential $credential -LoadUserProfile -WorkingDirectory $repository -ArgumentList @('-NoLogo', '-NoProfile', '-NonInteractive', '-File', ('"' + $launcher + '"')) -PassThru
    if (-not $child.WaitForExit($TimeoutSeconds * 1000)) {
        $child.Kill($true)
        $child.WaitForExit(10000) | Out-Null
        throw 'Превышено общее время установки под обычным CI пользователем.'
    }
    if ($child.ExitCode -ne 0) {
        # Preserve bounded owned diagnostics in the job log even when GitHub
        # refuses artifact uploads. Never print the inherited environment.
        $failureReceipt = Join-Path $childOutput 'install-ci-receipt.json'
        if ((Test-Path -LiteralPath $failureReceipt -PathType Leaf) -and (Get-Item -LiteralPath $failureReceipt).Length -lt 1MB) {
            $failure = Get-Content -LiteralPath $failureReceipt -Raw | ConvertFrom-Json
            $failure | Select-Object schema, passed, error, steps, stages, setup_sha256, source_manifest_sha256 | ConvertTo-Json -Depth 6 | Write-Host
        } else {
            Get-Content -LiteralPath (Join-Path $output 'ordinary-user.log') -Tail 60 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host $_ }
        }
        throw ('Обычный пользователь не прошёл Setup smoke; код ' + $child.ExitCode + '; см. ordinary-user.log.')
    }
    $receipt = Get-Content -LiteralPath (Join-Path $childOutput 'install-ci-receipt.json') -Raw | ConvertFrom-Json
    if ($receipt.passed -ne $true -or $receipt.ordinary_user_token -ne $true -or $receipt.product_qualified -ne $false) { throw 'Нет достоверной ограниченной install smoke receipt.' }
    $passed = $true
} catch {
    $primaryError = $_
    throw
} finally {
    if ($child -and -not $child.HasExited) {
        try { $child.Kill($true); $child.WaitForExit(10000) | Out-Null } catch { $cleanupErrors.Add('Не удалось остановить собственный CI launcher.') }
    }
    if ($createdUser) {
        # Очистка одноразового SID не затрагивает процессы исходного runner и иных пользователей.
        try {
            foreach ($process in Get-CimInstance Win32_Process) {
                if ($process.ProcessId -eq $PID) { continue }
                $owner = Invoke-CimMethod -InputObject $process -MethodName GetOwnerSid -ErrorAction SilentlyContinue
                if ($owner -and $owner.Sid -eq $createdUser.SID.Value) { Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue }
            }
        } catch { $cleanupErrors.Add('Не удалось проверить процессы одноразового SID.') }
        foreach ($grantPath in @($output, $repository)) {
            try {
                Remove-CiExplicitGrant $grantPath $createdUser.SID.Value | ConvertTo-Json -Compress | Add-Content -LiteralPath (Join-Path $output 'cleanup-acl.log') -Encoding utf8
            } catch { $cleanupErrors.Add('Не удалось удалить точный временный grant: ' + $grantPath + '; ' + $_.Exception.Message) }
        }
        try {
            Get-CimInstance Win32_UserProfile -Filter ("SID='" + $createdUser.SID.Value + "'") | Remove-CimInstance
        } catch { $cleanupErrors.Add('Не удалось удалить системный профиль одноразового SID.') }
        try { Remove-LocalUser -SID $createdUser.SID } catch { $cleanupErrors.Add('Не удалось удалить одноразового пользователя.') }
    }
    if ($output -and (Test-Path -LiteralPath $output -PathType Container)) {
        try {
            $summary = [ordered]@{ schema='green-atlas.windows-product-install-harness/1'; passed=($passed -and $cleanupErrors.Count -eq 0); ordinary_account_created=($null -ne $createdUser); product_qualified=$false; primary_error=if ($primaryError) { $primaryError.Exception.Message } else { $null }; cleanup_errors=@($cleanupErrors); source_profile_preserved=$true }
            [IO.File]::WriteAllText((Join-Path $output 'harness-receipt.json'), ($summary | ConvertTo-Json -Depth 8) + "`n", [Text.UTF8Encoding]::new($false))
        } catch { $cleanupErrors.Add('Не записана квитанция harness: ' + $_.Exception.Message) }
    }
    Pop-Location
    if ($cleanupErrors.Count) {
        if ($primaryError) { Write-Warning ($cleanupErrors -join ' ') }
        else { throw ($cleanupErrors -join ' ') }
    }
}
Write-Output (Join-Path $output 'user-checks/install-ci-receipt.json')
