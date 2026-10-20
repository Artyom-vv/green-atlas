"""Lossless JSON formatting and atomic writer tests without AutoCAD."""

from __future__ import annotations

import json
import re
import signal
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

NATIVE = Path(__file__).resolve().parent
FIXTURE = NATIVE / "fixtures" / "snapshot_writer_pretty.json"
SOURCE_PATH = 'fixtures/Сцена "1".dwg'
JSON_TOKENS = re.compile(
    rb'"(?:\\.|[^"\\])*"|(?:-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?)'
    rb"|true|false|null|[{}\[\],:]|[ \t\r\n]+"
)


def tokens(payload: bytes) -> list[bytes]:
    """Compare all JSON lexemes; whitespace within a string is one token."""
    found = [match.group() for match in JSON_TOKENS.finditer(payload)]
    assert b"".join(found) == payload
    return [token for token in found if not token.isspace()]


@pytest.fixture(scope="module")
def compactor(tmp_path_factory: pytest.TempPathFactory) -> Path:
    executable = tmp_path_factory.mktemp("compact-json") / "check"
    subprocess.run(
        [
            "c++",
            "-std=c++17",
            "-Wall",
            "-Wextra",
            "-Werror",
            str(NATIVE / "test_compact_json.cpp"),
            "-o",
            str(executable),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return executable


@pytest.fixture(scope="module")
def writer(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if sys.platform != "darwin":
        pytest.skip("The production file helper uses macOS CommonCrypto")
    executable = tmp_path_factory.mktemp("compact-snapshot") / "check"
    compile_writer(executable, "test_snapshot_runtime.cpp")
    return executable


def compile_writer(executable: Path, driver: str) -> None:
    subprocess.run(
        [
            "xcrun",
            "clang++",
            "-std=c++17",
            "-Wno-deprecated-declarations",
            *[
                str(NATIVE / name)
                for name in (
                    driver,
                    "snapshot_writer.cpp",
                    "operation_control.cpp",
                    "geometry_math.cpp",
                    "file_io.cpp",
                )
            ],
            "-o",
            str(executable),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def capture(writer: Path, destination: Path, cancel_at: int = 0) -> dict[str, object]:
    result = subprocess.run(
        [str(writer), SOURCE_PATH, str(cancel_at), "live", str(destination)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def legacy_fixture_for_current_version() -> bytes:
    """Plugin metadata is versioned independently of whitespace formatting."""
    version = re.search(
        r'kPluginVersion\s*=\s*"([^"]+)"', (NATIVE / "bridge_config.h").read_text()
    )
    assert version
    return re.sub(
        rb'("plugin_version": ")[^"]+("\s*,)',
        lambda match: match[1] + version[1].encode() + match[2],
        FIXTURE.read_bytes(),
        count=1,
    )


def test_compactor_preserves_lexemes_and_reports_destination_errors(
    compactor: Path,
) -> None:
    subprocess.run([str(compactor)], check=True, capture_output=True)


def test_pretty_snapshot_retains_all_parsed_values_and_lexemes(
    compactor: Path,
) -> None:
    legacy = FIXTURE.read_bytes()
    compact = subprocess.run(
        [str(compactor), "--compact"],
        input=legacy,
        check=True,
        capture_output=True,
    ).stdout
    assert json.loads(compact, parse_float=Decimal) == json.loads(
        legacy, parse_float=Decimal
    )
    assert tokens(compact) == tokens(legacy)
    assert len(compact) < len(legacy)
    assert len(compact) == sum(map(len, tokens(legacy)))


def test_production_writer_only_removes_formatting_from_previous_payload(
    writer: Path, tmp_path: Path
) -> None:
    destination = tmp_path / "display.json"
    result = capture(writer, destination)
    result.pop("transfer_facts")
    assert result == {"success": True, "path": str(destination), "error": ""}
    compact = destination.read_bytes()
    legacy = legacy_fixture_for_current_version()
    assert json.loads(compact, parse_float=Decimal) == json.loads(
        legacy, parse_float=Decimal
    )
    assert tokens(compact) == tokens(legacy)
    assert len(compact) == sum(map(len, tokens(legacy)))
    assert b"0.12345678901234566" in compact
    assert not Path(str(destination) + ".tmp").exists()


@pytest.mark.parametrize("cancel_at", range(1, 10))
def test_cancel_preserves_published_snapshot_and_removes_staging(
    writer: Path, tmp_path: Path, cancel_at: int
) -> None:
    destination = tmp_path / "display.json"
    destination.write_bytes(b"previous confirmed snapshot")
    result = capture(writer, destination, cancel_at)
    assert result == {
        "success": False,
        "path": "",
        "error": "native topology export cancelled",
    }
    assert destination.read_bytes() == b"previous confirmed snapshot"
    assert not Path(str(destination) + ".tmp").exists()


def test_atomic_publication_failure_keeps_previous_destination(
    writer: Path, tmp_path: Path
) -> None:
    destination = tmp_path / "protected"
    destination.mkdir()
    previous = destination / "previous.json"
    previous.write_bytes(b"previous confirmed snapshot")
    result = capture(writer, destination)
    assert result["success"] is False
    assert result["error"] == "topology sidecar could not be published atomically"
    assert previous.read_bytes() == b"previous confirmed snapshot"
    assert not Path(str(destination) + ".tmp").exists()


def test_failed_file_flush_cannot_replace_published_snapshot(
    writer: Path, tmp_path: Path
) -> None:
    import resource

    destination = tmp_path / "display.json"
    destination.write_bytes(b"previous confirmed snapshot")

    def restrict_file_size() -> None:
        # A real write/flush error without filling the user's filesystem.
        signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
        resource.setrlimit(resource.RLIMIT_FSIZE, (1024, 1024))

    execution = subprocess.run(
        [str(writer), SOURCE_PATH, "0", "live", str(destination)],
        preexec_fn=restrict_file_size,
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(execution.stdout)
    assert result["success"] is False
    assert result["error"] == "topology sidecar could not be published atomically"
    assert destination.read_bytes() == b"previous confirmed snapshot"
    assert not Path(str(destination) + ".tmp").exists()


def test_control_exception_closes_file_before_removing_staging(
    writer: Path, tmp_path: Path
) -> None:
    executable = tmp_path / "throwing-control"
    compile_writer(executable, "test_snapshot_cancel_exception.cpp")
    destination = tmp_path / "display.json"
    destination.write_bytes(b"previous confirmed snapshot")
    subprocess.run([str(executable), str(destination)], check=True)
    assert destination.read_bytes() == b"previous confirmed snapshot"
    assert not Path(str(destination) + ".tmp").exists()
