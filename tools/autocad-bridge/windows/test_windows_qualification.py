"""Проверки безопасного Windows qualification runner без Win32 и CAD на Mac."""
from __future__ import annotations

import importlib.util
import hashlib
import json
import struct
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("windows_qualification", ROOT / "scripts/desktop/windows_qualification.py")
assert SPEC is not None and SPEC.loader is not None
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


class Child:
    def __init__(self, chunks: list[bytes]):
        self.chunks = chunks
        self.closed = False

    def read_available(self, maximum: int) -> bytes:
        return self.chunks.pop(0) if self.chunks else b""

    def poll(self) -> int | None:
        return None

    def close(self) -> None:
        self.closed = True


def test_ready_is_bounded_and_secret_is_not_logged(capsys) -> None:
    value = {"service": "green-atlas-desktop", "protocol": 1,
             "origin": "http://127.0.0.1:8123", "session_token": "a" * 43}
    body = json.dumps(value).encode() + b"\n"
    child = Child([b"startup warning\n", body[:15], body[15:]])
    assert RUNNER.ready_record(child, 1) == value
    assert capsys.readouterr().out == ""
    with pytest.raises(ValueError, match="предел"):
        RUNNER.ready_record(Child([b"x" * 65537]), 1)
    value["origin"] = "http://127.0.0.1:8123.evil"
    with pytest.raises(ValueError, match="контракт"):
        RUNNER.ready_record(Child([json.dumps(value).encode() + b"\n"]), 1)


def test_clean_environment_has_no_developer_or_user_configuration(tmp_path: Path) -> None:
    inherited = {"SystemRoot": str(tmp_path / "Windows"), "USERPROFILE": "actual-user",
                 "PYTHONHOME": "python", "PYTHONPATH": "source", "NODE_PATH": "node",
                 "GREEN_ATLAS_DB_PATH": "actual-projects", "OPENAI_API_KEY": "secret"}
    value = RUNNER.clean_environment(tmp_path / "synthetic", inherited)
    assert value["PATH"] == str(tmp_path / "Windows/System32")
    assert len({key.casefold() for key in value}) == len(value)
    for forbidden in ("PYTHONHOME", "PYTHONPATH", "NODE_PATH", "GREEN_ATLAS_DB_PATH", "OPENAI_API_KEY"):
        assert forbidden not in value
    assert value["USERPROFILE"] != "actual-user"


def test_smoke_rejects_escape_before_creating_directories(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(RUNNER, "REPO", tmp_path)
    target = tmp_path / ".runtime/../outside"
    with pytest.raises(ValueError, match=".runtime"):
        RUNNER.frozen_smoke(tmp_path / "missing.bundle", target)
    assert not (tmp_path / "outside").exists()


def test_readonly_dll_preflight_covers_exact_pe_set(tmp_path: Path) -> None:
    windows = tmp_path / "Contents/Windows"
    windows.mkdir(parents=True)
    data = bytearray(512)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 60, 64)
    data[64:68] = b"PE\0\0"
    struct.pack_into("<H", data, 68, 0x8664)
    struct.pack_into("<H", data, 84, 112)
    struct.pack_into("<H", data, 88, 0x20B)
    struct.pack_into("<I", data, 148, 512)
    (windows / "host.exe").write_bytes(data)
    report = {"schema": "green-atlas.windows-dependencies/1", "resolution": "build-file-inventory",
              "native_execution_qualified": False,
              "binaries": [{"binary": "Contents/Windows/host.exe", "imports": []}]}
    path = windows / "dependency-inventory.json"
    path.write_text(json.dumps(report))
    assert RUNNER.dll_preflight(tmp_path)["binaries_checked"] == 1
    (windows / "unrecorded.dll").write_bytes(data)
    with pytest.raises(ValueError, match="не покрывает"):
        RUNNER.dll_preflight(tmp_path)


def test_readonly_dll_preflight_checks_pinned_objectarx_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.distribution.objectarx_imports import LIBRARIES, SdkCatalog
    from app.distribution import windows_dependencies

    windows = tmp_path / "Contents/Windows"
    worker = windows / "Worker"
    evidence = windows / "BuildEvidence"
    worker.mkdir(parents=True)
    evidence.mkdir()
    catalog = SdkCatalog(
        dict(LIBRARIES),
        {pin.module: name for name, pin in LIBRARIES.items() if pin.module},
    )
    binaries = []
    for target, name, parent in (
        ("gui", "GreenAtlasBridge.arx", windows),
        ("core", "GreenAtlasQuery.crx", worker),
    ):
        binary = parent / name
        binary.write_bytes(b"synthetic PE " + target.encode())
        sdk_files = [
            {"path": path, "size": pin.size, "sha256": pin.sha256}
            for path, pin in LIBRARIES.items()
            if target == "gui" or path != "lib-x64/acad.lib"
        ]
        (evidence / f"{target}.json").write_text(json.dumps({
            "schema": "green-atlas.packaged-native-evidence/1",
            "target": target,
            "execution_qualified": False,
            "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
            "sdk_files": sdk_files,
        }))
        imports = [catalog.module_evidence("accore.dll", target="gui")] if target == "gui" else []
        binaries.append({"binary": binary.relative_to(tmp_path).as_posix(), "imports": imports})

    monkeypatch.setattr(
        windows_dependencies, "pe_imports",
        lambda path: ("accore.dll",) if path.suffix == ".arx" else (),
    )
    report = {
        "schema": "green-atlas.windows-dependencies/1",
        "resolution": "objectarx-sdk-import-contract",
        "native_execution_qualified": False,
        "objectarx_sdk": catalog.report(),
        "binaries": binaries,
    }
    inventory = windows / "dependency-inventory.json"
    inventory.write_text(json.dumps(report))
    assert RUNNER.dll_preflight(tmp_path)["binaries_checked"] == 2
    report["binaries"][0]["imports"][0]["import_library_sha256"] = "0" * 64
    inventory.write_text(json.dumps(report))
    with pytest.raises(ValueError, match="ObjectARX DLL import"):
        RUNNER.dll_preflight(tmp_path)


def test_output_cannot_be_created_inside_checked_payload(tmp_path: Path, monkeypatch) -> None:
    package = tmp_path / "GreenAtlas.bundle"
    package.mkdir()
    output = package / "report.json"
    monkeypatch.setattr("sys.argv", ["windows_qualification", "--package-root", str(package), "--output", str(output)])
    with pytest.raises(SystemExit) as stopped:
        RUNNER.main()
    assert stopped.value.code == 2 and not output.exists()


def test_nested_output_inside_payload_creates_no_directory(tmp_path: Path, monkeypatch) -> None:
    package = tmp_path / "GreenAtlas.bundle"
    package.mkdir()
    output = package / "new/qa/report.json"
    monkeypatch.setattr("sys.argv", ["windows_qualification", "--package-root", str(package), "--output", str(output)])
    with pytest.raises(SystemExit) as stopped:
        RUNNER.main()
    assert stopped.value.code == 2 and list(package.iterdir()) == []


def test_fresh_output_parent_records_failure_without_touching_payload(tmp_path: Path, monkeypatch) -> None:
    package = tmp_path / "GreenAtlas.bundle"
    package.mkdir()
    output = tmp_path / ".runtime/qa/windows-smoke.json"
    monkeypatch.setattr("sys.argv", ["windows_qualification", "--package-root", str(package), "--output", str(output)])
    assert RUNNER.main() == 1  # У пустого комплекта нет manifest; отказ всё равно получает квитанцию.
    assert json.loads(output.read_bytes())["passed"] is False
    assert list(package.iterdir()) == []


def test_output_parent_cannot_redirect_into_payload(tmp_path: Path, monkeypatch) -> None:
    package = tmp_path / "GreenAtlas.bundle"
    package.mkdir()
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(package, target_is_directory=True)
    except OSError:
        pytest.skip("Символические ссылки недоступны в этом окружении")
    output = alias / "report.json"
    monkeypatch.setattr("sys.argv", ["windows_qualification", "--package-root", str(package), "--output", str(output)])
    with pytest.raises(SystemExit) as stopped:
        RUNNER.main()
    assert stopped.value.code == 2 and not output.exists()


def test_package_alias_cannot_hide_output_inside_real_payload(tmp_path: Path, monkeypatch) -> None:
    package = tmp_path / "Real.bundle"
    package.mkdir()
    alias = tmp_path / "Alias.bundle"
    try:
        alias.symlink_to(package, target_is_directory=True)
    except OSError:
        pytest.skip("Символические ссылки недоступны в этом окружении")
    output = package / "new/qa/report.json"
    monkeypatch.setattr("sys.argv", ["windows_qualification", "--package-root", str(alias), "--output", str(output)])
    with pytest.raises(SystemExit) as stopped:
        RUNNER.main()
    assert stopped.value.code == 2 and list(package.iterdir()) == []


@pytest.mark.parametrize("exit_code", [0, 3])
def test_host_diagnostics_use_only_fixed_switch_and_always_release_child(
    tmp_path: Path, monkeypatch, exit_code: int
) -> None:
    from app.shared import windows_process

    class Diagnostic(Child):
        def poll(self) -> int:
            return exit_code

        def active_processes(self) -> int:
            return 0

    child = Diagnostic([b'{"passed":true}\n'])
    calls = []

    def launch(command, *, cwd):
        calls.append((command, cwd))
        return child

    monkeypatch.setattr(windows_process, "launch_owned", launch)
    binary = tmp_path / "GreenAtlasDesktop.exe"
    if exit_code:
        with pytest.raises(RuntimeError, match="вернул ошибку"):
            RUNNER.executable_json(binary, "--self-test")
    else:
        assert RUNNER.executable_json(binary, "--self-test") == {"passed": True}
    assert calls == [([str(binary), "--self-test"], tmp_path)]
    assert child.closed
    with pytest.raises(ValueError, match="Неизвестная"):
        RUNNER.executable_json(binary, "--ticket-id")
    assert len(calls) == 1
