from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from shapely.geometry import LineString, Polygon, mapping, shape


ROOT = Path(__file__).resolve().parents[2]


def module(name: str, file_name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts/cad-lab" / file_name)
    loaded = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(loaded)
    return loaded


manifest_module = module("world_manifest", "build_world_source_manifest.py")
packet_module = module("world_packet", "compile_world_base_packet.py")


class WorldSourceFusionTests(unittest.TestCase):
    def test_conflicting_external_road_is_detected_in_local_frame(self):
        with tempfile.TemporaryDirectory() as temporary:
            surfaces = Path(temporary) / "surfaces.geojson"
            surfaces.write_text(json.dumps({
                "type": "FeatureCollection",
                "features": [{
                    "type": "Feature",
                    "geometry": mapping(Polygon([(100, 200), (110, 200), (110, 210), (100, 210)])),
                    "properties": {"class": "lawn"},
                }],
            }))
            context = {"features": [{
                "type": "Feature",
                "geometry": mapping(LineString([(-5, 5), (15, 5)])),
                "properties": {
                    "id": "osm:way:test", "kind": "transportation",
                    "highway": "service", "name": None,
                },
            }]}
            conflicts = manifest_module.detect_transport_conflicts(
                surfaces, context, [100, 200, 0]
            )
            self.assertEqual(len(conflicts), 1)
            self.assertAlmostEqual(conflicts[0]["centerline_inside_exact_lawn_m"], 10.0)

    def test_external_world_is_clipped_by_features_not_dxf_rectangle(self):
        alignment = {
            "projection_before_fit": {
                "reference_lon_lat": [37.0, 55.0], "earth_radius_m": 6378137.0,
            },
            "similarity_transform_row_vector": {
                "scale": 1.0,
                "rotation": [[1.0, 0.0], [0.0, 1.0]],
                "translation": [0.0, 0.0],
            },
        }
        frame = packet_module.Frame(alignment, [0.0, 0.0, 0.0])
        lon_left, lat = frame.source_to_wgs84(-10.0, 5.0)
        lon_right, _ = frame.source_to_wgs84(20.0, 5.0)
        authority = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "building.geojson").write_text(json.dumps({
                "type": "FeatureCollection", "features": [],
            }))
            (directory / "segment.geojson").write_text(json.dumps({
                "type": "FeatureCollection",
                "features": [{
                    "type": "Feature", "id": "segment-test",
                    "geometry": mapping(LineString([(lon_left, lat), (lon_right, lat)])),
                    "properties": {
                        "subtype": "road", "class": "service", "sources": [],
                    },
                }],
            }))
            features, receipt = packet_module.normalize_overture(
                directory, frame, authority, Polygon([(-50, -50), (50, -50), (50, 50), (-50, 50)])
            )
        self.assertEqual(receipt["residual_authority_intersections"], 0)
        self.assertGreater(receipt["suppressed_inside_DXF_authority_m"], 9.9)
        self.assertEqual(len(features), 1)
        remaining = shape(features[0]["geometry"])
        self.assertGreater(remaining.length, 19.0)
        self.assertTrue(remaining.geom_type in {"MultiLineString", "GeometryCollection"})

    def test_overture_inventory_never_treats_floor_count_as_explicit_height(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            empty = {"type": "FeatureCollection", "features": []}
            for feature_type in manifest_module.OVERTURE_TYPES:
                (directory / f"{feature_type}.geojson").write_text(json.dumps(empty))
            (directory / "building.geojson").write_text(json.dumps({
                "type": "FeatureCollection",
                "features": [
                    {"type": "Feature", "geometry": mapping(Polygon([(0, 0), (1, 0), (1, 1), (0, 0)])),
                     "properties": {"height": 12.5, "sources": []}},
                    {"type": "Feature", "geometry": mapping(Polygon([(2, 0), (3, 0), (3, 1), (2, 0)])),
                     "properties": {"num_floors": 5, "sources": []}},
                    {"type": "Feature", "geometry": mapping(Polygon([(4, 0), (5, 0), (5, 1), (4, 0)])),
                     "properties": {"sources": []}},
                ],
            }))
            audited = manifest_module.audit_overture(directory)
        self.assertEqual(audited["building_quality"]["explicit_height"], 1)
        self.assertEqual(audited["building_quality"]["num_floors"], 1)
        self.assertEqual(audited["building_quality"]["height_or_floors"], 2)
        self.assertAlmostEqual(audited["building_quality"]["vertical_coverage_ratio"], 2 / 3)


if __name__ == "__main__":
    unittest.main()
