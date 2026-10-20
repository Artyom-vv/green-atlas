#requires -Version 7.0
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$PackageRoot,
    [Parameter(Mandatory=$true)][string]$Output,
    [string]$Python,
    [switch]$RunFrozenSmoke
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
Push-Location $repository
try {
    . ./scripts/windows-env.ps1
    if (-not $Python) { $Python = Join-Path $repository 'apps/api/.venv/Scripts/python.exe' }
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        throw 'Нужен Python разработчика из checkout. Конечному пользователю он не требуется.'
    }
    $arguments = @('scripts/desktop/windows_qualification.py', '--package-root', $PackageRoot,
        '--output', $Output)
    if ($RunFrozenSmoke) { $arguments += '--run-frozen-smoke' }
    & $Python @arguments
    if ($LASTEXITCODE -ne 0) { throw 'Проверки Windows candidate не пройдены. Смотрите адресную квитанцию.' }
} finally { Pop-Location }
