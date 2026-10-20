#requires -Version 7.0
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Backend,
    [Parameter(Mandatory)][string]$BackendReceipt,
    [Parameter(Mandatory)][string]$Compiler,
    [Parameter(Mandatory)][string]$OutputDirectory
)
$ErrorActionPreference = 'Stop'
$repository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
Push-Location $repository
try {
    . ./scripts/windows-env.ps1
    $Backend = (Resolve-Path -LiteralPath $Backend).Path
    $Compiler = (Resolve-Path -LiteralPath $Compiler).Path
    $OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
    if (-not $OutputDirectory.StartsWith($repository + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Вывод мастера должен оставаться в checkout.' }
    $receipt = Get-Content -LiteralPath $BackendReceipt -Raw | ConvertFrom-Json
    $version = & ./tools/autocad-bridge/windows-product-version.ps1
    if ($receipt.product_version -ne $version) { throw 'Backend собран для другой версии продукта.' }
    $sha = (Get-FileHash -LiteralPath $Backend -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($sha -ne $receipt.sha256 -or (Get-Item -LiteralPath $Backend).Length -ne $receipt.bytes) { throw 'Backend не совпадает со своей build receipt.' }
    New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
    $log = Join-Path $OutputDirectory 'wizard-compile.log'
    . ./scripts/windows-env.ps1
    & $Compiler ('/DBackend=' + $Backend) ('/DBackendSHA=' + $sha) ('/DOutput=' + $OutputDirectory) ('/DProductVersion=' + $version) tools/autocad-bridge/installer/windows/wizard.iss *> $log
    if ($LASTEXITCODE -ne 0) { Get-Content -LiteralPath $log -Tail 70 | ForEach-Object { Write-Host $_ }; throw 'Не собран native Windows мастер.' }
    $wizard = Join-Path $OutputDirectory 'GreenAtlasSetupWizard.exe'
    $result = [ordered]@{
        schema='green-atlas.windows-native-wizard-build/1'; kind='inno-setup'; installer_revision=2
        product_version=$version
        setup='GreenAtlasSetupWizard.exe'; sha256=(Get-FileHash -LiteralPath $wizard -Algorithm SHA256).Hash.ToLowerInvariant()
        bytes=(Get-Item -LiteralPath $wizard).Length; backend=$Backend; backend_sha256=$sha
        source_manifest_sha256=$receipt.source_manifest_sha256; distribution_manifest_sha256=$receipt.distribution_manifest_sha256
        source_git_commit=(& git rev-parse HEAD).Trim(); compiler_version=[Diagnostics.FileVersionInfo]::GetVersionInfo($Compiler).FileVersion
        native_gui_execution_qualified=$false; product_qualified=$false
    }
    $result | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path (Split-Path $OutputDirectory -Parent) 'wizard-build-receipt.json') -Encoding utf8
    Write-Output $wizard
} finally { Pop-Location }
