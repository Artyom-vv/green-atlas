from datetime import UTC, datetime
from io import BytesIO, StringIO

import ezdxf
import pytest
from fastapi.testclient import TestClient

from app.dxf_import.adapters import EzdxfReader
from app.dxf_import.application import ImportApplication
from app.dxf_import.contracts import ImportEditability, ImportMode
from app.dxf_import.preview_contracts import CadPreviewProvenance
from app.dxf_import.preview_marker import PREVIEW_RECORD_KEY, write_preview_marker
from app.geometry.contracts import GeometrySnapshot
from app.history.adapters import InMemoryProjectHistory
from app.planning.contracts import Plan
from app.planting_zones.contracts import PlantingZoneAssignment
from app.projects.adapters import InMemoryProjectRepository
from app.projects.contracts import Project
from app.releases.service import ParsedReleaseBundle


def provenance() -> CadPreviewProvenance:
    return CadPreviewProvenance(
        original_sha256="a" * 64,
        converted_sha256="b" * 64,
        boundary_original_sha256="a" * 64,
        boundary_handle="14651AD",
        boundary_bounds_m=(0, 0, 10, 10),
        omitted_entity_count=4,
    )


def drawing_content(*, binary: bool = True, malformed: str | None = None) -> bytes:
    document = ezdxf.new("R2013")
    document.units = ezdxf.units.M
    document.modelspace().add_line((-100, -100), (100, 100))
    if malformed is not None:
        document.rootdict.add_xrecord(PREVIEW_RECORD_KEY).reset([(1, malformed)])
    else:
        write_preview_marker(document, provenance())
    if binary:
        stream = BytesIO()
        document.write(stream, fmt="bin")
        return stream.getvalue()
    text = StringIO()
    document.write(text)
    return text.getvalue().encode("utf-8")


def importer() -> tuple[ImportApplication, InMemoryProjectRepository, str]:
    repository = InMemoryProjectRepository()
    project = repository.create(Project(name="Preview"))
    return (
        ImportApplication(
            repository=repository,
            dxf_reader=EzdxfReader(),
            history=InMemoryProjectHistory(),
            invalidate_spatial=lambda _: None,
            now=lambda: datetime.now(UTC),
        ),
        repository,
        project.id,
    )


@pytest.mark.parametrize("binary", [True, False])
def test_unwrapped_dxf_retains_preview_provenance_and_read_only_gate(
    binary: bool,
) -> None:
    application, repository, project_id = importer()
    source = drawing_content(binary=binary)
    project = application.import_dxf(project_id, "territory.dxf", source)
    assert project.import_status.mode == ImportMode.CAD_PREVIEW
    assert project.import_status.editability == ImportEditability.READ_ONLY
    assert project.source_file is not None
    assert project.source_file.preview_provenance == provenance()
    assert project.source_file.bounds == [0, 0, 10, 10]
    assert project.import_status.message in project.source_file.warnings
    assert any("описанием: 4" in warning for warning in project.source_file.warnings)
    assert not any("назначьте корректный слой" in warning for warning in project.source_file.warnings)
    assert repository.get_source(project_id) == source
    assert project.source_geometry is not None
    assert len(project.source_geometry.feature_collection["features"]) == 1
    assert project.plan is None and not project.map_ready


@pytest.mark.parametrize(
    "payload", ["{}", "not-json", '{"status":"complete"}', "x" * 4097]
)
def test_corrupt_declaration_cannot_silently_become_full_source(payload: str) -> None:
    application, repository, project_id = importer()
    before = repository.get(project_id)
    with pytest.raises(ValueError, match="декларация"):
        application.import_dxf(
            project_id, "preview.dxf", drawing_content(malformed=payload)
        )
    assert repository.get(project_id) == before


def test_bundle_editability_cannot_override_derived_dxf_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, _, project_id = importer()
    parsed = ParsedReleaseBundle(
        manifest={"project": {"name": "Claimed editable"}},
        source_filename="derived.dxf",
        source_content=drawing_content(),
        dxf_content=None,
        plan=Plan(objects=[]),
        geometry=GeometrySnapshot(
            feature_collection={"type": "FeatureCollection", "features": []},
            site_area_m2=100,
            planning_area_m2=80,
            allowed_area_m2=50,
        ),
        planting_zones=[
            PlantingZoneAssignment(
                id="foreign-zone",
                label="Calculated elsewhere",
                geometry={
                    "type": "Polygon",
                    "coordinates": [[[0, 0], [5, 0], [5, 5], [0, 0]]],
                },
            )
        ],
        editable=True,
    )
    monkeypatch.setattr(
        "app.dxf_import.application.parse_release_bundle", lambda _: parsed
    )
    project = application.import_release_bundle(project_id, "bundle.zip", b"bundle")
    assert project.import_status.mode == ImportMode.CAD_PREVIEW
    assert project.import_status.editability == ImportEditability.READ_ONLY
    assert project.plan is None and project.geometry is None and not project.map_ready
    assert project.source_geometry is not None
    assert project.planting_zones == []
    assert project.site_area_m2 is None
    assert project.planning_area_m2 is None
    assert project.allowed_area_m2 is None


def test_http_preview_can_be_viewed_but_not_calculated_or_released() -> None:
    from app.main import app

    with TestClient(app) as client:
        created = client.post("/api/projects", json={"name": "Derived preview"}).json()
        root = f"/api/projects/{created['id']}"
        imported = client.post(
            root + "/source-dxf", files={"file": ("preview.dxf", drawing_content())}
        )
        assert imported.status_code == 200, imported.text
        assert client.get(root).json()["source_file"][
            "preview_provenance"
        ] == provenance().model_dump(mode="json")
        assert client.get(root + "/source-dxf/download").status_code == 200
        for path in ("/operations/geometry", "/plan/manual"):
            response = client.post(root + path)
            assert response.status_code == 400, response.text
            assert "Предварительная карта" in response.json()["message"]
        release = client.post(root + "/releases", json={"mode": "draft"})
        assert release.status_code == 400, release.text
