import json
from typing import Any
from unittest.mock import patch

import pytest

from app.geometry.contracts import DxfVerticalPrimitive, GeometrySnapshot
from app.releases.manifest_serialization import manifest_bytes


@pytest.mark.parametrize("with_geometry", [False, True])
def test_manifest_preserves_canonical_bytes_and_coordinates(
    with_geometry: bool,
) -> None:
    geometry = (
        GeometrySnapshot(
            feature_collection={
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"layer": "Газон", "unknown": None},
                        "geometry": {
                            "type": "Point",
                            "coordinates": [0.30000000000000004, -0.0],
                        },
                    }
                ],
                "bbox": [-0.0, 0, 1.0, 2.0],
            },
            calculation_scope="available_data",
            site_area_m2=12.5,
            vertical_primitives=[
                DxfVerticalPrimitive(
                    primitive_id="elevation-1",
                    primitive_type="point",
                    vertices_m=[[0.30000000000000004, -0.0, 3.5]],
                    source_layer="Высоты",
                    source_entity_type="POINT",
                    source_file_units="m",
                    unit_scale_to_m=1.0,
                    vertical_evidence="explicit_xyz",
                )
            ],
        )
        if with_geometry
        else None
    )
    metadata = {"schema": "green-atlas-release:3", "objects": ["посадка"]}
    expected = (
        json.dumps(
            {
                **metadata,
                "geometry": geometry.model_dump(mode="json") if geometry else None,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    assert manifest_bytes(metadata, geometry) == expected
    assert "geometry" not in metadata


def test_geometry_tree_is_not_copied_by_model_serialization() -> None:
    geometry = GeometrySnapshot(
        feature_collection={"type": "FeatureCollection", "features": []}
    )
    original = GeometrySnapshot.model_dump

    def bounded_dump(self: GeometrySnapshot, **kwargs: Any) -> dict[str, Any]:
        assert kwargs["exclude"] == {"feature_collection", "vertical_primitives"}
        return original(self, **kwargs)

    with patch.object(GeometrySnapshot, "model_dump", bounded_dump):
        assert (
            json.loads(manifest_bytes({}, geometry))["geometry"]["feature_collection"]
            == geometry.feature_collection
        )


def test_metadata_cannot_shadow_geometry() -> None:
    with pytest.raises(ValueError, match="отдельно"):
        manifest_bytes({"geometry": {}}, None)
