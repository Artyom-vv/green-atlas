[CmdletBinding()]
param(
    [string]$SourceRoot = (Join-Path $PSScriptRoot '../..')
)

# Только разбор AST: сборка, установка и содержимое проверяемых файлов не запускаются.
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath($SourceRoot)
$paths = @(
    (Join-Path $root 'scripts/windows-env.ps1')
    Get-ChildItem -LiteralPath (Join-Path $root 'tools/autocad-bridge') -Filter '*.ps1' -File -Recurse |
        Select-Object -ExpandProperty FullName
)
$results = @()
$failed = $false
foreach ($path in ($paths | Sort-Object -Unique)) {
    $tokens = $null
    $errors = $null
    $null = [System.Management.Automation.Language.Parser]::ParseFile($path, [ref]$tokens, [ref]$errors)
    $issues = @($errors | ForEach-Object {
        [ordered]@{
            line = $_.Extent.StartLineNumber
            column = $_.Extent.StartColumnNumber
            id = $_.ErrorId
            message = $_.Message
        }
    })
    if ($issues.Count -gt 0) { $failed = $true }
    $results += [ordered]@{ file = $path.Substring($root.Length + 1); errors = $issues }
}
[ordered]@{
    schema = 'green-atlas.powershell-syntax/1'
    powershell = $PSVersionTable.PSVersion.ToString()
    scripts = $results
    passed = (-not $failed)
    execution_performed = $false
} | ConvertTo-Json -Depth 8
if ($failed) { exit 1 }
