#requires -Version 7.0
[CmdletBinding()]
param(
    [string]$BuildPython,
    [string]$OutputDirectory = '.runtime/autocad-bridge/ci-runtime',
    [switch]$BuildFrozenApi,
    [switch]$OnlyOptimizedStorage,
    [ValidateRange(0, 4)][int]$SourceShard = 0,
    [ValidateRange(-1, 23)][int]$SourceCheckIndex = -1,
    [switch]$TraceSourceTests
)

$ErrorActionPreference = 'Stop'
$repository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
Push-Location $repository
$previousPythonPath = [Environment]::GetEnvironmentVariable('PYTHONPATH', 'Process')
$previousPyi = [Environment]::GetEnvironmentVariable('PYINSTALLER_CONFIG_DIR', 'Process')
$previousUtf8 = [Environment]::GetEnvironmentVariable('PYTHONUTF8', 'Process')
try {
    . ./scripts/windows-env.ps1
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) { throw 'Нужна настоящая Windows x64.' }
    if ($OnlyOptimizedStorage -and $BuildFrozenApi) { throw 'Source-only проверка не включает сборку frozen API.' }
    if ($SourceShard -and -not $OnlyOptimizedStorage) { throw 'Разделение допустимо только для source-проверки.' }
    if ($SourceCheckIndex -ge 0 -and (-not $OnlyOptimizedStorage -or $SourceShard)) { throw 'Отдельный модуль допустим только вне общей source-группы.' }
    if ($TraceSourceTests -and (-not $OnlyOptimizedStorage -or $SourceCheckIndex -lt 0)) { throw 'Диагностические отметки допустимы только для отдельного source-модуля.' }
    if (-not $BuildPython) { $BuildPython = (Get-Command python -ErrorAction Stop).Source }
    $BuildPython = (Resolve-Path -LiteralPath $BuildPython).Path
    $output = if ([IO.Path]::IsPathRooted($OutputDirectory)) { [IO.Path]::GetFullPath($OutputDirectory) } else { [IO.Path]::GetFullPath((Join-Path $repository $OutputDirectory)) }
    if (-not $output.StartsWith($repository + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Вывод CI должен оставаться в checkout.' }
    if (Test-Path -LiteralPath $output) { throw 'Для CI нужен новый OutputDirectory, без старых результатов.' }
    $cursor = [IO.Path]::GetDirectoryName($output)
    while ($cursor -and $cursor.StartsWith($repository, [StringComparison]::OrdinalIgnoreCase)) {
        if ((Test-Path -LiteralPath $cursor) -and ((Get-Item -LiteralPath $cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Каталог CI содержит reparse point.' }
        if ($cursor -eq $repository) { break }
        $cursor = [IO.Path]::GetDirectoryName($cursor)
    }
    New-Item -ItemType Directory -Path $output | Out-Null
    $env:PYTHONUTF8 = '1'
    $env:PYTHONPATH = Join-Path $repository 'apps/api'
    $env:PYINSTALLER_CONFIG_DIR = Join-Path $output 'pyinstaller-cache'

    function Invoke-BoundaryPython([string]$Name, [string[]]$Arguments, [hashtable]$Environment = @{}, [switch]$Bootstrap) {
        . ./scripts/windows-env.ps1
        $log = Join-Path $output ($Name + '.log')
        Write-Host ('Windows CI phase: ' + $Name)
        $previousCommandEnvironment = @{}
        try {
            foreach ($environmentName in $Environment.Keys) {
                $previousCommandEnvironment[$environmentName] = [Environment]::GetEnvironmentVariable($environmentName, 'Process')
                [Environment]::SetEnvironmentVariable($environmentName, $Environment[$environmentName], 'Process')
            }
            if ($OnlyOptimizedStorage) {
                # Независимый source-контроль использует штатный запуск runner.
                # Его вывод и предел времени принадлежат отдельному шагу Actions.
                & $BuildPython -u @Arguments
                $result = $LASTEXITCODE
            } elseif ($Bootstrap) {
                # Проверка владельца процессов не запускается через него самого:
                # ошибки запуска и вложенных Job Objects должны выйти напрямую.
                $diagnosticDeadline = if ($OnlyOptimizedStorage -and -not $Bootstrap) { 300 } else { 120 }
                $phaseStart = [Diagnostics.ProcessStartInfo]::new($BuildPython)
                $phaseStart.UseShellExecute = $false
                $phaseStart.CreateNoWindow = $true
                # У headless Windows runner нет пригодных наследуемых stdout/stderr.
                # Даём Python настоящие handles до переключения на его файл журнала.
                $phaseStart.RedirectStandardOutput = $true
                $phaseStart.RedirectStandardError = $true
                $phaseStart.StandardOutputEncoding = [Text.UTF8Encoding]::new($false)
                $phaseStart.StandardErrorEncoding = [Text.UTF8Encoding]::new($false)
                $phaseStart.WorkingDirectory = $repository
                foreach ($argument in @('-u', (Join-Path $repository 'scripts/desktop/python_phase_control.py'), '--deadline', [string]$diagnosticDeadline, '--log', $log, '--') + $Arguments) {
                    $phaseStart.ArgumentList.Add($argument)
                }
                $phase = [Diagnostics.Process]::Start($phaseStart)
                try {
                    $startupOutput = $phase.StandardOutput.ReadToEndAsync()
                    $startupError = $phase.StandardError.ReadToEndAsync()
                    if (-not $phase.WaitForExit(($diagnosticDeadline + 15) * 1000)) {
                        $phase.Kill($true)
                        if (-not $phase.WaitForExit(10000)) { throw 'Не остановилась собственная Python-фаза CI.' }
                        $result = 124
                    } else {
                        $result = $phase.ExitCode
                    }
                    foreach ($stream in @($startupOutput, $startupError)) {
                        if (-not $stream.Wait(5000)) { throw 'Не закрылся стартовый канал собственной Python-фазы CI.' }
                        if ($stream.Result) { Write-Host $stream.Result }
                    }
                } finally {
                    if (-not $phase.HasExited) { $phase.Kill($true); [void]$phase.WaitForExit(10000) }
                    $phase.Dispose()
                }
                Get-Content -LiteralPath $log -Tail 200 | ForEach-Object { Write-Host $_ }
            } else {
                & $BuildPython -u scripts/desktop/windows_qa_command.py --log $log --timeout 1200 -- @Arguments
                $result = $LASTEXITCODE
            }
            if ($result -ne 0) {
                if ($OnlyOptimizedStorage) { throw "Windows CI: $Name завершился с кодом $result; вывод сохранён в журнале шага Actions." }
                throw "Windows CI: $Name завершился с кодом $result; журнал $log"
            }
        } finally {
            foreach ($environmentName in $previousCommandEnvironment.Keys) {
                [Environment]::SetEnvironmentVariable($environmentName, $previousCommandEnvironment[$environmentName], 'Process')
            }
        }
    }

    Invoke-BoundaryPython 'qualified-python' @('apps/api/scripts/check_runtime.py') -Bootstrap
    $base = @('-m', 'pytest', '-o', 'addopts=', '-q', '-p', 'no:cacheprovider', '--timeout=45')
    if ($TraceSourceTests) { $base += @('-s', '-p', 'scripts.desktop.windows_test_progress', '-o', 'faulthandler_timeout=25') }
    if (-not $OnlyOptimizedStorage) {
        Invoke-BoundaryPython 'build-tools' @('-c', 'import sys,struct,PyInstaller; assert sys.platform=="win32" and struct.calcsize("P")==8 and PyInstaller.__version__=="6.22.0"; print("Windows x64 PyInstaller 6.22.0")') -Bootstrap
        Invoke-BoundaryPython 'command-ownership-tests' ($base + @(
            '--basetemp', (Join-Path $output 'pytest-command'), '--junitxml', (Join-Path $output 'command-ownership-tests.xml'),
            'scripts/desktop/test_python_phase_control.py', 'scripts/desktop/test_windows_ci_evidence.py',
            'scripts/desktop/test_windows_qa_command.py'
        )) -Bootstrap
        Invoke-BoundaryPython 'product-version-tests' ($base + @(
            '--basetemp', (Join-Path $output 'pytest-version'), '--junitxml', (Join-Path $output 'product-version-tests.xml'),
            'scripts/desktop/test_windows_product_version.py'
        ))
    }
    # Общий checkpoint должен работать с реальными Windows handles и SQLite,
    # а не только проходить сборку frozen API.
    $maxFailures = if ($OnlyOptimizedStorage) { 1 } else { 10 }
    $optimizedChecks = @(
        'apps/api/tests/test_display_geometry_storage.py',
        'apps/api/tests/test_persistent_viewport.py',
        'apps/api/tests/test_prepared_storage.py',
        'apps/api/tests/test_prepared_geometry.py',
        'apps/api/tests/test_prepared_registry.py',
        'apps/api/tests/test_prepared_stream.py',
        'apps/api/tests/test_binary_prepared_engine.py',
        'apps/api/tests/test_constraint_union_reuse.py',
        'apps/api/tests/test_search_geometry_scope.py',
        'apps/api/tests/test_search_spacing_precheck.py',
        'apps/api/tests/test_geometry_metadata_cache.py',
        'apps/api/tests/test_history_projection.py',
        'apps/api/tests/test_native_point_check_projection.py',
        'apps/api/tests/test_pattern_project_projection.py',
        'apps/api/tests/test_plan_geometry_retention.py',
        'apps/api/tests/test_planting_catalog_projection.py',
        'apps/api/tests/test_source_role_projection.py',
        'apps/api/tests/test_stable_overview_paths.py',
        'apps/api/tests/test_viewport_metadata_selection.py',
        'apps/api/tests/test_project_repository.py',
        'apps/api/tests/test_source_storage.py',
        'apps/api/tests/test_viewport_streaming.py',
        'apps/api/tests/test_viewport_request_cancellation.py',
        'apps/api/tests/test_desktop_project_lifecycle.py::test_owned_prepared_project_planting_history_and_restart[source]',
        'apps/api/tests/test_occupied_centers.py'
    )
    if ($SourceCheckIndex -ge 0) {
        $optimizedChecks = @($optimizedChecks[$SourceCheckIndex])
    } elseif ($SourceShard) {
        $optimizedChecks = @(for ($index = 0; $index -lt $optimizedChecks.Count; $index++) {
            if ($index % 4 -eq $SourceShard - 1) { $optimizedChecks[$index] }
        })
    }
    Write-Host ('Source check group ' + $SourceShard + ': ' + ($optimizedChecks -join ', '))
    Invoke-BoundaryPython 'optimized-storage-tests' ($base + @(
        '--tb=short', ('--maxfail=' + $maxFailures), '--durations=15',
        '--basetemp', (Join-Path $output 'pytest-optimized'), '--junitxml', (Join-Path $output 'optimized-storage-tests.xml')
    ) + $optimizedChecks)
    if ($OnlyOptimizedStorage) {
        [xml]$junit = Get-Content -LiteralPath (Join-Path $output 'optimized-storage-tests.xml') -Raw
        $cases = @($junit.SelectNodes('//testcase'))
        if (-not $cases.Count -or @($cases | Where-Object { $_.SelectSingleNode('failure|error|skipped') }).Count) {
            throw 'Не все обязательные source storage/geometry проверки прошли.'
        }
        . ./scripts/windows-env.ps1
        $sourceCommit = & git rev-parse HEAD
        if ($LASTEXITCODE -ne 0 -or ($env:GITHUB_SHA -and $sourceCommit -ne $env:GITHUB_SHA)) {
            throw 'Исходный commit не совпадает с закреплённым CI checkout.'
        }
        $receipt = [ordered]@{
            schema = 'green-atlas.windows-optimized-source-ci/2'
            passed = $true; source_commit = $sourceCommit
            source_cases_passed = $cases.Count
            source_shard = $SourceShard; source_shard_count = $(if ($SourceShard) { 4 } else { 1 })
            source_check_index = $SourceCheckIndex
            test_progress_notices = [bool]$TraceSourceTests
            source_full_set_executed = ($SourceShard -eq 0 -and $SourceCheckIndex -lt 0)
            selected_checks = $optimizedChecks
            driver = 'direct_powershell'; command_wrapper_tested = $false
            github_step_deadline_seconds = 480
            frozen_api_executed = $false; installer_executed = $false
            autocad_executed = $false; product_qualified = $false
        }
        $receiptJson = $receipt | ConvertTo-Json -Depth 4
        [IO.File]::WriteAllText((Join-Path $output 'optimized-source-ci-receipt.json'), $receiptJson + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
        Write-Host $receiptJson
        return
    }
    Invoke-BoundaryPython 'boundary-tests' ($base + @(
        '--basetemp', (Join-Path $output 'pytest-boundary'), '--junitxml', (Join-Path $output 'boundary-tests.xml'),
        'apps/api/tests/test_windows_setup_guard.py',
        'apps/api/tests/test_windows_process.py',
        'apps/api/tests/test_windows_shell_transaction.py',
        'apps/api/tests/test_windows_distribution_prerequisites.py',
        'apps/api/tests/test_windows_install_recovery.py',
        'apps/api/tests/test_windows_prerequisite_elevation.py',
        'apps/api/tests/test_windows_ticket_files.py',
        'apps/api/tests/test_child_dll_search.py::test_real_windows_external_program_and_restore'
    ))
    Invoke-BoundaryPython 'native-files-tests' ($base + @(
        '--basetemp', (Join-Path $output 'pytest-native-files'), '--junitxml', (Join-Path $output 'native-files-tests.xml'),
        'apps/api/tests/test_windows_job_files.py', '-k', 'real_windows'
    ))
    Invoke-BoundaryPython 'setup-files-tests' ($base + @(
        '--basetemp', (Join-Path $output 'pytest-setup-files'), '--junitxml', (Join-Path $output 'setup-files-tests.xml'),
        'apps/api/tests/test_windows_setup_payload.py', '-k', 'not bundle_copy_refuses and not preserves_dangling'
    ))
    Invoke-BoundaryPython 'external-process-tests' ($base + @(
        '--basetemp', (Join-Path $output 'pytest-external-processes'), '--junitxml', (Join-Path $output 'external-process-tests.xml'),
        'apps/api/tests/test_external_launch_consumers.py',
        'apps/api/tests/test_render_resource_settings.py',
        'apps/api/tests/test_blender_runtime.py',
        'apps/api/tests/test_render_preview_assets.py',
        'apps/api/tests/test_scene_rendering.py'
    ))

    # Это проверка заморозки и безопасного CLI, а не комплект установки продукта.
    $placeholder = Join-Path $output 'compile-only-payload'
    New-Item -ItemType Directory -Path $placeholder | Out-Null
    [IO.File]::WriteAllText((Join-Path $placeholder 'COMPILE-ONLY.txt'), 'Синтетический payload. Установка и публикация запрещены.', [Text.UTF8Encoding]::new($false))
    Invoke-BoundaryPython 'setup-compile' @(
        '-m', 'PyInstaller', '--noconfirm', '--onefile', '--windowed', '--name', 'GreenAtlasSetupCompileSmoke',
        '--distpath', (Join-Path $output 'dist'), '--workpath', (Join-Path $output 'work'), '--specpath', (Join-Path $output 'spec'),
        '--paths', (Join-Path $repository 'apps/api'), '--collect-submodules', 'app.distribution',
        '--add-data', ($placeholder + ';payload/GreenAtlas.bundle'),
        '--manifest', (Join-Path $PSScriptRoot 'installer/windows/setup.manifest'),
        (Join-Path $PSScriptRoot 'installer/windows/setup_entry.py')
    )
    $setup = Join-Path $output 'dist/GreenAtlasSetupCompileSmoke.exe'
    if (-not (Test-Path -LiteralPath $setup -PathType Leaf)) { throw 'PyInstaller не создал compile-only Setup.' }
    $probe = @'
import ctypes, hashlib, json, os, pathlib, subprocess, sys
from unittest.mock import patch
from app.distribution.windows_shell import create_shortcut, installation_mutex
setup, root = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
# Только частный ярлык в checkout, без Start Menu/HKCU и запуска его цели.
shortcut = root / 'private-shortcut.lnk'
create_shortcut(shortcut, setup, arguments='--status-json --quiet')
if not shortcut.is_file() or shortcut.stat().st_size == 0:
    raise RuntimeError('COM не сохранил частный ярлык')
# Admin policy отдельно проверяется оригинальным EXE; здесь проверяется только kernel ABI.
with patch('app.distribution.windows_shell.require_windows_user', lambda: None):
    with installation_mutex():
        try:
            with installation_mutex():
                raise AssertionError('Второй setup не был отклонён')
        except ValueError:
            pass
    with installation_mutex():
        pass
profile = root / 'synthetic-profile'
if profile.exists():
    raise RuntimeError('Для smoke нужен отсутствующий профиль')
env = dict(os.environ)
env['APPDATA'] = str(profile / 'Roaming')
env['LOCALAPPDATA'] = str(profile / 'Local')
result = subprocess.run([str(setup), '--status-json', '--quiet'], env=env, capture_output=True, timeout=90, check=False)
admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
expected = {'ok': False, 'error': 'Запустите установщик от обычного пользователя без повышения прав'} if admin else {'installed': False, 'package_sha256': None}
channel, empty = (result.stderr, result.stdout) if admin else (result.stdout, result.stderr)
if result.returncode != (1 if admin else 0) or empty or json.loads(channel.decode('utf-8')) != expected:
    raise RuntimeError('Frozen windowed Setup не вернул ожидаемый приватный status JSON')
if profile.exists():
    raise RuntimeError('Readonly Setup status создал пользовательский профиль')
with setup.open('rb') as stream:
    setup_sha256 = hashlib.file_digest(stream, 'sha256').hexdigest()
receipt = {'schema': 'green-atlas.windows-runtime-ci/1', 'python': sys.version, 'setup_compiled': True,
           'setup_status_executed': True, 'setup_status_mode': 'admin_refused' if admin else 'fresh_uninstalled',
           'setup_sha256': setup_sha256, 'private_com_shortcut_saved': True,
           'global_sid_mutex_api_executed': True, 'mutex_admin_policy_bypassed_only_in_isolated_api_test': True,
           'setup_bytes': setup.stat().st_size, 'profile_written': False, 'installer_executed': False,
           'product_qualified': False, 'autocad_executed': False, 'webview2_executed': False,
           'payload': 'Синтетический payload для компиляции. Распространение EXE запрещено.'}
(root / 'runtime-ci-receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps(receipt, ensure_ascii=False))
'@
    Invoke-BoundaryPython 'setup-status' @('-c', $probe, $setup, $output)
    if ($BuildFrozenApi) {
        $frozenOutput = Join-Path $output 'frozen-api'
        New-Item -ItemType Directory -Path $frozenOutput | Out-Null
        $frozenReceiptPath = Join-Path $frozenOutput 'frozen-api-ci-receipt.json'
        $frozenReceipt = [ordered]@{
            schema = 'green-atlas.windows-frozen-api-ci/1'
            passed = $false; source_verified = $false; api_built = $false
            smoke_executed = $false; smoke_passed = $false
            lifecycle_executed = $false; lifecycle_passed = $false
            product_qualified = $false; installer_executed = $false
            autocad_executed = $false; blender_executed = $false; webview2_executed = $false
            synthetic_profile_only = $true; payload_uploaded = $false; error = $null
        }
        try {
            $sealProbe = @'
import ast, hashlib, json, os, pathlib, stat, subprocess, sys
root, output = pathlib.Path(sys.argv[1]).resolve(), pathlib.Path(sys.argv[2])
def git(*arguments):
    return subprocess.run(['git', *arguments], cwd=root, capture_output=True, check=True).stdout
head = git('rev-parse', 'HEAD').decode('ascii').strip()
expected = os.environ.get('GITHUB_SHA') or head
if head != expected or git('status', '--porcelain=v1', '--untracked-files=no'):
    raise ValueError('Для frozen API нужен неизменённый закреплённый Git checkout')
algorithm = git('rev-parse', '--show-object-format').decode('ascii').strip()
if algorithm not in {'sha1', 'sha256'}:
    raise ValueError('Неизвестный формат Git objects')
rows = []
for record in git('ls-tree', '-r', '-z', '--full-tree', head).split(b'\0'):
    if not record:
        continue
    description, encoded_path = record.split(b'\t', 1)
    mode, kind, object_id = description.decode('ascii').split()
    relative = encoded_path.decode('utf-8')
    parts = pathlib.PurePosixPath(relative).parts
    if mode not in {'100644', '100755'} or kind != 'blob':
        raise ValueError('Source seal допускает только обычные tracked файлы: ' + relative)
    forbidden = {'.git', '.runtime', '.venv', 'node_modules', '__pycache__', 'output'}
    if not parts or any(part in forbidden for part in parts) or parts[0].lower() in {'sdk', 'objectarx'}:
        raise ValueError('Developer/SDK данные не входят в source seal: ' + relative)
    path = root.joinpath(*parts)
    if path.suffix.lower() in {'.db', '.db3', '.sqlite', '.sqlite3'}:
        raise ValueError('Рабочие базы не входят в source seal: ' + relative)
    if path.suffix.lower() in {'.dwg', '.dxf'} and parts[0] != 'fixtures':
        raise ValueError('CAD вне версионированных fixtures не входит в source seal: ' + relative)
    for current in (path, *path.parents):
        if current == root:
            break
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Source seal не допускает ссылку/reparse: ' + relative)
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError('Source seal ожидает обычный файл: ' + relative)
    size = path.stat().st_size
    blob = hashlib.new(algorithm)
    blob.update(b'blob ' + str(size).encode('ascii') + b'\0')
    digest, observed = hashlib.sha256(), 0
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            observed += len(block)
            blob.update(block)
            digest.update(block)
    if observed != size or blob.hexdigest() != object_id:
        raise ValueError('Checkout bytes не совпадают с закреплённым Git blob: ' + relative)
    rows.append({'path': relative, 'sha256': digest.hexdigest(), 'bytes': size})
rows.sort(key=lambda row: row['path'])
paths = {row['path'] for row in rows}
tree = ast.parse((root / 'apps/api/app/distribution/preflight.py').read_text(encoding='utf-8'))
resources = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == 'RUNTIME_RESOURCES' for target in node.targets))
required = {'tools/autocad-bridge/desktop/entry.py', 'apps/api/app/__init__.py'} | {'apps/api/app/' + value for value in resources}
if not required <= paths:
    raise ValueError('Git checkout не содержит обязательные runtime исходники')
manifest = output / 'source-manifest.json'
with manifest.open('x', encoding='utf-8', newline='\n') as stream:
    json.dump({'files': rows}, stream, ensure_ascii=False, indent=2)
    stream.write('\n')
receipt = {'schema': 'green-atlas.frozen-api-git-source/1', 'git_commit': head,
           'git_tree': git('rev-parse', head + '^{tree}').decode('ascii').strip(),
           'git_object_format': algorithm, 'git_blob_bytes_match': True,
           'source_manifest_sha256': hashlib.sha256(manifest.read_bytes()).hexdigest(),
           'tracked_regular_files': len(rows), 'app_python_files': sum(path.startswith('apps/api/app/') and path.endswith('.py') for path in paths),
           'runtime_raw_resources': len(resources), 'product_qualified': False}
with (output / 'git-source-receipt.json').open('x', encoding='utf-8') as stream:
    json.dump(receipt, stream, ensure_ascii=False, indent=2)
    stream.write('\n')
print(json.dumps(receipt, ensure_ascii=False))
'@
            Invoke-BoundaryPython 'frozen-api-source' @('-c', $sealProbe, $repository, $frozenOutput)
            $sourceManifest = Join-Path $frozenOutput 'source-manifest.json'
            $frozenReceipt.source_verified = $true
            $frozenReceipt.git_source = Get-Content -LiteralPath (Join-Path $frozenOutput 'git-source-receipt.json') -Raw | ConvertFrom-Json -AsHashtable
            . ./scripts/windows-env.ps1
            $builtRuntime = @(& ./tools/autocad-bridge/build-desktop-runtime-windows.ps1 -BuildPython $BuildPython -SourceManifest $sourceManifest)
            if ($builtRuntime.Count -ne 1 -or -not (Test-Path -LiteralPath ([string]$builtRuntime[0]) -PathType Leaf)) {
                throw 'Frozen API builder не вернул единственный путь собранного EXE.'
            }
            $runtime = [string]$builtRuntime[0]
            $frozenReceipt.api_built = $true
            Copy-Item -LiteralPath (Join-Path (Split-Path $runtime -Parent) 'frozen-api-provenance.json') -Destination (Join-Path $frozenOutput 'frozen-api-provenance.json')
            # Системные профили и внешние положительные CAD-контроли не наследуются smoke.
            $frozenSmokeProfile = Join-Path $frozenOutput 'synthetic-profile'
            $smokeEnvironment = @{
                GREEN_ATLAS_FROZEN_RUNTIME = $runtime
                GREEN_ATLAS_NATIVE_TICKET = $null; GREEN_ATLAS_PREPARED_DB = $null
                GREEN_ATLAS_PREPARED_PROJECT_ID = $null; GREEN_ATLAS_FROZEN_PRESETS = $null
                APPDATA = Join-Path $frozenSmokeProfile 'Roaming'; LOCALAPPDATA = Join-Path $frozenSmokeProfile 'Local'
                TEMP = Join-Path $frozenSmokeProfile 'Temp'; TMP = Join-Path $frozenSmokeProfile 'Temp'; TMPDIR = Join-Path $frozenSmokeProfile 'Temp'
                GREEN_ATLAS_LAYER_MODEL_PROVIDER = 'names'
            }
            New-Item -ItemType Directory -Path $smokeEnvironment.TEMP -Force | Out-Null
            $frozenReceipt.smoke_executed = $true
            Invoke-BoundaryPython 'frozen-api-smoke' @(
                '-m', 'pytest', '-c', 'apps/api/pyproject.toml', '-o', 'addopts=', '-q', '-p', 'no:cacheprovider',
                '--timeout=120', '--assert=plain', '--basetemp', (Join-Path $output 'pytest-frozen-api'),
                '--junitxml', (Join-Path $output 'frozen-api-smoke.xml'),
                'tools/autocad-bridge/desktop/test_frozen_runtime.py', '-k', 'not saves_automatic_planting'
            ) $smokeEnvironment
            $frozenReceipt.smoke_passed = $true
            $frozenReceipt.lifecycle_executed = $true
            Invoke-BoundaryPython 'frozen-api-project-lifecycle' @(
                '-m', 'pytest', '-c', 'apps/api/pyproject.toml', '-o', 'addopts=', '-q', '-p', 'no:cacheprovider',
                '--timeout=180', '--tb=short', '--assert=plain', '--basetemp', (Join-Path $output 'pytest-frozen-lifecycle'),
                '--junitxml', (Join-Path $output 'frozen-api-project-lifecycle.xml'),
                'apps/api/tests/test_desktop_project_lifecycle.py::test_owned_prepared_project_planting_history_and_restart[frozen]'
            ) $smokeEnvironment
            $frozenReceipt.lifecycle_passed = $true
            Invoke-BoundaryPython 'frozen-api-source-recheck' @('scripts/desktop/package_preflight.py', 'check-source', '--source-root', $repository, '--source-manifest', $sourceManifest)
            $buildReceiptProbe = @'
import hashlib, json, pathlib, stat, sys, xml.etree.ElementTree as ET
binary, output, junit = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3])
def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()
source = json.loads((output / 'git-source-receipt.json').read_text(encoding='utf-8'))
proof = json.loads((output / 'frozen-api-provenance.json').read_text(encoding='utf-8'))
if proof.get('compiled_source_matches') is not True or proof.get('binary_sha256') != sha(binary) or proof.get('source_manifest_sha256') != source['source_manifest_sha256']:
    raise ValueError('Frozen API provenance не соответствует построенному кандидату')
cases = ET.parse(junit).findall('.//testcase')
if len(cases) != source['runtime_raw_resources'] + 2 or any(any(case.find(tag) is not None for tag in ('failure', 'error', 'skipped')) for case in cases):
    raise ValueError('Не все обязательные frozen API проверки завершились успешно')
inventory = []
for path in sorted(binary.parent.rglob('*')):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise ValueError('Frozen API inventory не допускает ссылок/reparse')
    if path.is_file():
        inventory.append({'path': path.relative_to(binary.parent).as_posix(), 'bytes': info.st_size, 'sha256': sha(path)})
receipt = {'schema': 'green-atlas.frozen-api-ci-build/1', 'git_commit': source['git_commit'],
           'source_manifest_sha256': source['source_manifest_sha256'], 'binary_sha256': sha(binary),
           'binary_bytes': binary.stat().st_size, 'compiled_source_matches': True,
           'entry_verified': proof['entry']['source_path'], 'app_modules_verified': len(proof['modules']),
           'runtime_raw_resources_verified': source['runtime_raw_resources'], 'smoke_cases_passed': len(cases),
           'checks': ['relocated_clean_path', 'private_api_403', 'authenticated_health', 'empty_projects', 'validation_422', 'shutdown_202_exit_0', 'unknown_worker_rejected'],
           'payload_inventory': inventory, 'product_qualified': False, 'windows_product_acceptance': False,
           'autocad_executed': False, 'blender_executed': False, 'webview2_executed': False,
           'web': 'Синтетический index.html; compiled Web здесь не проверяется', 'payload_uploaded': False}
with (output / 'frozen-api-build-receipt.json').open('x', encoding='utf-8') as stream:
    json.dump(receipt, stream, ensure_ascii=False, indent=2)
    stream.write('\n')
print(json.dumps({key: value for key, value in receipt.items() if key != 'payload_inventory'}, ensure_ascii=False))
'@
            Invoke-BoundaryPython 'frozen-api-build-receipt' @('-c', $buildReceiptProbe, $runtime, $frozenOutput, (Join-Path $output 'frozen-api-smoke.xml'))
            $frozenReceipt.build = Get-Content -LiteralPath (Join-Path $frozenOutput 'frozen-api-build-receipt.json') -Raw | ConvertFrom-Json -AsHashtable
            $null = $frozenReceipt.build.Remove('payload_inventory')
            $frozenReceipt.passed = $true
        } catch {
            $frozenReceipt.error = $_.Exception.Message
            throw
        } finally {
            [IO.File]::WriteAllText($frozenReceiptPath, ($frozenReceipt | ConvertTo-Json -Depth 12) + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
        }
        Write-Host "Настоящий frozen API собран и проверен как CI-кандидат. Отчёт: $frozenReceiptPath"
    }
    Write-Host "Windows boundary и compile-only Setup проверены. Отчёт: $output"
} finally {
    [Environment]::SetEnvironmentVariable('PYTHONPATH', $previousPythonPath, 'Process')
    [Environment]::SetEnvironmentVariable('PYINSTALLER_CONFIG_DIR', $previousPyi, 'Process')
    [Environment]::SetEnvironmentVariable('PYTHONUTF8', $previousUtf8, 'Process')
    Pop-Location
}
