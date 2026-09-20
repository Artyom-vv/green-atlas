import asyncio
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import UploadFile

from app.cad_intake.config import CadIntakeConfig
from app.cad_intake.paths import CadDiscovery
from app.cad_intake.uploads import store_uploaded_package


def upload(name: str, content: bytes) -> UploadFile:
    return UploadFile(file=BytesIO(content), filename=name)


def test_uploaded_package_becomes_an_immutable_discovery_root(tmp_path: Path) -> None:
    config = CadIntakeConfig((), tmp_path / "storage", None)
    package = asyncio.run(
        store_uploaded_package(
            config,
            [
                upload("genplan.dxf", b"first"),
                upload("genplan.dxf.green-atlas.snapshot.json", b"native-1"),
                upload("geobase.dxf", b"second"),
                upload("geobase.dxf.green-atlas.snapshot.json", b"native-2"),
            ],
        )
    )

    assert package.total_bytes == 27
    assert [entry.path for entry in package.entries] == [
        "genplan.dxf",
        "geobase.dxf",
    ]
    assert [entry.path for entry in package.snapshots] == [
        "genplan.dxf.green-atlas.snapshot.json",
        "geobase.dxf.green-atlas.snapshot.json",
    ]
    config.require_enabled(package.root_id)
    directory = CadDiscovery(config).directory(package.root_id)
    assert [entry.name for entry in directory.entries] == [
        "genplan.dxf",
        "geobase.dxf",
    ]
    assert (config.root(package.root_id).path / "upload.json").is_file()
    assert not CadDiscovery(config).roots()


def test_upload_rejects_paths_duplicates_and_total_overflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = CadIntakeConfig((), tmp_path / "storage", None)
    with pytest.raises(ValueError, match="вложенных путей"):
        asyncio.run(store_uploaded_package(config, [upload("folder/site.dxf", b"x")]))
    with pytest.raises(ValueError, match="уникальны"):
        asyncio.run(
            store_uploaded_package(
                config,
                [upload("site.dxf", b"x"), upload("SITE.DXF", b"y")],
            )
        )

    with pytest.raises(ValueError, match="нужен точный AutoCAD snapshot"):
        asyncio.run(store_uploaded_package(config, [upload("site.dxf", b"x")]))

    monkeypatch.setattr("app.cad_intake.uploads.MAX_CAD_UPLOAD_TOTAL_BYTES", 5)
    with pytest.raises(ValueError, match="не больше 2 ГБ"):
        asyncio.run(
            store_uploaded_package(
                config,
                [
                    upload("site.dxf", b"four"),
                    upload("site.dxf.green-atlas.snapshot.json", b"two"),
                ],
            )
        )
    uploads = config.storage / "uploads"
    assert not uploads.exists() or not list(uploads.iterdir())
