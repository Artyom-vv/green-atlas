#requires -Version 7.0
[CmdletBinding()]
param([string]$SourceRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..')))
$ErrorActionPreference = 'Stop'
[xml]$package = Get-Content -LiteralPath (Join-Path $SourceRoot 'tools/autocad-bridge/PackageContents.xml') -Raw
$version = [string]$package.ApplicationPackage.AppVersion
if ($version -notmatch '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$' -or
    @($version.Split('.') | Where-Object { $_.Length -gt 5 -or [int]$_ -gt 65535 }).Count) {
    throw 'Версия продукта должна содержать три числа от 0 до 65535.'
}
[xml]$desktop = Get-Content -LiteralPath (Join-Path $SourceRoot 'tools/autocad-bridge/desktop/Info.plist') -Raw
$nodes = @($desktop.SelectNodes("/plist/dict/key[text()='CFBundleShortVersionString']/following-sibling::*[1]"))
if ($nodes.Count -ne 1 -or $nodes[0].Name -ne 'string' -or $nodes[0].InnerText -ne $version) {
    throw 'Версии продукта и desktop расходятся; Windows-комплект не собирается.'
}
Write-Output $version
