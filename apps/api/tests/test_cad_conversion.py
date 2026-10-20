import sys
from pathlib import Path

import ezdxf
import psutil
import pytest

from app.cad_import.archive.cache import file_sha256, publish
from app.cad_import.contracts import (
    CadConversion,
    CadConversionError,
    CadConversionMemoryExceeded,
    ConverterIdentity,
    DrawingInspection,
)
from app.cad_import.legacy import conversion
from app.cad_import.legacy.conversion import LibreDwgConverter
from app.cad_import.legacy.dxf_inspection import DxfInspector
from app.cad_import.legacy.inspection import inspect_drawing
from app.cad_import.legacy.process import ProcessResult, run_converter
from app.cad_import.policy import ConversionPolicy


def test_inspection_worker_runs_outside_api_with_no_pythonpath(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PYTHONPATH", raising=False)
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "source.dxf"
    document = ezdxf.new("R2018")
    document.modelspace().add_circle((10, 20), 2)
    document.saveas(source)
    before = file_sha256(source)

    result = DxfInspector(tmp_path / "cache").inspect_dxf(source)

    assert result.modelspace_entities == {"CIRCLE": 1}
    assert file_sha256(source) == before
    assert result.boundary_catalog is not None


@pytest.fixture
def converter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> LibreDwgConverter:
    identity = ConverterIdentity(
        name="libredwg",
        version="dwg2dxf test",
        executable_sha256="a" * 64,
    )
    monkeypatch.setattr(conversion, "converter_identity", lambda _: identity)
    monkeypatch.setattr(
        LibreDwgConverter, "_inspect", lambda _, path: inspect_drawing(path)
    )
    return LibreDwgConverter(Path(sys.executable), tmp_path / "cache")


def test_original_immutable_cache_reused_and_corruption_rebuilt(
    converter: LibreDwgConverter,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "Деревья.dwg"
    original = b"AC1032fixture bytes"
    source.write_bytes(original)
    calls = []

    def convert(
        command: list[str], output: Path, log: Path, _: ConversionPolicy
    ) -> ProcessResult:
        calls.append(command)
        assert Path(command[-1]).read_bytes() == original
        document = ezdxf.new("R2018")
        document.modelspace().add_circle((10, 20), 2)
        document.saveas(output)
        log.write_text("WARNING: unsupported custom object\n", encoding="utf-8")
        return ProcessResult(0, 0.25, 1024)

    monkeypatch.setattr(conversion, "run_converter", convert)
    first = converter.convert(source)
    assert source.read_bytes() == original
    assert first.evidence.inspection.modelspace_entities == {"CIRCLE": 1}
    assert first.evidence.integrity == "requires_review"
    assert first.evidence.output_sha256 == file_sha256(first.path)
    assert not first.cache_hit
    assert converter.convert(source).cache_hit
    assert len(calls) == 1
    first.path.write_bytes(b"damaged")
    repaired = converter.convert(source)
    assert not repaired.cache_hit
    assert len(calls) == 2
    assert file_sha256(repaired.path) == repaired.evidence.output_sha256


def test_unreadable_dxf_is_not_published_as_success(
    converter: LibreDwgConverter,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.dwg"
    source.write_bytes(b"AC1032original")

    def convert(
        _: list[str], output: Path, log: Path, policy: ConversionPolicy
    ) -> ProcessResult:
        output.write_bytes(b"not a DXF")
        log.write_text("", encoding="utf-8")
        return ProcessResult(0, 0.1, 0)

    monkeypatch.setattr(conversion, "run_converter", convert)
    with pytest.raises((OSError, ezdxf.DXFError)):
        converter.convert(source)
    assert not list(converter.cache.rglob("conversion.json"))
    assert source.read_bytes() == b"AC1032original"


@pytest.mark.parametrize("content", [b"", b"notdwg", b"AC1000old"])
def test_invalid_sources_rejected_before_process(
    converter: LibreDwgConverter,
    tmp_path: Path,
    content: bytes,
) -> None:
    source = tmp_path / "source.dwg"
    source.write_bytes(content)
    with pytest.raises(CadConversionError):
        converter.convert(source)
    assert not converter.cache.exists()


@pytest.mark.parametrize("memory_limit", [None, 2 * 1024**3])
def test_converter_process_timeout(tmp_path: Path, memory_limit: int | None) -> None:
    with pytest.raises(CadConversionError, match="время"):
        run_converter(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            tmp_path / "output.dxf",
            tmp_path / "process.log",
            ConversionPolicy(
                timeout_seconds=0.2, poll_seconds=0.02, max_memory_bytes=memory_limit
            ),
        )


def test_converter_process_output_budget(tmp_path: Path) -> None:
    output = tmp_path / "output.dxf"
    with pytest.raises(CadConversionError, match="объём"):
        run_converter(
            [
                sys.executable,
                "-c",
                "import sys; open(sys.argv[1], 'wb').write(b'x'*100)",
                str(output),
            ],
            output,
            tmp_path / "process.log",
            ConversionPolicy(max_output_bytes=50),
        )


def test_converter_process_memory_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.cad_import.legacy.process.ProcessTree.resident_bytes", lambda _: 1000
    )
    with pytest.raises(CadConversionMemoryExceeded, match="памяти") as failure:
        run_converter(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            tmp_path / "output.dxf",
            tmp_path / "process.log",
            ConversionPolicy(max_memory_bytes=500),
        )
    assert failure.value.limit_bytes == 500
    assert failure.value.peak_memory_bytes == 1000
    assert "1000" in str(failure.value)


def test_optional_memory_limit_retains_measurement_without_stopping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    measured_peak = 7 * 1024**3
    monkeypatch.setattr(
        "app.cad_import.legacy.process.ProcessTree.resident_bytes",
        lambda _: measured_peak,
    )
    output = tmp_path / "output.txt"
    result = run_converter(
        [
            sys.executable,
            "-c",
            "import sys,time; time.sleep(.1); open(sys.argv[1], 'w').write('ready')",
            str(output),
        ],
        output,
        tmp_path / "process.log",
        ConversionPolicy(max_memory_bytes=None, poll_seconds=0.02),
    )
    assert output.read_text() == "ready"
    assert result.exit_code == 0
    assert result.peak_memory_bytes == measured_peak


@pytest.mark.parametrize("memory_limit", [0, -1])
def test_configured_memory_limit_must_be_positive(memory_limit: int) -> None:
    with pytest.raises(ValueError, match="memory budget"):
        ConversionPolicy(max_memory_bytes=memory_limit)


def test_cancellation_still_stops_process_without_memory_limit(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"

    def cancel_after_start() -> None:
        if pid_file.exists():
            raise InterruptedError("Операция отменена")

    with pytest.raises(InterruptedError, match="отменена"):
        run_converter(
            [
                sys.executable,
                "-c",
                "import os,sys,time; open(sys.argv[1], 'w').write(str(os.getpid())); time.sleep(30)",
                str(pid_file),
            ],
            pid_file,
            tmp_path / "process.log",
            ConversionPolicy(max_memory_bytes=None, poll_seconds=0.02),
            check_cancelled=cancel_after_start,
        )
    assert not psutil.pid_exists(int(pid_file.read_text()))


def test_dxf_inspection_preserves_external_reference(tmp_path: Path) -> None:
    source = tmp_path / "reference.dxf"
    document = ezdxf.new("R2018")
    document.add_xref_def(filename="../utilities.dwg", name="engineering")
    document.modelspace().add_blockref("engineering", (10, 20))
    document.saveas(source)
    assert inspect_drawing(source).xrefs == {"engineering": "../utilities.dwg"}


def test_evidence_remains_immutable_after_another_conversion(tmp_path: Path) -> None:
    folder = tmp_path / "cache"
    folder.mkdir()
    inspection = DrawingInspection(
        dxf_version="AC1032", units=6, modelspace_entities={}, layer_names=[], xrefs={}
    )
    first = None
    for index, content in enumerate((b"first", b"second")):
        work = tmp_path / str(index)
        work.mkdir()
        output = work / "drawing.dxf"
        output.write_bytes(content)
        (work / "converter.log").write_bytes(b"")
        evidence = CadConversion(
            source_name="source.dwg",
            source_sha256="a" * 64,
            source_bytes=100,
            source_version="AC1032",
            converter=ConverterIdentity(
                name="libredwg", version="test", executable_sha256="b" * 64
            ),
            output_sha256=file_sha256(output),
            output_bytes=len(content),
            elapsed_seconds=index,
            exit_code=0,
            inspection=inspection,
        )
        result = publish(folder, work, evidence)
        if first is None:
            first = result
        else:
            assert first.path.read_bytes() == b"first"
            assert (
                CadConversion.model_validate_json(
                    first.evidence_path.read_text(encoding="utf-8")
                )
                == first.evidence
            )
            assert first.evidence_path != result.evidence_path


def test_memory_guard_counts_and_stops_python_descendants(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    child = "import time; allocation=bytearray(64*1024*1024); time.sleep(30)"
    parent = "import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c',sys.argv[1]]); open(sys.argv[2],'w').write(str(p.pid)); time.sleep(30)"
    with pytest.raises(CadConversionMemoryExceeded, match="памяти") as failure:
        run_converter(
            [sys.executable, "-c", parent, child, str(pid_file)],
            pid_file,
            tmp_path / "process.log",
            ConversionPolicy(
                timeout_seconds=5, max_memory_bytes=64 * 1024 * 1024, poll_seconds=0.02
            ),
        )
    assert failure.value.limit_bytes == 64 * 1024 * 1024
    assert failure.value.peak_memory_bytes > failure.value.limit_bytes
    assert pid_file.exists(), (
        "Лимит учитывает память дочернего процесса, а не только запускающего процесса"
    )
    pid = int(pid_file.read_text())
    assert (
        not psutil.pid_exists(pid)
        or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    )
