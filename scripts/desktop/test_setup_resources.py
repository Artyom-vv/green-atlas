"""Регрессия отсутствующих данных самого Setup и сверка архивных байтов."""

from pathlib import Path

import pytest
from setup_resources import resources, verify_resources


class Archive:
    def __init__(self, values: dict[str, bytes]) -> None:
        self.values = values
        self.toc: dict[str, object] = dict.fromkeys(values)

    def extract(self, name: str) -> bytes:
        return self.values[name]


def repository(root: Path, names: tuple[object, ...] = ("cad_bridge/probe_diagnostics.json",)) -> Path:
    app = root / "apps/api/app"
    (app / "distribution").mkdir(parents=True)
    (app / "distribution/preflight.py").write_text("RUNTIME_RESOURCES = " + repr(names), encoding="utf-8")
    (app / "cad_bridge").mkdir()
    (app / "cad_bridge/probe_diagnostics.json").write_bytes(b'{"synthetic":true}')
    return root


@pytest.mark.parametrize("separator", ["/", "\\"])
def test_exact_archive_bytes_for_both_platform_names(tmp_path: Path, separator: str) -> None:
    root = repository(tmp_path)
    archive = Archive({"app/cad_bridge/probe_diagnostics.json".replace("/", separator): b'{"synthetic":true}'})
    verified = verify_resources(root, archive)
    assert len(verified) == 1
    assert verified["app/cad_bridge/probe_diagnostics.json"]["bytes"] == 18


def test_missing_raw_resource_in_setup_rejected(tmp_path: Path) -> None:
    root = repository(tmp_path)
    # Данные внутри вложенного API не заменяют данные проверяющего Setup.
    archive = Archive({"payload/GreenAtlas.bundle/Runtime/_internal/app/cad_bridge/probe_diagnostics.json": b'{"synthetic":true}'})
    with pytest.raises(ValueError, match="отсутствует ресурс"):
        verify_resources(root, archive)


def test_changed_raw_resource_in_setup_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="отличается от исходника"):
        verify_resources(repository(tmp_path), Archive({"app/cad_bridge/probe_diagnostics.json": b"changed"}))


def test_ambiguous_archive_names_rejected(tmp_path: Path) -> None:
    archive = Archive({"app/cad_bridge/probe_diagnostics.json": b"a", "app\\cad_bridge\\probe_diagnostics.json": b"a"})
    with pytest.raises(ValueError, match="Повторён путь"):
        verify_resources(repository(tmp_path), archive)


@pytest.mark.parametrize("names", [
    ("../escape.json",), ("/absolute.json",), ("cad_bridge//probe_diagnostics.json",),
    ("cad_bridge\\probe_diagnostics.json",), ("C:/escape.json",),
    ("cad_bridge/probe_diagnostics.json", "cad_bridge/probe_diagnostics.json"),
])
def test_invalid_registry_rejected(tmp_path: Path, names: tuple[str, ...]) -> None:
    with pytest.raises(ValueError, match="Неканонический или повторённый"):
        resources(repository(tmp_path, names))


def test_missing_source_rejected(tmp_path: Path) -> None:
    root = repository(tmp_path, ("cad_bridge/missing.json",))
    with pytest.raises(FileNotFoundError):
        resources(root)


def test_resource_symlink_rejected(tmp_path: Path) -> None:
    root = repository(tmp_path)
    source = root / "apps/api/app/cad_bridge/probe_diagnostics.json"
    external = root / "external.json"
    external.write_bytes(source.read_bytes())
    source.unlink()
    source.symlink_to(external)
    with pytest.raises(ValueError, match="содержит ссылку"):
        resources(root)


def test_production_registry_covers_all_required_data() -> None:
    root = Path(__file__).resolve().parents[2]
    entries = resources(root)
    assert len(entries) == 14
    assert "app/cad_bridge/probe_diagnostics.json" in entries
    assert "app/rendering/blender_worker.py" in entries
