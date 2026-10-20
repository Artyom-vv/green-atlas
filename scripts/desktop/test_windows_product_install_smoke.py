"""Переносимые проверки CI приёмки не подменяют настоящую установку Windows."""

import io
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.desktop import windows_product_install_smoke as smoke


def test_short_profile_is_new_unicode_sibling_inside_same_writable_grant(tmp_path):
    grant = tmp_path / "owned-install-smoke"
    output = grant / "user-checks"
    output.mkdir(parents=True)
    sentinel = output / "existing-diagnostic.txt"
    sentinel.write_bytes(b"preserve")
    profile = smoke.create_synthetic_profile(output)
    assert profile.parent == grant and profile.name == "П"
    assert not profile.is_relative_to(output)
    assert {path.name for path in profile.iterdir()} == {"Local", "Roaming", "Temp"}
    environment = smoke.clean_environment(profile, {"SystemRoot": str(tmp_path / "Windows")})
    assert Path(environment["APPDATA"]) == profile / "Roaming"
    assert Path(environment["LOCALAPPDATA"]) == profile / "Local"
    with pytest.raises(FileExistsError):
        smoke.create_synthetic_profile(output)
    assert sentinel.read_bytes() == b"preserve"


@pytest.mark.parametrize("characters,expected", [(259, True), (260, False)])
def test_measured_native_dll_paths_enforce_actual_utf16_boundary(characters, expected):
    # Короткий собственный temp нужен, чтобы точно воспроизвести обе стороны 260.
    with tempfile.TemporaryDirectory(prefix="dll-ci-", dir=smoke.REPO / ".runtime") as directory:
        package = Path(directory)
        runtime = package / "Contents/Windows/Runtime"
        runtime.mkdir(parents=True)
        prefix = "Библиотека_😀_"
        padding = characters - len(str(runtime / (prefix + ".dll")).encode("utf-16-le")) // 2
        assert padding > 0
        longest = runtime / (prefix + "x" * padding + ".dll")
        longest.write_bytes(b"file path inspection, not a loaded PE")
        (runtime / "short.DLL").write_bytes(b"second native DLL")
        (runtime / "longer-non-native-text-file.txt").write_bytes(b"not a DLL")
        report = smoke.runtime_native_dll_paths(package)
        assert report["runtime_native_dll_count"] == 2
        assert report["longest_runtime_native_dll_path"] == str(longest)
        assert report["runtime_native_dll_max_path_chars"] == characters
        assert report["runtime_native_dll_paths_below_max_path"] is expected
        assert len(str(longest)) < characters  # Emoji занимает две UTF-16 позиции.


def test_empty_runtime_does_not_claim_native_path_check_passed(tmp_path):
    with pytest.raises(ValueError, match="не содержит DLL"):
        smoke.runtime_native_dll_paths(tmp_path)


@pytest.mark.parametrize("source", [
    b'<script src="https://foreign.invalid/main.js"></script>',
    b'<script src="//foreign.invalid/main.js"></script>',
    b'<script src="/%2e%2e/private/main.js"></script>',
    b'<script src="/assets/../main.js"></script>',
    b'<script src="/assets/main.js?foreign=1"></script>',
    b'<title>Only synthetic placeholder</title>',
])
def test_document_requires_compiled_local_javascript(source):
    with pytest.raises(ValueError):
        smoke.document_resources(source)


@pytest.mark.parametrize("wrong", [None, "index.html", "assets/main.js", "assets/main.css"])
def test_http_checks_actual_compiled_bytes_and_private_headers(tmp_path, monkeypatch, wrong):
    files = {"index.html": b'<link href="/assets/main.css" rel="stylesheet"><script src="/assets/main.js"></script>',
             "assets/main.js": b"console.log('real compiled asset')", "assets/main.css": b"body { color: black; }"}
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    seen = []

    class Response(io.BytesIO):
        status = 200

    class Opener:
        def open(self, request, *, timeout):
            assert timeout == 30
            assert request.get_header("X-green-atlas-local-session") == "private-token"
            relative = request.full_url.removeprefix("http://127.0.0.1:43210/") or "index.html"
            seen.append(relative)
            content = files[relative]
            # Ответ SPA fallback 200 не доказывает наличие правильного JS/CSS.
            if wrong == relative:
                content = b"x" * len(content)
            return Response(content)

    monkeypatch.setattr(smoke, "build_opener", lambda *handlers: Opener())
    if wrong is not None:
        with pytest.raises(ValueError, match="отличается"):
            smoke.verify_web_http("http://127.0.0.1:43210", "private-token", tmp_path)
    else:
        receipt = smoke.verify_web_http("http://127.0.0.1:43210", "private-token", tmp_path)
        assert receipt == {"index_and_document_resources_match": True, "resources_checked": 2, "browser_javascript_executed": False}
        assert set(seen) == set(files)
        assert "private-token" not in str(receipt)


def test_sid_identity_uses_big_endian_authority_and_little_endian_subauthorities():
    value = bytes([1, 3]) + (5).to_bytes(6, "big") + b"".join(part.to_bytes(4, "little") for part in (21, 0x12345678, 1001))
    assert smoke.sid_string(value) == "S-1-5-21-305419896-1001"
    with pytest.raises(ValueError, match="SID"):
        smoke.sid_string(value[:-1])


def test_original_setup_argv_and_failed_stderr_are_not_accepted(tmp_path, monkeypatch):
    setup = tmp_path / "Сборка с пробелами/GreenAtlasSetup.exe"
    environment = {"APPDATA": "private"}
    seen = []

    def run(command, **kwargs):
        seen.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, b'{"installed":false,"package_sha256":null}', b"")

    monkeypatch.setattr(smoke.subprocess, "run", run)
    assert smoke.setup_command(setup, ["--status-json"], tmp_path, "status", environment) == {"installed": False, "package_sha256": None}
    command, kwargs = seen[0]
    assert command == [str(setup), "--status-json", "--quiet"]
    assert kwargs["env"] is environment and kwargs["timeout"] == 780
    assert kwargs.get("shell") is None
    monkeypatch.setattr(smoke.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, b"{}", b"unexpected"))
    with pytest.raises(RuntimeError, match="Настоящий Setup"):
        smoke.setup_command(setup, [], tmp_path, "failed", environment)


def test_installed_api_records_bounded_failure_without_private_ready_and_closes_child(tmp_path, monkeypatch):
    from app.shared import windows_process

    secret = "private_session_" + "a" * 43
    chunks = [b"Runtime startup\n", b'Traceback (most recent call last):\n',
              b'ValueError: Module worker metadata missing\n',
              ('{"service":"green-atlas-desktop","session_token":"' + secret + '"}\n').encode(),
              ("unlabelled private value: " + secret).encode()]
    calls = []

    class Child:
        closed = False

        def read_available(self, maximum):
            return chunks.pop(0) if chunks else b""

        def poll(self):
            return 1

        def close(self):
            self.closed = True

    child = Child()

    def launch(command, *, cwd, environment):
        calls.append((command, cwd, environment))
        return child

    monkeypatch.setattr(windows_process, "launch_owned", launch)
    monkeypatch.setattr(smoke, "os", SimpleNamespace(environ={"SystemRoot": str(tmp_path / "Windows")}))
    package, profile = tmp_path / "GreenAtlas.bundle", tmp_path / "Синтетический профиль"
    with pytest.raises(RuntimeError, match="до готовности"):
        smoke.installed_api(package, profile, tmp_path)
    diagnostic = (tmp_path / "installed-api.stderr.log").read_text(encoding="utf-8")
    assert "Traceback" in diagnostic and "Module worker metadata missing" in diagnostic
    assert "код завершения 1" in diagnostic
    assert secret not in diagnostic and "session_token" not in diagnostic
    assert "green-atlas-desktop" not in diagnostic
    assert child.closed
    command, cwd, environment = calls[0]
    windows = package / "Contents/Windows"
    assert command == [str(windows / "Runtime/GreenAtlasRuntime.exe"),
                       "--data-dir", str(profile / "Local/GreenAtlas/Workspace"),
                       "--web-dir", str(windows / "Web"),
                       "--ticket-root", str(profile / "Local/GreenAtlas/Transfers"),
                       "--cad-worker", str(windows / "Worker")]
    assert cwd == windows and environment["PATH"] == str(tmp_path / "Windows/System32")


def test_failed_ready_diagnostic_bounds_output_and_preserves_original_error(tmp_path):
    from scripts.desktop.windows_qualification import ready_record

    class Child:
        def __init__(self):
            self.chunks = [b"x" * 65537, b"\nlast traceback\n"]
            self.reads = 0

        def read_available(self, maximum):
            self.reads += 1
            return self.chunks.pop(0) if self.chunks else b""

        def poll(self):
            return 3

    child = Child()
    diagnostics = []
    with pytest.raises(ValueError, match="предел"):
        ready_record(child, on_failure=diagnostics.append)
    assert "last traceback" in diagnostics[0]
    assert "ограниченный хвост" in diagnostics[0]
    assert len(diagnostics[0].encode()) < 65536 + 256
    assert child.reads == 3

    def failed_write(text):
        raise OSError("Отказ сохранения локального отчёта")

    with pytest.raises(RuntimeError, match="до готовности") as failure:
        ready_record(child, on_failure=failed_write)
    assert any("Не удалось сохранить диагностику" in note for note in failure.value.__notes__)


def test_ready_success_does_not_write_diagnostic():
    from scripts.desktop.windows_qualification import ready_record

    class Child:
        def read_available(self, maximum):
            return (b'{"service":"green-atlas-desktop","protocol":1,'
                    b'"origin":"http://127.0.0.1:8123","session_token":"' + b"a" * 43 + b'"}\n')

    diagnostics = []
    assert ready_record(Child(), on_failure=diagnostics.append)["protocol"] == 1
    assert not diagnostics


@pytest.mark.skipif(smoke.os.name != "nt", reason="Нужны настоящие Windows COM интерфейсы")
def test_real_windows_installed_shortcut_target_with_cyrillic_space(tmp_path):
    from app.distribution.windows_shell import create_shortcut

    executable = tmp_path / "Кириллица с пробелами/GreenAtlasDesktop.exe"
    executable.parent.mkdir()
    executable.write_bytes("Не запускается: проверяется только COM ярлык".encode())
    shortcut = tmp_path / "Green Atlas.lnk"
    create_shortcut(shortcut, executable, arguments="--status-json")
    assert smoke.shortcut_target(shortcut) == (str(executable), "--status-json")


@pytest.mark.parametrize(
    "code,active_sequence,expected_error",
    [(0, [2, 1, 0], None), (7, [0], RuntimeError), (None, [1], TimeoutError),
     (0, [1], TimeoutError)],
    ids=["natural-descendant-exit", "nonzero-leader", "live-leader", "live-descendant"],
)
def test_clean_exit_requires_zero_leader_and_empty_job_without_forced_cleanup(
    monkeypatch, code, active_sequence, expected_error
):
    from scripts.desktop import windows_qualification as qualification

    clock = {"now": 0.0}
    monkeypatch.setattr(qualification, "time", SimpleNamespace(
        monotonic=lambda: clock["now"],
        sleep=lambda delay: clock.__setitem__("now", clock["now"] + delay),
    ))
    active = list(active_sequence)
    secret = "private_session_" + "x" * 43

    class Child:
        def __init__(self):
            self.chunks = [f'{{"service":"green-atlas-desktop","session_token":"{secret}"}}\n'.encode()]

        def poll(self):
            return code

        def read_available(self, maximum):
            return self.chunks.pop(0) if self.chunks else b""

        def active_processes(self):
            return active.pop(0) if len(active) > 1 else active[0]

        def close(self):
            pytest.fail("Graceful PASS не должен использовать close")

        def terminate_all(self):
            pytest.fail("Graceful PASS не должен использовать terminate")

    child = Child()
    if expected_error is None:
        result = qualification.wait_clean_exit(child, timeout=0.05, label="API119")
        assert result["exit_code"] == result["job_active_final"] == 0
        assert result["job_active_after_leader"] == 2
        assert result["elapsed_seconds"] == 0.02
    else:
        with pytest.raises(expected_error) as failure:
            qualification.wait_clean_exit(child, timeout=0.05, label="API119")
        assert "API119" in str(failure.value)
        assert secret not in str(failure.value) and "session_token" not in str(failure.value)
    assert clock["now"] <= 0.05


def test_clean_exit_drains_a_bounded_pipe_and_preserves_common_deadline(monkeypatch):
    from scripts.desktop import windows_qualification as qualification

    clock = {"now": 0.0}
    monkeypatch.setattr(qualification, "time", SimpleNamespace(
        monotonic=lambda: clock["now"],
        sleep=lambda delay: clock.__setitem__("now", clock["now"] + delay),
    ))
    reads = []

    class Child:
        def read_available(self, maximum):
            reads.append(maximum)
            return b"info\n" if len(reads) < 3 else b""

        def poll(self):
            return 0 if len(reads) >= 3 else None

        def active_processes(self):
            return 0 if len(reads) >= 3 else 1

    result = qualification.wait_clean_exit(Child(), timeout=0.03)
    assert result["output_bytes"] == 10 and reads == [16384] * 3
    assert clock["now"] == 0.02


@pytest.mark.skipif(smoke.os.name != "nt", reason="Нужен настоящий Windows Job Object")
def test_real_windows_clean_exit_waits_for_a_naturally_finishing_descendant(tmp_path):
    import sys

    from app.shared.windows_process import launch_owned

    from scripts.desktop.windows_qualification import wait_clean_exit

    program = (
        "import subprocess,sys; "
        "subprocess.Popen([sys.executable,'-c','import time;time.sleep(0.25)']); "
        "print('parent finished',flush=True)"
    )
    child = launch_owned([sys.executable, "-c", program], cwd=tmp_path)
    try:
        result = wait_clean_exit(child, timeout=5)
        assert result["exit_code"] == result["job_active_final"] == 0
        assert not child._terminated
    finally:
        child.close()
