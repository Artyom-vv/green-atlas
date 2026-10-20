"""Подготовка профиля запускает только проверенный payload, без настоящей Windows."""

import importlib.util
import json
import shutil
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
import test_windows_distribution

from app.distribution.windows_install import InstallReceipt

factory = test_windows_distribution.factory
ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def setup(tmp_path, monkeypatch):
    path = ROOT / "tools/autocad-bridge/installer/windows/setup_entry.py"
    spec = importlib.util.spec_from_file_location("profile_bootstrap_setup", path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    monkeypatch.setattr(
        value, "sys", SimpleNamespace(frozen=True, _MEIPASS=str(tmp_path / "extracted"))
    )
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setattr(value, "sleep", lambda _seconds: None)
    return value


@pytest.fixture
def payload(factory, setup):
    package, _, _ = factory("profile")
    source = Path(setup.sys._MEIPASS) / "payload/GreenAtlas.bundle"
    source.parent.mkdir(parents=True)
    shutil.copytree(package, source)
    return source


class Process:
    def __init__(self, output, code=0):
        self.output, self.code, self.closed = bytearray(output), code, False
        self.limits = []

    def read_available(self, limit):
        self.limits.append(limit)
        chunk = self.output[:limit]
        del self.output[:limit]
        return chunk

    def poll(self):
        return self.code

    def active_processes(self):
        return int(self.code is None)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True


def receipt(setup, **changes):
    return {
        "schema": "green-atlas.desktop-profile/1",
        "prepared": True,
        "profile": str(Path(setup.os.environ["LOCALAPPDATA"]) / "GreenAtlas"),
        **changes,
    }


def transport(monkeypatch, setup, output, *, code=0):
    process = Process(output, code)
    calls = []

    def launch(command, **kwargs):
        calls.append((command, kwargs))
        return process

    monkeypatch.setattr(setup, "launch_owned", launch)
    return process, calls


def test_profile_helper_is_fixed_sealed_payload_and_does_not_write_setup_files(
    setup, payload, monkeypatch
):
    process, calls = transport(monkeypatch, setup, json.dumps(receipt(setup)).encode())
    checks = []
    verify = setup.load_owned_bundle

    def verified(source):
        checks.append(source)
        assert not calls
        return verify(source)

    monkeypatch.setattr(setup, "load_owned_bundle", verified)
    assert setup.prepare_profile() == payload
    executable = payload / "Contents/Windows/GreenAtlasDesktop.exe"
    assert calls == [
        ([str(executable), "--prepare-profile"], {"cwd": executable.parent})
    ]
    assert checks == [payload]
    assert process.closed and all(0 < limit <= 16385 for limit in process.limits)
    assert not (Path(setup.os.environ["LOCALAPPDATA"]) / "GreenAtlas").exists()


@pytest.mark.parametrize("damage", ["host", "manifest"])
def test_tampered_payload_is_refused_before_native_launch(
    setup, payload, monkeypatch, damage
):
    path = payload / (
        "Contents/Windows/GreenAtlasDesktop.exe"
        if damage == "host"
        else "distribution-manifest.json"
    )
    path.write_bytes(b"corrupted-payload")
    monkeypatch.setattr(
        setup,
        "launch_owned",
        lambda *_args, **_kwargs: pytest.fail("Unverified EXE must not run"),
    )
    with pytest.raises(ValueError):
        setup.prepare_profile()


@pytest.mark.parametrize(
    "changes",
    [
        {"schema": "wrong"},
        {"prepared": 1},
        {"prepared": False},
        {"profile": "relative/GreenAtlas"},
        {"profile": "/another-user/GreenAtlas"},
        {"profile": None},
        {"extra": True},
    ],
)
def test_wrong_receipt_cannot_select_another_profile(
    setup, payload, monkeypatch, changes
):
    process, _ = transport(
        monkeypatch, setup, json.dumps(receipt(setup, **changes)).encode()
    )
    with pytest.raises(ValueError, match="Не подтверждён"):
        setup.prepare_profile()
    assert process.closed


@pytest.mark.parametrize(
    "output",
    [b"private-invalid-output", b"[]", b'{"schema":"a","schema":"b"}', b"\xff"],
)
def test_malformed_receipt_is_a_safe_failure(setup, payload, monkeypatch, output):
    process, _ = transport(monkeypatch, setup, output)
    with pytest.raises(ValueError) as error:
        setup.prepare_profile()
    assert "private-invalid-output" not in str(error.value)
    assert process.closed


def test_nonzero_exit_never_becomes_success_even_with_valid_receipt(
    setup, payload, monkeypatch
):
    process, _ = transport(
        monkeypatch, setup, json.dumps(receipt(setup)).encode(), code=1
    )
    with pytest.raises(ValueError, match="Не подтверждён"):
        setup.prepare_profile()
    assert process.closed


def test_native_failure_keeps_only_validated_role_code_and_win32(
    setup, payload, monkeypatch
):
    failure = {
        "schema": "green-atlas.desktop-profile/1",
        "prepared": False,
        "error": {
            "code": "PROFILE_BOOTSTRAP_HAS_DATA",
            "role": "Workspace",
            "win32_error": 5,
        },
    }
    transport(monkeypatch, setup, json.dumps(failure).encode(), code=1)
    with pytest.raises(
        ValueError, match="Workspace, PROFILE_BOOTSTRAP_HAS_DATA, Win32 5"
    ):
        setup.prepare_profile()
    failure["error"]["role"] = {"private": "not-a-role"}
    transport(monkeypatch, setup, json.dumps(failure).encode(), code=1)
    with pytest.raises(ValueError, match="Не подтверждён") as error:
        setup.prepare_profile()
    assert "private" not in str(error.value)


@pytest.mark.parametrize("failure", ["limit", "timeout"])
def test_helper_resources_are_bounded_and_closed_on_failure(
    setup, payload, monkeypatch, failure
):
    process, _ = transport(
        monkeypatch, setup, b"x" * 20000 if failure == "limit" else b"", code=None
    )
    if failure == "timeout":
        times = iter([0, 31])
        monkeypatch.setattr(setup, "monotonic", lambda: next(times))
    with pytest.raises((ValueError, RuntimeError), match="размер|время"):
        setup.prepare_profile()
    assert process.closed
    assert not (Path(setup.os.environ["LOCALAPPDATA"]) / "GreenAtlas").exists()


def main_boundaries(setup, monkeypatch, events):
    @contextmanager
    def mutex():
        events.append("mutex-enter")
        try:
            yield
        finally:
            events.append("mutex-exit")

    monkeypatch.setattr(setup, "require_windows_user", lambda: None)
    monkeypatch.setattr(setup, "installation_mutex", mutex)
    monkeypatch.setattr(
        setup, "assert_applications_closed", lambda: events.append("closed")
    )
    monkeypatch.setattr(setup, "write_diagnostic", lambda *_args, **_kwargs: True)


def test_preflight_runs_verified_helper_under_mutex_without_cad_or_installer_mutations(
    setup, payload, monkeypatch
):
    events = []
    main_boundaries(setup, monkeypatch, events)
    monkeypatch.delenv("APPDATA")
    monkeypatch.setattr(sys, "argv", ["Setup.exe", "--quiet", "--prepare-profile"])
    process, calls = transport(monkeypatch, setup, json.dumps(receipt(setup)).encode())
    launch = setup.launch_owned
    monkeypatch.setattr(
        setup,
        "launch_owned",
        lambda *a, **kw: (events.append("helper"), launch(*a, **kw))[1],
    )
    monkeypatch.setattr(
        setup,
        "BundleInstaller",
        lambda *_a: pytest.fail("No install/recovery in preflight"),
    )
    monkeypatch.setattr(
        setup, "keep_setup", lambda *_a: pytest.fail("No setup copy in preflight")
    )
    monkeypatch.setattr(
        setup,
        "ensure_webview_runtime",
        lambda *_a, **_kw: pytest.fail("No prerequisite in preflight"),
    )
    assert setup.main() == 0
    assert events == ["mutex-enter", "closed", "helper", "mutex-exit"]
    assert len(calls) == 1 and process.closed


@pytest.mark.parametrize("arguments", [[], ["--prepare-profile"]])
def test_profile_failure_stops_before_copy_recovery_or_install(
    setup, monkeypatch, arguments
):
    events = []
    main_boundaries(setup, monkeypatch, events)
    monkeypatch.setattr(sys, "argv", ["Setup.exe", "--quiet", *arguments])
    monkeypatch.setattr(
        setup,
        "BundleInstaller",
        lambda *_args: SimpleNamespace(installed=Path("missing")),
    )

    def refuse():
        events.append("profile")
        raise ValueError("PROFILE_BOOTSTRAP_HAS_DATA")

    monkeypatch.setattr(setup, "prepare_profile", refuse)
    monkeypatch.setattr(
        setup,
        "keep_setup",
        lambda *_args: pytest.fail("Profile failure must precede copying"),
    )
    assert setup.main() == 1
    assert events == ["mutex-enter", "closed", "profile", "mutex-exit"]


@pytest.mark.parametrize("wizard", [False, True])
def test_install_prepares_before_copy_and_reuses_verified_source(
    setup, monkeypatch, tmp_path, wizard
):
    events = []
    main_boundaries(setup, monkeypatch, events)
    source = tmp_path / "verified-payload"
    arguments = (
        ["--native-uninstaller", str(tmp_path / "native-uninstall.exe")]
        if wizard
        else []
    )
    monkeypatch.setattr(sys, "argv", ["Setup.exe", "--quiet", *arguments])
    monkeypatch.setattr(setup, "native_uninstaller", lambda _local, path: path)
    monkeypatch.setattr(
        setup, "prepare_profile", lambda: (events.append("profile"), source)[1]
    )
    monkeypatch.setattr(
        setup,
        "keep_setup",
        lambda *_args: (events.append("keep"), tmp_path / "Setup.exe")[1],
    )
    monkeypatch.setattr(
        setup,
        "load_owned_bundle",
        lambda *_args: pytest.fail("Already verified source is reused"),
    )
    monkeypatch.setattr(
        setup,
        "WindowsShell",
        lambda *_a, **_kw: SimpleNamespace(
            publish=lambda *_a: None, remove=lambda: None
        ),
    )
    monkeypatch.setattr(
        setup,
        "BundleInstaller",
        lambda *_a: SimpleNamespace(
            recover=lambda **_kw: events.append("recover"),
            install=lambda value, **_kw: (
                events.append("install"),
                InstallReceipt("install", str(value), None, "a" * 64),
            )[1],
        ),
    )
    monkeypatch.setattr(
        setup,
        "ensure_webview_runtime",
        lambda value, **_kw: (
            events.append("webview") if value == source else pytest.fail("Wrong source")
        ),
    )
    monkeypatch.setattr(
        setup,
        "purge_setup_versions",
        lambda *_a, **_kw: (events.append("purge"), ())[1],
    )
    monkeypatch.setattr(
        setup, "write_install_receipt", lambda *_a: events.append("receipt")
    )
    assert setup.main() == 0
    assert events == [
        "mutex-enter",
        "closed",
        "profile",
        "keep",
        "recover",
        "webview",
        "install",
        "purge",
        "receipt",
        "mutex-exit",
    ]


@pytest.mark.parametrize(
    "arguments",
    [
        ["--check-closed"],
        ["--status-json"],
        ["--dependency-action", "discover", "--dependency-result", "unused.ini"],
        ["--uninstall"],
        ["--rollback"],
        ["--purge-app-backups"],
    ],
)
def test_existing_noninstall_actions_do_not_prepare_or_repair_profile(
    setup, monkeypatch, arguments
):
    from app.distribution import dependency_setup

    events = []
    main_boundaries(setup, monkeypatch, events)
    monkeypatch.setattr(sys, "argv", ["Setup.exe", "--quiet", *arguments])
    monkeypatch.setattr(
        setup,
        "prepare_profile",
        lambda: pytest.fail("Non-install actions must not prepare profile"),
    )
    monkeypatch.setattr(
        setup,
        "BundleInstaller",
        lambda *_a: SimpleNamespace(installed=Path("not-present-profile-fixture")),
    )
    monkeypatch.setattr(dependency_setup, "wizard_action", lambda *_a: True)
    monkeypatch.setattr(
        setup,
        "keep_setup",
        lambda *_a: (_ for _ in ()).throw(
            ValueError("Stop after proving maintenance dispatch")
        ),
    )
    result = setup.main()
    assert result == (
        1 if arguments[0] in {"--uninstall", "--rollback", "--purge-app-backups"} else 0
    )


def test_wizard_prepares_profile_in_preinstall_and_keeps_it_out_of_uninstall():
    source = (ROOT / "tools/autocad-bridge/installer/windows/wizard.iss").read_text()
    preflight = source.split("function PrepareToInstall", 1)[1].split(
        "function InitializeUninstall", 1
    )[0]
    assert (
        preflight.index("GetSHA256OfFile")
        < preflight.index("--check-closed")
        < preflight.index("--prepare-profile")
    )
    assert source.count("--prepare-profile") == 1
    assert "if Stage = 'profile' then" in source


@pytest.mark.parametrize(
    "boundary", ["not-frozen", "no-payload-root", "relative-local"]
)
def test_profile_preparation_rejects_invalid_invocation_before_launch(
    setup, monkeypatch, boundary
):
    if boundary == "not-frozen":
        setup.sys.frozen = False
    elif boundary == "no-payload-root":
        del setup.sys._MEIPASS
    else:
        monkeypatch.setenv("LOCALAPPDATA", "relative-user")
    monkeypatch.setattr(
        setup,
        "launch_owned",
        lambda *_a, **_kw: pytest.fail(
            "Invalid profile invocation must not launch helper"
        ),
    )
    with pytest.raises(ValueError):
        setup.prepare_profile()
