"""Malformed physical objects must not disappear into apparently free ground."""

import pytest

from app.dxf_import.layers.contracts import Layer, LayerKind
from app.geometry.checking.adapters import ShapelyGeometryEngine
from app.geometry.contracts import GeometrySnapshot
from app.projects.contracts import Project
from app.shared.error_messages import public_failure_message
from app.shared.failures import DomainFailure, FailureReason

PHYSICAL_ROLES = [role for role in LayerKind if role != LayerKind.IGNORE]


def project_with_geometry(
    kind: LayerKind, geometry: dict[str, object] | None, *, context: bool = False
) -> Project:
    return Project(
        name="Untrusted normalized input",
        layers=[
            Layer(
                id="physical",
                source_name="physical",
                suggested_kind=kind,
                mapped_kind=kind,
                object_count=1,
                color="#777777",
            )
        ],
        source_geometry=GeometrySnapshot(
            feature_collection={
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "id": "damaged-object",
                        "geometry": geometry,
                        "properties": {
                            "source_layer": "physical",
                            "source_context_only": context,
                        },
                    }
                ],
            }
        ),
    )


@pytest.mark.parametrize("kind", PHYSICAL_ROLES)
@pytest.mark.parametrize(
    "geometry",
    [
        None,
        {"type": "NotAGeometry", "coordinates": []},
        {"type": "Polygon", "coordinates": []},
        {"type": "Point", "coordinates": [float("nan"), 2]},
        {"type": "Polygon", "coordinates": [[[0, 0], [2, 2], [0, 2], [2, 0], [0, 0]]]},
    ],
)
def test_missing_empty_or_invalid_physical_geometry_is_not_silently_skipped(
    kind: LayerKind, geometry: dict[str, object] | None
) -> None:
    project = project_with_geometry(kind, geometry)
    with pytest.raises(DomainFailure) as failure:
        ShapelyGeometryEngine().calculate(project)
    assert failure.value.reason is FailureReason.GEOMETRY_INVALID_LAYERS
    assert failure.value.params == {"layer_names": ["physical"]}
    assert "physical" in public_failure_message(failure.value)
    assert project.geometry is None
    assert project.source_geometry is not None
    assert not project.source_geometry.feature_collection["features"][0][
        "properties"
    ].get("source_invalid_geometry")


def test_explicitly_ignored_context_does_not_block_calculation() -> None:
    project = project_with_geometry(LayerKind.IGNORE, None)
    result = ShapelyGeometryEngine().calculate(project)
    assert result.feature_collection["features"][0]["id"] == "damaged-object"
    assert result.allowed_area_m2 is None


def test_annotation_is_not_a_missing_physical_contour() -> None:
    project = project_with_geometry(LayerKind.BUILDING, None, context=True)
    result = ShapelyGeometryEngine().calculate(project)
    assert result.feature_collection["features"][0]["properties"]["kind"] == "ignore"
