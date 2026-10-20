"""Внешний Microsoft installer создаётся при сброшенном DLL search; ожидание снаружи."""

import hashlib
import importlib.util
import json
import subprocess
import sys
from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.distribution import windows_prerequisites as target


@pytest.mark.parametrize("failure", [None, "timeout", "restore", "exit"])
def test_external_creation_boundary_and_cleanup(tmp_path, monkeypatch, failure):
    directory = tmp_path / "Contents/Windows/Prerequisites"
    directory.mkdir(parents=True)
    installer = directory / "MicrosoftEdgeWebView2RuntimeInstallerX64.exe"
    installer.write_bytes(b"synthetic-installer-not-executed")
    (directory / "webview2-installer.json").write_text(json.dumps({"schema": "green-atlas.offline-webview2/1", "file": installer.name, "sha256": hashlib.sha256(installer.read_bytes()).hexdigest(), "publisher": "Microsoft Corporation", "authenticode": "Valid"}))
    active = False
    events = []

    @contextmanager
    def boundary(*, external):
        nonlocal active
        assert external is True
        active = True
        try:
            yield
        finally:
            active = False
        if failure == "restore":
            raise OSError("restore failure")

    class Process:
        def __init__(self, command):
            assert active
            assert command == [str(installer), "/silent", "/install"]
            events.append("created")

        def wait(self, timeout=None):
            assert active is False
            events.append(("wait", timeout))
            if failure == "timeout" and timeout is not None:
                raise subprocess.TimeoutExpired("synthetic", timeout)
            return 7 if failure == "exit" else 0

        def kill(self):
            assert active is False
            events.append("kill")

    versions = iter(["91.0.1.0", "152.0.4191.53"])
    monkeypatch.setattr(target, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(target, "child_dll_search", boundary)
    monkeypatch.setattr(target, "webview_runtime_version", lambda: next(versions))
    monkeypatch.setattr(target.subprocess, "Popen", Process)
    if failure is None:
        assert target.ensure_webview_runtime(tmp_path) == "152.0.4191.53"
        assert events == ["created", ("wait", 600)]
    else:
        with pytest.raises((RuntimeError, OSError)):
            target.ensure_webview_runtime(tmp_path)
        if failure in {"timeout", "restore"}:
            assert events[-2:] == ["kill", ("wait", None)]


def test_supported_runtime_does_not_launch_any_installer(monkeypatch):
    monkeypatch.setattr(target, "webview_runtime_version", lambda: "152.0.4191.53")
    monkeypatch.setattr(target.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("Внешний процесс не нужен"))
    assert target.ensure_webview_runtime(Path("not-read")) == "152.0.4191.53"


def test_non_windows_guard_refuses_before_payload_or_elevation(monkeypatch):
    monkeypatch.setattr(target, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(target, "webview_runtime_version", lambda: None)
    monkeypatch.setattr(target.subprocess, "Popen", lambda *args: pytest.fail("Mac не запускает WebView2 installer"))
    with pytest.raises(RuntimeError, match="только на Windows"):
        target.ensure_webview_runtime(Path("not-read"), confirm_elevation=lambda: pytest.fail("Mac не запрашивает UAC"))


@pytest.fixture
def offline_payload(tmp_path):
    directory = tmp_path / "Contents/Windows/Prerequisites"
    directory.mkdir(parents=True)
    installer = directory / "MicrosoftEdgeWebView2RuntimeInstallerX64.exe"
    installer.write_bytes(b"synthetic-Microsoft-installer-not-executed")
    sha = hashlib.sha256(installer.read_bytes()).hexdigest()
    receipt = directory / "webview2-installer.json"
    receipt.write_text(json.dumps({"schema": "green-atlas.offline-webview2/1", "file": installer.name,
        "sha256": sha, "publisher": "Microsoft Corporation", "authenticode": "Valid"}))
    return tmp_path, installer, receipt, sha


def mock_installers(monkeypatch, versions, normal_code, *, elevated_code=0, elevated_error=None):
    reads = iter(versions)
    calls = []
    monkeypatch.setattr(target, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(target, "webview_runtime_version", lambda: next(reads))

    class Process:
        def wait(self, *, timeout):
            assert timeout == 600
            return normal_code

    def normal(command):
        calls.append(("normal", command))
        return Process()

    def elevated(installer, *, expected_sha256, timeout_seconds):
        calls.append(("elevated", installer, expected_sha256, timeout_seconds))
        if elevated_error:
            raise elevated_error
        return elevated_code

    monkeypatch.setattr(target.subprocess, "Popen", normal)
    monkeypatch.setitem(sys.modules, "app.distribution.windows_prerequisite_elevation",
        SimpleNamespace(run_elevated_webview_installer=elevated))
    return calls


@pytest.mark.parametrize("code", [0x80040828, -2147219416])
@pytest.mark.parametrize("consent", [None, False])
def test_known_machine_conflict_requires_explicit_consent(offline_payload, monkeypatch, code, consent):
    package, _, _, _ = offline_payload
    calls = mock_installers(monkeypatch, ["131.0.2903.86", "131.0.2903.86"], code)
    callback = None if consent is None else lambda: consent
    with pytest.raises(RuntimeError, match="needs_machine_prerequisite_upgrade"):
        target.ensure_webview_runtime(package, confirm_elevation=callback)
    assert [call[0] for call in calls] == ["normal"]


@pytest.mark.parametrize("elevated_code", [0, 3010])
def test_machine_upgrade_binds_original_payload_sha_and_requires_fresh_runtime(offline_payload, monkeypatch, elevated_code):
    package, installer, receipt, sha = offline_payload
    calls = mock_installers(monkeypatch, ["131.0.2903.86", "131.0.2903.86", "155.0.4283.45"],
        0x80040828, elevated_code=elevated_code)

    def consent():
        # UI не меняет authority ожидаемого SHA уже проверенного payload.
        value = json.loads(receipt.read_bytes())
        value["sha256"] = "f" * 64
        receipt.write_text(json.dumps(value))
        return True

    assert target.ensure_webview_runtime(package, confirm_elevation=consent) == "155.0.4283.45"
    assert calls == [("normal", [str(installer), "/silent", "/install"]), ("elevated", installer, sha, 600)]


@pytest.mark.parametrize("failure", ["cancel", "still-old", "exit"])
def test_machine_upgrade_cancel_error_or_old_runtime_cannot_pass(offline_payload, monkeypatch, failure):
    package, _, _, _ = offline_payload
    calls = mock_installers(monkeypatch, ["131.0.2903.86"] * 3, 0x80040828,
        elevated_code=7 if failure == "exit" else 0,
        elevated_error=RuntimeError("UAC cancelled 1223") if failure == "cancel" else None)
    with pytest.raises(RuntimeError, match="cancelled|не обнаружен|кодом 7"):
        target.ensure_webview_runtime(package, confirm_elevation=lambda: True)
    assert [call[0] for call in calls] == ["normal", "elevated"]


def test_unrelated_installer_error_never_requests_elevation(offline_payload, monkeypatch):
    package, _, _, _ = offline_payload
    calls = mock_installers(monkeypatch, ["131.0.2903.86"], 7)
    with pytest.raises(RuntimeError, match="кодом 7"):
        target.ensure_webview_runtime(package, confirm_elevation=lambda: pytest.fail("Неизвестный код не даёт UAC"))
    assert [call[0] for call in calls] == ["normal"]


def test_parallel_machine_update_is_detected_before_any_uac(offline_payload, monkeypatch):
    package, _, _, _ = offline_payload
    calls = mock_installers(monkeypatch, ["131.0.2903.86", "155.0.4283.45"], 0x80040828)
    assert target.ensure_webview_runtime(package, confirm_elevation=lambda: pytest.fail("Runtime уже обновлён")) == "155.0.4283.45"
    assert [call[0] for call in calls] == ["normal"]


@pytest.mark.parametrize("machine,user,expected,opened", [
    ("131.0.2903.86", "155.0.4283.45", "131.0.2903.86", ["machine"]),
    ("155.0.4283.45", "152.0.4191.53", "155.0.4283.45", ["machine"]),
    (None, "155.0.4283.45", "155.0.4283.45", ["machine", "user"]),
    ("0.0.0.0", "155.0.4283.45", "155.0.4283.45", ["machine", "user"]),
])
def test_runtime_detection_uses_machine_before_user(monkeypatch, machine, user, expected, opened):
    queries = []

    @contextmanager
    def open_key(hive, path, reserved, rights):
        assert path.endswith(target.WEBVIEW_CLIENT)
        assert reserved == 0 and rights == 1
        queries.append(hive)
        value = machine if hive == "machine" else user
        if value is None:
            raise FileNotFoundError
        yield value

    monkeypatch.setattr(target, "os", SimpleNamespace(name="nt"))
    monkeypatch.setitem(sys.modules, "winreg", SimpleNamespace(HKEY_LOCAL_MACHINE="machine", HKEY_CURRENT_USER="user",
        KEY_READ=1, REG_SZ=1, OpenKey=open_key, QueryValueEx=lambda key, name: (key, 1)))
    assert target.webview_runtime_version() == expected
    assert queries == opened


@pytest.mark.parametrize("quiet", [True, False])
def test_setup_wires_machine_confirmation_only_for_interactive_install(tmp_path, monkeypatch, quiet):
    from app.distribution.windows_install import InstallReceipt

    path = Path(__file__).resolve().parents[3] / "tools/autocad-bridge/installer/windows/setup_entry.py"
    spec = importlib.util.spec_from_file_location("setup_consent_test", path)
    assert spec is not None and spec.loader is not None
    setup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(setup)
    messages = []
    callbacks = []
    monkeypatch.setattr(sys, "argv", ["Setup.exe", *( ["--quiet"] if quiet else [])])
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    monkeypatch.setattr(setup, "require_windows_user", lambda: None)
    monkeypatch.setattr(setup, "installation_mutex", nullcontext)
    monkeypatch.setattr(setup, "assert_applications_closed", lambda: None)
    monkeypatch.setattr(setup, "keep_setup", lambda root: tmp_path / "Setup.exe")
    monkeypatch.setattr(setup, "load_owned_bundle", lambda source: None)
    monkeypatch.setattr(setup, "write_install_receipt", lambda *args: None)
    monkeypatch.setattr(setup, "write_diagnostic", lambda *args: True)
    monkeypatch.setattr(setup, "message", lambda text, **kwargs: messages.append((text, kwargs)) or True)
    monkeypatch.setattr(setup, "WindowsShell", lambda *args: SimpleNamespace(publish=lambda *args: None, remove=lambda: None))
    monkeypatch.setattr(setup, "BundleInstaller", lambda *args: SimpleNamespace(recover=lambda **kwargs: False, install=lambda *args, **kwargs: InstallReceipt("install", "owned", None, "a" * 64)))
    monkeypatch.setattr(setup, "purge_setup_versions", lambda *args, **kwargs: ())

    def ensure(source, *, confirm_elevation):
        callbacks.append(confirm_elevation)
        if confirm_elevation is not None:
            assert confirm_elevation() is True
        return "155.0.4283.45"

    monkeypatch.setattr(setup, "ensure_webview_runtime", ensure)
    assert setup.main() == 0
    assert len(callbacks) == 1 and (callbacks[0] is None) == quiet
    if quiet:
        assert messages == []
    else:
        assert any("Microsoft WebView2" in text and options.get("confirm") is True for text, options in messages)


@pytest.mark.parametrize("quiet", [True, False])
def test_setup_gui_hides_machine_code_while_quiet_json_keeps_it(monkeypatch, quiet):
    path = Path(__file__).resolve().parents[3] / "tools/autocad-bridge/installer/windows/setup_entry.py"
    spec = importlib.util.spec_from_file_location("setup_machine_error_test", path)
    assert spec is not None and spec.loader is not None
    setup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(setup)
    machine = "needs_machine_prerequisite_upgrade: "
    human = "Для обновления системного WebView2 нужно согласие пользователя"
    shown, diagnostics = [], []

    def refuse():
        raise RuntimeError(machine + human)

    monkeypatch.setattr(sys, "argv", ["Setup.exe", *(["--quiet"] if quiet else [])])
    monkeypatch.setattr(setup, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(setup, "require_windows_user", refuse)
    monkeypatch.setattr(setup, "message", lambda text, **kwargs: shown.append(text) or True)
    monkeypatch.setattr(setup, "write_diagnostic", lambda text, **kwargs: diagnostics.append(json.loads(text)) or True)
    assert setup.main() == 1
    assert shown == ([] if quiet else [human])
    assert diagnostics == ([{"ok": False, "error": machine + human}] if quiet else [])
