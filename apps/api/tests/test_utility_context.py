from datetime import UTC, datetime
from io import StringIO

import ezdxf
import pytest
from pydantic import ValidationError
from shapely.geometry import LineString, box, mapping
from test_application_boundaries import HistoryReset, ImportReader

from app.contracts import GeometrySnapshot, Plan, Project
from app.dxf_import.adapters import EzdxfReader
from app.dxf_import.application import ImportApplication
from app.dxf_import.contracts import SourceFile
from app.dxf_import.layer_contracts import Layer, LayerKind, LayerMapping
from app.exporting.contracts import ReleaseCreateRequest
from app.geometry.adapters import ShapelyGeometryEngine
from app.geometry.utility_contracts import UtilityContext
from app.projects.adapters import InMemoryProjectRepository
from app.releases.service import build_release, release_identity
from app.scene.contracts import SceneSnapshot


def confirmed_context():
    return UtilityContext(
        network_type="gas",
        geometry_reference="axis",
        installation="underground",
        review_status="confirmed",
        source_reference="Survey page 1",
        confirmed_by="Reviewer",
    )


def layer(kind, context=None):
    return Layer(
        id=kind,
        source_name=kind,
        suggested_kind=kind,
        mapped_kind=kind,
        object_count=1,
        color="#888888",
        utility_context=context,
    )


def mapped_project():
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


def import_service(project):
    repository = InMemoryProjectRepository()
    project = repository.create(project)
    invalidated = []
    service = ImportApplication(
        repository=repository,
        dxf_reader=ImportReader(),
        history=HistoryReset([]),
        invalidate_spatial=invalidated.append,
        now=lambda: datetime(2026, 9, 15, tzinfo=UTC),
    )
    return service, project, invalidated


def test_context_defaults_unknown_and_confirmation_requires_evidence():
    assert UtilityContext().network_type == "unknown"
    assert UtilityContext().review_status == "unconfirmed"
    with pytest.raises(ValidationError, match="подтверждения"):
        UtilityContext(review_status="confirmed", network_type="gas")
    with pytest.raises(ValidationError, match="слоя инженерных"):
        LayerMapping(layer_id="road", kind="road", utility_context=confirmed_context())


def test_old_mapping_client_preserves_context_without_invalidating_geometry():
    service, project, invalidated = import_service(mapped_project())
    saved = service.save_mappings(
        project.id, [LayerMapping(layer_id="utility", kind="utility", visible=False)]
    )
    assert saved.layers[1].utility_context == confirmed_context()
    assert saved.map_ready
    assert saved.geometry_version == 3
    assert not saved.layers[1].visible
    assert invalidated == []


def test_review_required_mapping_must_be_explicitly_confirmed():
    project = mapped_project()
    project.layers[1].mapping_review_required = True
    project.layers[1].mapping_confirmed = False
    service, project, _invalidated = import_service(project)

    with pytest.raises(ValueError, match="Подтвердите предложенные роли"):
        service.save_mappings(
            project.id,
            [
                LayerMapping(
                    layer_id="utility",
                    kind="utility",
                    confirmed=False,
                )
            ],
        )

    saved = service.save_mappings(
        project.id,
        [
            LayerMapping(
                layer_id="utility",
                kind="utility",
                confirmed=True,
            )
        ],
    )
    assert saved.layers[1].mapping_confirmed


@pytest.mark.parametrize("kind", ["utility", "road"])
def test_explicit_clear_or_semantic_remap_invalidates_derived_geometry(kind):
    service, project, invalidated = import_service(mapped_project())
    saved = service.save_mappings(
        project.id, [LayerMapping(layer_id="utility", kind=kind, utility_context=None)]
    )
    assert saved.layers[1].utility_context is None
    assert saved.geometry is None
    assert saved.source_geometry is not None
    assert not saved.map_ready
    assert saved.geometry_version == 4
    assert invalidated == [project.id]


def test_metadata_only_change_invalidates_and_rebuild_uses_mapping_not_raw_metadata():
    original = mapped_project()
    service, project, invalidated = import_service(original)
    context = UtilityContext.model_validate(
        {**confirmed_context().model_dump(), "network_type": "water"}
    )
    saved = service.save_mappings(
        project.id,
        [LayerMapping(layer_id="utility", kind="utility", utility_context=context)],
    )
    assert saved.geometry_version == 4
    assert invalidated == [project.id]
    result = ShapelyGeometryEngine().calculate(saved)
    feature = next(
        item
        for item in result.feature_collection["features"]
        if item["properties"].get("source_layer") == "utility"
    )
    assert feature["properties"]["utility_context"]["network_type"] == "water"
    assert (
        original.source_geometry.feature_collection["features"][1]["properties"][
            "utility_context"
        ]["network_type"]
        == "gas"
    )


def test_unconfirmed_layer_name_never_inherits_raw_confirmed_context():
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
def test_release_round_trip_preserves_only_explicit_layer_context(confirmed):
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
    bundle = artifacts[
        next(item.id for item in package.artifacts if item.kind == "bundle")
    ]
    service, target, _invalidated = import_service(Project(name="Restored"))
    service.dxf_reader = EzdxfReader()
    restored = service.import_release_bundle(target.id, "release.zip", bundle)
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
