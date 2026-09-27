[CmdletBinding()]
param(
    [string]$WorkDirectory,
    [switch]$WithoutPatch,
    [switch]$IncludeIndexedAcis
)
$ErrorActionPreference = 'Stop'
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
. (Join-Path $repo 'scripts/windows-env.ps1')
if (-not $WorkDirectory) {
    $WorkDirectory = Join-Path $repo '.runtime/libredwg-material-build'
}
$WorkDirectory = [IO.Path]::GetFullPath($WorkDirectory)
if ([IO.Path]::GetPathRoot($WorkDirectory) -ne [IO.Path]::GetPathRoot($repo)) {
    throw 'Build directory must be on the checkout drive.'
}
if (Test-Path -LiteralPath $WorkDirectory) {
    throw 'Choose a fresh work directory; existing builds are preserved.'
}
$revision = 'd9468ae948b8f07a08efa756c19f8916052358c0'
$source = Join-Path $WorkDirectory 'source'
$build = Join-Path $WorkDirectory 'build'
$patch = Join-Path $repo 'patches/libredwg/0.14-material-ascii-export.patch'
if ($WithoutPatch -and $IncludeIndexedAcis) { throw 'Choose baseline or patched ACDS build.' }
$patchFiles = @($patch)
if ($IncludeIndexedAcis) {
    $patchFiles += Join-Path $repo 'patches/libredwg/0.14-indexed-acds-ascii.patch'
    $patchFiles += Join-Path $repo 'patches/libredwg/0.14-utf8-text-chunks.patch'
    $patchFiles += Join-Path $repo 'patches/libredwg/0.14-win-getopt-state.patch'
}
New-Item -ItemType Directory -Path $WorkDirectory | Out-Null
git clone --depth 1 --branch 0.14 https://github.com/LibreDWG/libredwg.git $source
if ($LASTEXITCODE) { throw 'Clone failed' }
$actual = (git -C $source rev-parse HEAD).Trim()
if ($actual -ne $revision) { throw "Unexpected upstream revision: $actual" }
git -C $source submodule update --init --depth 1
if ($LASTEXITCODE) { throw 'Submodule checkout failed' }
if (-not $WithoutPatch) {
    foreach ($patchFile in $patchFiles) {
        git -C $source apply --check $patchFile
        if ($LASTEXITCODE) { throw "Patch check failed: $patchFile" }
        git -C $source apply $patchFile
        if ($LASTEXITCODE) { throw "Patch application failed: $patchFile" }
    }
    if ($IncludeIndexedAcis) {
        Copy-Item -LiteralPath (Join-Path $repo 'patches/libredwg/THIRD_PARTY_NOTICES.md') -Destination (Join-Path $source 'COPYING.ezdxf')
    }
}
# Running configure in the source avoids upstream CMake reading our repo version.
$env:PATH = 'C:/Program Files/Git/usr/bin;' + $env:PATH
Push-Location $source
try {
    cmake -S . -B $build -G 'Visual Studio 17 2022' -A x64 -DDISABLE_WERROR=ON -DENABLE_LTO=OFF -DBUILD_SHARED_LIBS=OFF
    if ($LASTEXITCODE) { throw 'Configure failed' }
    cmake --build $build --config Release --target dwg2dxf dwgread --parallel 2
    if ($LASTEXITCODE) { throw 'Build failed' }
} finally { Pop-Location }
@{
    revision = $revision
    patch_applied = -not $WithoutPatch
    indexed_acis_patch = [bool]$IncludeIndexedAcis
    patches = @($patchFiles | ForEach-Object { @{ path = $_; sha256 = (Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash.ToLowerInvariant() } })
    patch_sha256 = (Get-FileHash -LiteralPath $patch -Algorithm SHA256).Hash.ToLowerInvariant()
    binary_sha256 = (Get-FileHash -LiteralPath (Join-Path $build 'Release/dwg2dxf.exe') -Algorithm SHA256).Hash.ToLowerInvariant()
    runtime_installed = $false
    limitations = if ($IncludeIndexedAcis) { 'Inline indexed SAB only; paged blob01 rejected. Windows lab verified, Linux and full assembly pending. No iconv.' } else { 'MSVC upstream getopt loops on options; lab positional-input use only. No iconv. Validate before runtime use.' }
} | ConvertTo-Json | Set-Content (Join-Path $WorkDirectory 'build-manifest.json') -Encoding utf8
