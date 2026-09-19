from __future__ import annotations

from hashlib import sha256

import pytest

from app.cad_bridge import CadSnapshotProvenance
from app.cad_intake.composition import ImportedDrawing, compose_dxf_imports
from app.dxf_import.capacity import SourceCapacityExceeded, SourceGeometryCapacity
from app.dxf_import.contracts import DxfImportResult
from app.dxf_import.layer_contracts import Layer, LayerKind
from app.geometry.contracts import CoordinateReference, GeometrySnapshot


def imported(
    path: str,
    *,
    crs: CoordinateReference | None = None,
    snapshot: bool = False,
) -> ImportedDrawing:
    content = f"DXF:{path}".encode()
    provenance = (
        CadSnapshotProvenance(
            schema="green-atlas.autocad-snapshot/1",
            source_sha256=sha256(content).hexdigest(),
            payload_sha256="b" * 64,
            autocad_version="2027.0.1",
            plugin_version="0.1.6",
            target="macos-arm64",
            source_instances=1,
            native_geometry=1,
            unresolved_instances=0,
        )
        if snapshot
        else None
    )
    return ImportedDrawing(
        path=path,
        source=content,
        imported=DxfImportResult(
            layers=[
                Layer(
                    id="same-layer-id",
                    source_name="BUILDINGS",
                    suggested_kind=LayerKind.BUILDING,
                    mapped_kind=LayerKind.BUILDING,
                    object_count=1,
                    bounds=(0, 0, 10, 10),
                    color="#000000",
                )
            ],
            geometry=GeometrySnapshot(
                feature_collection={
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "id": "dxf-0",
                            "properties": {
                                "source_layer": "BUILDINGS",
                                "source_handle": "A1",
                            },
                            "geometry": {
                                "type": "LineString",
                                "coordinates": [[0, 0], [10, 10]],
                            },
                        }
                    ],
                }
            ),
            dxf_version="AC1032",
            units="м",
            entity_count=1,
            bounds=[0, 0, 10, 10],
            warnings=["test warning"],
            coordinate_reference=crs or CoordinateReference(),
            cad_snapshot_provenance=provenance,
        ),
    )


def test_composes_same_named_layers_without_identity_collisions() -> None:
    left = imported("base/site.dxf", snapshot=True)
    right = imported("networks/site.dxf")

    composed = compose_dxf_imports([left, right])

    assert len({layer.id for layer in composed.imported.layers}) == 2
    assert [layer.source_name for layer in composed.imported.layers] == [
        "[base/site.dxf] BUILDINGS",
        "[networks/site.dxf] BUILDINGS",
    ]
    features = composed.imported.geometry.feature_collection["features"]
    assert len({feature["id"] for feature in features}) == 2
    assert features[0]["properties"]["source_layer_original_name"] == "BUILDINGS"
    assert features[0]["properties"]["source_drawing_path"] == "base/site.dxf"
    assert features[1]["properties"]["source_drawing_path"] == "networks/site.dxf"
    assert composed.imported.entity_count == 2
    assert composed.imported.bounds == [0, 0, 10, 10]
    assert composed.drawings[0].cad_snapshot is not None
    assert composed.drawings[1].cad_snapshot is None
    assert composed.imported.cad_snapshot_provenance is None


def test_rejects_ambiguous_coordinate_reference_composition() -> None:
    declared = CoordinateReference(
        status="declared",
        crs_id="EPSG:3857",
        source="user_declared",
        axis_order="xy",
    )
    with pytest.raises(ValueError, match="системами координат"):
        compose_dxf_imports([imported("known.dxf", crs=declared), imported("unknown.dxf")])


def test_rejects_duplicate_drawing_path() -> None:
    drawing = imported("same.dxf")
    with pytest.raises(ValueError, match="дважды"):
        compose_dxf_imports([drawing, drawing])


def test_applies_capacity_to_the_combined_geometry() -> None:
    with pytest.raises(SourceCapacityExceeded, match="объектов 2 > 1"):
        compose_dxf_imports(
            [imported("one.dxf"), imported("two.dxf")],
            capacity=SourceGeometryCapacity(
                max_features=1,
                max_coordinates=10,
                max_feature_coordinates=10,
            ),
        )
