import json
from datetime import UTC, datetime
from io import StringIO

import ezdxf
import pytest
from pydantic import ValidationError
from shapely.geometry import LineString, box, mapping
from test_application_boundaries import HistoryReset, ImportReader

from app.contracts import GeometrySnapshot, Plan, Project
from app.dxf_import.application import ImportApplication
from app.dxf_import.contracts import SourceFile
from app.dxf_import.layers.contracts import Layer, LayerKind, LayerMapping
from app.exporting.contracts import ReleaseCreateRequest
from app.geometry.checking.adapters import ShapelyGeometryEngine
from app.geometry.networks.utility_contracts import (
    UtilityContext,
    UtilityGeometryReference,
    UtilityType,
)
from app.projects.adapters import SqliteProjectRepository
from app.releases.service import build_release, release_identity
from app.scene.contracts import SceneSnapshot
from app.shared.failures import DomainFailure, FailureReason


def confirmed_context() -> UtilityContext:
    return UtilityContext(
        network_type=UtilityType.GAS,
        geometry_reference=UtilityGeometryReference.AXIS,
        installation="underground",
        review_status="confirmed",
        source_reference="Survey page 1",
        confirmed_by="Reviewer",
    )


def layer(kind: LayerKind, context: UtilityContext | None = None) -> Layer:
    return Layer(
        id=kind,
        source_name=kind,
        suggested_kind=kind,
        mapped_kind=kind,
        object_count=1,
        color="#888888",
        utility_context=context,
    )


def mapped_project() -> Project:
    context = confirmed_context()
    features = [
        {
            "type": "Feature",
            "properties": {"kind": "site_border", "source_layer": "site_border"},
            "geometry": mapping(box(0, 0, 100, 100)),
        },
        {
            "type": "Feature",
            "properties": {
                "kind": "utility",
                "source_layer": "utility",
                "utility_context": context.model_dump(mode="json"),
            },
            "geometry": mapping(LineString([(10, 0), (10, 100)])),
        },
    ]
    geometry = GeometrySnapshot(
        feature_collection={"type": "FeatureCollection", "features": features}
    )
    return Project(
        name="Explicit networks",
        layers=[layer(LayerKind.SITE_BORDER), layer(LayerKind.UTILITY, context)],
        source_geometry=geometry,
        geometry=geometry.model_copy(deep=True),
        map_ready=True,
        geometry_version=3,
        plan=Plan(),
    )


def import_service(project: Project) -> tuple[ImportApplication, Project, list[str]]:
    repository = SqliteProjectRepository(":memory:")
    project = repository.create(project)
    invalidated: list[str] = []
    service = ImportApplication(
        repository=repository,
        dxf_reader=ImportReader(),
        history=HistoryReset([]),
        invalidate_spatial=invalidated.append,
        now=lambda: datetime(2026, 9, 15, tzinfo=UTC),
    )
    return service, project, invalidated


def test_context_defaults_unknown_and_confirmation_requires_evidence() -> None:
    assert UtilityContext().network_type == "unknown"
    assert UtilityContext().review_status == "unconfirmed"
    with pytest.raises(ValidationError, match="подтверждения"):
        UtilityContext(review_status="confirmed", network_type=UtilityType.GAS)
    with pytest.raises(ValidationError, match="слоя инженерных"):
        LayerMapping(
            layer_id="road", kind=LayerKind.ROAD, utility_context=confirmed_context()
        )


def test_old_mapping_client_preserves_context_without_invalidating_geometry() -> None:
    service, project, invalidated = import_service(mapped_project())
    saved = service.save_mappings(
        project.id,
        [LayerMapping(layer_id="utility", kind=LayerKind.UTILITY, visible=False)],
    )
    assert saved.layers[1].utility_context == confirmed_context()
    assert saved.map_ready
    assert saved.geometry_version == 3
    assert not saved.layers[1].visible
    assert invalidated == []


@pytest.mark.parametrize("mapped_kind", [None, LayerKind.IGNORE])
def test_partial_mapping_rejects_an_unassigned_required_saved_layer(
    mapped_kind: LayerKind | None,
) -> None:
    original = mapped_project()
    original.layers[1].required = True
    original.layers[1].mapped_kind = mapped_kind
    service, project, invalidated = import_service(original)
    before = project.model_dump_json()

    with pytest.raises(DomainFailure) as failure:
        service.save_mappings(
            project.id,
            [
                LayerMapping(
                    layer_id="site_border", kind=LayerKind.SITE_BORDER, visible=False
                )
            ],
        )
    assert failure.value.reason is FailureReason.REQUIRED_LAYERS_UNMAPPED
    assert failure.value.params == {"layer_names": ["utility"]}

    assert service.repository.get(project.id).model_dump_json() == before
    assert invalidated == []


def test_partial_mapping_cannot_ignore_an_assigned_required_layer() -> None:
    original = mapped_project()
    original.layers[1].required = True
    service, project, invalidated = import_service(original)
    before = project.model_dump_json()

    with pytest.raises(DomainFailure) as failure:
        service.save_mappings(
            project.id, [LayerMapping(layer_id="utility", kind=LayerKind.IGNORE)]
        )
    assert failure.value.reason is FailureReason.REQUIRED_LAYERS_UNMAPPED
    assert failure.value.params == {"layer_names": ["utility"]}

    assert service.repository.get(project.id).model_dump_json() == before
    assert invalidated == []


def test_review_required_mapping_can_be_saved_before_calculation() -> None:
    project = mapped_project()
    project.layers[1].mapping_review_required = True
    project.layers[1].mapping_confirmed = False
    service, project, _invalidated = import_service(project)

    pending = service.save_mappings(
        project.id,
        [LayerMapping(layer_id="utility", kind=LayerKind.UTILITY, confirmed=False)],
    )
    assert not pending.layers[1].mapping_confirmed

    saved = service.save_mappings(
        project.id,
        [
            LayerMapping(
                layer_id="utility",
                kind=LayerKind.UTILITY,
                confirmed=True,
            )
        ],
    )
    assert saved.layers[1].mapping_confirmed


@pytest.mark.parametrize("kind", [LayerKind.UTILITY, LayerKind.ROAD])
def test_clear_context_preserves_map_and_role_change_requires_preparation(
    kind: LayerKind,
) -> None:
    service, project, invalidated = import_service(mapped_project())
    saved = service.save_mappings(
        project.id, [LayerMapping(layer_id="utility", kind=kind, utility_context=None)]
    )
    assert saved.layers[1].utility_context is None
    assert (saved.geometry is None) == (kind == "road")
    assert saved.source_geometry is not None
    assert saved.map_ready == (kind == "utility")
    assert saved.geometry_version == (4 if kind == "road" else 3)
    assert invalidated == [project.id]


def test_network_meaning_change_preserves_map_and_rebuild_uses_current_context() -> (
    None
):
    original = mapped_project()
    service, project, invalidated = import_service(original)
    context = UtilityContext.model_validate(
        {**confirmed_context().model_dump(), "network_type": "water"}
    )
    saved = service.save_mappings(
        project.id,
        [
            LayerMapping(
                layer_id="utility", kind=LayerKind.UTILITY, utility_context=context
            )
        ],
    )
    assert saved.geometry_version == 3
    assert saved.geometry is not None and saved.map_ready
    assert saved.plan is not None
    assert saved.plan.validation_basis is None
    assert invalidated == [project.id]
    result = ShapelyGeometryEngine().calculate(saved)
    feature = next(
        item
        for item in result.feature_collection["features"]
        if item["properties"].get("source_layer") == "utility"
    )
    assert feature["properties"]["utility_context"]["network_type"] == "water"
    assert original.source_geometry is not None
    assert (
        original.source_geometry.feature_collection["features"][1]["properties"][
            "utility_context"
        ]["network_type"]
        == "gas"
    )


def test_unconfirmed_layer_name_never_inherits_raw_confirmed_context() -> None:
    project = mapped_project()
    project.layers[1].utility_context = None
    result = ShapelyGeometryEngine().calculate(project)
    feature = next(
        item
        for item in result.feature_collection["features"]
        if item["properties"].get("source_layer") == "utility"
    )
    assert "utility_context" not in feature["properties"]


@pytest.mark.parametrize("confirmed", [False, True])
def test_release_and_saved_project_preserve_only_explicit_layer_context(
    confirmed: bool,
) -> None:
    document = ezdxf.new("R2010")
    document.units = 6
    document.layers.new("site_border")
    document.layers.new("utility")
    document.modelspace().add_lwpolyline(
        [(0, 0), (100, 0), (100, 100), (0, 100)],
        close=True,
        dxfattribs={"layer": "site_border"},
    )
    document.modelspace().add_line((10, 0), (10, 100), dxfattribs={"layer": "utility"})
    stream = StringIO()
    document.write(stream)
    source = stream.getvalue().encode()
    project = mapped_project()
    if not confirmed:
        # The old derived snapshot is deliberately stale; it is not an authority.
        project.layers[1].utility_context = None
    project.source_file = SourceFile(
        name="source.dxf",
        size=len(source),
        imported_at="2026-09-15",
        dxf_version="AC1024",
        units="m",
        entity_count=2,
    )
    request = ReleaseCreateRequest(mode="draft")
    scene = SceneSnapshot(
        plan_version=1, horizon_year=0, coordinate_origin=[0, 0], note="Synthetic"
    )
    package, artifacts = build_release(
        project, request, release_identity(project, request), source, scene, source
    )
    manifest = json.loads(
        artifacts[
            next(item.id for item in package.artifacts if item.kind == "manifest")
        ]
    )
    mapping_record = next(
        item for item in manifest["layer_mappings"] if item["source_name"] == "utility"
    )
    assert mapping_record["utility_context"] == (
        confirmed_context().model_dump(mode="json") if confirmed else None
    )
    # Saved legacy state still resolves semantics from explicit layer context.
    project.geometry = ShapelyGeometryEngine().calculate(project)
    service, saved, _invalidated = import_service(
        Project.model_validate_json(project.model_dump_json())
    )
    restored = service.repository.get(saved.id)
    assert restored.geometry is not None
    utility = next(item for item in restored.layers if item.source_name == "utility")
    restored_properties = next(
        item["properties"]
        for item in restored.geometry.feature_collection["features"]
        if item["properties"].get("source_layer") == "utility"
    )
    assert utility.utility_context == (confirmed_context() if confirmed else None)
    if confirmed:
        assert restored_properties["utility_context"] == confirmed_context().model_dump(
            mode="json"
        )
    else:
        assert "utility_context" not in restored_properties
    assert restored.map_ready
