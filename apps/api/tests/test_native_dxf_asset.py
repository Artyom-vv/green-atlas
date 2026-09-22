from collections.abc import Iterator
from hashlib import sha256
from io import BytesIO, StringIO
from pathlib import Path

import ezdxf
import pytest
from fastapi.testclient import TestClient

from app.composition import Runtime, create_runtime, get_application
from app.cad_bridge.contracts import CadSnapshotProvenance
from app.dxf_import.contracts import ImportMode
from app.dxf_import.encoding import (
    DECODE_VALIDATION_CHUNK_BYTES,
    DeclaredDxfEncoding,
    DxfTextEncoding,
    validate_text_encoding,
)
from app.dxf_import.native_application import BINARY_DXF_SIGNATURE
from app.main import app
from app.projects.contracts import Project
from app.projects.source_contracts import SourceContentInfo


@pytest.fixture
def runtime(tmp_path: Path) -> Iterator[Runtime]:
    value = create_runtime(tmp_path / "native.sqlite3")
    try:
        yield value
    finally:
        value.close()


@pytest.fixture
def client(runtime: Runtime, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setitem(
        app.dependency_overrides, get_application, lambda: runtime.application
    )
    with TestClient(app) as value:
        yield value


def native_source(
    runtime: Runtime, units: int = 6, *, binary: bool = False
) -> tuple[Project, bytes]:
    document = ezdxf.new("R2013")
    document.units = units
    document.modelspace().add_line((1000, 2000), (3000, 4000))
    if binary:
        stream = BytesIO()
        document.write(stream, fmt="bin")
        content = stream.getvalue()
    else:
        text = StringIO()
        document.write(text)
        content = text.getvalue().encode("utf-8")
    project = runtime.project_repository.create(Project(name="Native DXF"))
    return runtime.application.import_dxf(project.id, "исходник.dxf", content), content


def root(project: Project) -> str:
    return f"/api/projects/{project.id}/source-dxf"


@pytest.mark.parametrize(
    "units,scale,assumed",
    [(6, 1, False), (4, 0.001, False), (2, 0.3048, False), (0, 1, True)],
)
def test_units_and_bounds_match_actual_import(
    runtime: Runtime, client: TestClient, units: int, scale: float, assumed: bool
) -> None:
    project, content = native_source(runtime, units)
    response = client.get(root(project) + "/asset")
    assert response.status_code == 200
    asset = response.json()
    assert asset["unit_scale_to_m"] == scale
    assert asset["units_assumed"] == assumed
    assert asset["scale_basis"] == (
        "import_assumption" if assumed else "imported_units"
    )
    assert asset["bounds_m"] == pytest.approx(
        [1000 * scale, 2000 * scale, 3000 * scale, 4000 * scale]
    )
    assert asset["source_sha256"] == sha256(content).hexdigest()
    assert asset["source_bytes"] == len(content)
    assert asset["file_encoding"] == "utf-8"
    assert asset["scope"] == "uploaded_drawing"
    assert asset["format"] == "dxf_ascii"
    assert "calculation_ready" not in asset and "operation_id" not in asset
    downloaded = client.get(asset["file_url"])
    assert downloaded.content == content
    assert downloaded.headers["etag"] == f'"{asset["source_sha256"]}"'


def test_metadata_and_download_never_read_full_project(
    runtime: Runtime, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, content = native_source(runtime)
    repository = runtime.project_repository
    get = repository.get
    statements: list[str] = []
    repository._connection.set_trace_callback(statements.append)

    def projection_only(project_id: str, *, lightweight: bool = False) -> Project:
        assert lightweight, "Source delivery loaded the full geometry payload"
        return get(project_id, lightweight=True)

    monkeypatch.setattr(repository, "get", projection_only)
    response = client.get(root(project) + "/asset")
    assert response.status_code == 200
    metadata_queries = list(statements)
    assert any(
        "length(source)" in item and "substr(source" in item
        for item in metadata_queries
    )
    assert not any("SELECT source FROM" in item for item in metadata_queries)
    assert client.get(response.json()["file_url"]).content == content
    assert client.get(root(project) + "/download").content == content
    assert runtime.application.download_source(project.id) == content
    assert all("SELECT payload" not in item for item in statements)


def test_existing_autocad_project_opens_without_reimport(runtime: Runtime, client: TestClient) -> None:
    project, content = native_source(runtime)
    project.source_file.dxf_version = "AutoCAD 2027"
    project.source_file.cad_snapshot_provenance = CadSnapshotProvenance(
        schema_="green-atlas.autocad-snapshot/1",
        source_sha256=sha256(content).hexdigest(), payload_sha256="a" * 64,
        autocad_version="2027", plugin_version="0.1.28", target="macos-arm64",
        source_instances=1, native_geometry=1, unresolved_instances=0,
    )
    runtime.project_repository.save(project)
    response = client.get(root(project) + "/asset")
    assert response.status_code == 200
    assert client.get(response.json()["file_url"]).content == content
    # Compatibility is read-only and does not weaken the immutable byte check.
    runtime.project_repository.save_source(project.id, b"x" + content[1:])
    assert client.get(response.json()["file_url"]).status_code == 409


def test_actual_dxf_format_mismatch_is_still_rejected(runtime: Runtime, client: TestClient) -> None:
    project, _ = native_source(runtime)
    project.source_file.dxf_version = "AC1009"
    runtime.project_repository.save(project)
    assert client.get(root(project) + "/asset").status_code == 409


def test_stale_url_never_serves_replaced_source(
    runtime: Runtime, client: TestClient
) -> None:
    project, content = native_source(runtime)
    old_url = client.get(root(project) + "/asset").json()["file_url"]
    replacement = content + b"\n"
    runtime.application.import_dxf(project.id, "replacement.dxf", replacement)
    assert client.get(old_url).status_code == 409
    assert client.get(root(project) + "/download").content == replacement


@pytest.mark.parametrize("change", ["same_size_bytes", "size", "missing"])
def test_blob_mismatch_is_rejected_before_any_range_body(
    runtime: Runtime, client: TestClient, change: str
) -> None:
    project, content = native_source(runtime)
    url = client.get(root(project) + "/asset").json()["file_url"]
    broken = b"x" + content[1:] if change == "same_size_bytes" else content + b"\n"
    if change == "missing":
        with runtime.project_repository._connection:
            runtime.project_repository._connection.execute(
                "UPDATE projects SET source=NULL WHERE id=?", (project.id,)
            )
    else:
        runtime.project_repository.save_source(project.id, broken)
    response = client.get(url, headers={"Range": "bytes=0-21"})
    assert response.status_code == 409
    assert response.json()["code"] == "NATIVE_DXF_ASSET_UNAVAILABLE"
    if change in {"size", "missing"}:
        assert client.get(root(project) + "/asset").status_code == 409


def test_binary_download_remains_supported_but_is_not_native_gpu(
    runtime: Runtime, client: TestClient
) -> None:
    project, content = native_source(runtime, binary=True)
    assert content.startswith(BINARY_DXF_SIGNATURE)
    assert client.get(root(project) + "/asset").status_code == 409
    assert client.get(root(project) + "/download").content == content
    guarded = (
        root(project)
        + "/download?expected_source_sha256="
        + sha256(content).hexdigest()
    )
    assert client.get(guarded).status_code == 409


@pytest.mark.parametrize("change", ["units", "assumption", "hash", "mode"])
def test_unqualified_metadata_does_not_invent_scale_or_receipt(
    runtime: Runtime, client: TestClient, change: str
) -> None:
    project, _ = native_source(runtime)
    assert project.source_file is not None
    if change == "units":
        project.source_file.units = "unknown"
    elif change == "assumption":
        project.source_file.units_assumed = True
    elif change == "hash":
        project.source_file.content_sha256 = None
    else:
        project.import_status.mode = ImportMode.CAD_PREVIEW
    runtime.project_repository.save(project)
    assert client.get(root(project) + "/asset").status_code == 409


def test_missing_project_and_empty_source_are_explicit(
    runtime: Runtime, client: TestClient
) -> None:
    assert client.get("/api/projects/absent/source-dxf/asset").status_code == 404
    project = runtime.project_repository.create(Project(name="No source"))
    assert client.get(root(project) + "/asset").status_code == 409


@pytest.mark.parametrize(
    "requested,expected_status",
    [
        ("bytes=0-21", 206),
        ("bytes=-8", 206),
        ("bytes=12-", 206),
        ("bytes=999999-", 416),
        ("bytes=8-2", 416),
        ("bytes=-0", 416),
        ("bytes=0-1,4-5", 416),
        ("bytes=" + "9" * 5000 + "-", 416),
    ],
)
def test_range_exact_bytes_and_failures(
    runtime: Runtime, client: TestClient, requested: str, expected_status: int
) -> None:
    project, content = native_source(runtime)
    asset = client.get(root(project) + "/asset").json()
    response = client.get(asset["file_url"], headers={"Range": requested})
    assert response.status_code == expected_status
    if expected_status == 206:
        expected = (
            content[:22]
            if requested == "bytes=0-21"
            else content[-8:]
            if requested == "bytes=-8"
            else content[12:]
        )
        assert response.content == expected
        assert int(response.headers["content-length"]) == len(expected)
        assert response.headers["content-range"].endswith(f"/{len(content)}")
    else:
        assert response.content == b""
        assert response.headers["content-range"] == f"bytes */{len(content)}"
    assert response.headers["etag"] == f'"{asset["source_sha256"]}"'


def test_if_range_mismatch_returns_whole_verified_source(
    runtime: Runtime, client: TestClient
) -> None:
    project, content = native_source(runtime)
    url = client.get(root(project) + "/asset").json()["file_url"]
    response = client.get(url, headers={"Range": "bytes=0-21", "If-Range": '"stale"'})
    assert response.status_code == 200 and response.content == content


def test_lightweight_metadata_inspection_works_in_memory() -> None:
    from app.projects.adapters import InMemoryProjectRepository

    repository = InMemoryProjectRepository()
    project = repository.create(Project(name="Memory"))
    assert repository.get_source_info(project.id, prefix_bytes=3) is None
    repository.save_source(project.id, b"abcdef")
    assert repository.get_source_info(project.id, prefix_bytes=3) == SourceContentInfo(
        6, b"abc"
    )
    with pytest.raises(KeyError):
        repository.get_source_info("absent", prefix_bytes=3)


@pytest.mark.parametrize(
    "codec,label,annotation",
    [("cp1251", "windows-1251", "Берёза"), ("cp1252", "windows-1252", "Café")],
)
def test_legacy_declared_codec_preserves_bytes_and_labels(
    runtime: Runtime, client: TestClient, codec: str, label: str, annotation: str
) -> None:
    document = ezdxf.new("R2000")
    document.encoding = codec
    document.units = 6
    document.modelspace().add_text(annotation)
    stream = StringIO()
    document.write(stream)
    content = stream.getvalue().encode(codec)
    created = runtime.project_repository.create(Project(name="Legacy codec"))
    project = runtime.application.import_dxf(created.id, "legacy.dxf", content)
    assert project.source_geometry is not None
    assert any(
        feature["properties"].get("source_text") == annotation
        for feature in project.source_geometry.feature_collection["features"]
    )
    response = client.get(root(project) + "/asset")
    assert response.status_code == 200
    asset = response.json()
    assert asset["file_encoding"] == label
    assert client.get(asset["file_url"]).content == content


@pytest.mark.parametrize("change", ["missing", "unknown"])
def test_unresolved_legacy_codec_keeps_ordinary_download(
    runtime: Runtime, client: TestClient, change: str
) -> None:
    document = ezdxf.new("R2000")
    document.modelspace().add_line((0, 0), (10, 10))
    stream = StringIO()
    document.write(stream)
    text = (
        stream.getvalue()
        .replace(
            "$DWGCODEPAGE", "$UNKNOWN_PAGE" if change == "missing" else "$DWGCODEPAGE"
        )
        .replace("ANSI_1252", "UNSUPPORTED" if change == "unknown" else "ANSI_1252")
    )
    content = text.encode("ascii")
    created = runtime.project_repository.create(Project(name="Unresolved codec"))
    project = runtime.application.import_dxf(created.id, "legacy.dxf", content)
    assert client.get(root(project) + "/asset").status_code == 409
    assert client.get(root(project) + "/download").content == content


def test_declared_modern_utf8_with_fallback_bytes_is_rejected_for_gpu(
    runtime: Runtime, client: TestClient
) -> None:
    document = ezdxf.new("R2013")
    document.modelspace().add_text("Берёза")
    stream = StringIO()
    document.write(stream)
    # Old importer permits recovery after the declared UTF-8 decode fails.
    # Its success must not authorise the browser to silently replace characters.
    content = stream.getvalue().encode("cp1251")
    created = runtime.project_repository.create(Project(name="Recovered input"))
    project = runtime.application.import_dxf(created.id, "recovered.dxf", content)
    asset = client.get(root(project) + "/asset").json()
    assert asset["file_encoding"] == "utf-8"
    assert (
        client.get(asset["file_url"], headers={"Range": "bytes=0-21"}).status_code
        == 409
    )
    assert client.get(root(project) + "/download").content == content


def test_incremental_codec_validation_retains_multibyte_boundary_state() -> None:
    encoding = DeclaredDxfEncoding("utf-8-sig", DxfTextEncoding.UTF8)
    content = b"x" * (DECODE_VALIDATION_CHUNK_BYTES - 1) + "Ж".encode()
    validate_text_encoding(content, encoding)
    with pytest.raises(UnicodeDecodeError):
        validate_text_encoding(content[:-1], encoding)
