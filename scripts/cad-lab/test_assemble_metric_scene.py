"""Independent address alignment and terrain checks for metric scene assembly."""
from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest

from shapely.geometry import Polygon, mapping

sys.path.insert(0, str(Path(__file__).parent))
spec = importlib.util.spec_from_file_location("assemble_metric_scene", Path(__file__).with_name("assemble_metric_scene.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class MetricSceneTests(unittest.TestCase):
    @staticmethod
    def addressed_buildings():
        lon0, lat0 = 37.75, 55.62
        offsets = [(0, 0), (70, 0), (0, 70), (70, 70), (130, 40)]
        cad_features, map_features = [], []
        for number, (x, y) in enumerate(offsets, 1):
            lon = lon0 + math.degrees(x / (module.EARTH_RADIUS_M * math.cos(math.radians(lat0))))
            lat = lat0 + math.degrees(y / module.EARTH_RADIUS_M)
            mapped = Polygon([(lon - 0.00001, lat - 0.00001), (lon + 0.00001, lat - 0.00001),
                              (lon + 0.00001, lat + 0.00001), (lon - 0.00001, lat + 0.00001)])
            displaced = x + (300 if number == 5 else 0)
            cad = Polygon([(displaced + 1000 - 1, y + 2000 - 1), (displaced + 1000 + 1, y + 2000 - 1),
                           (displaced + 1000 + 1, y + 2000 + 1), (displaced + 1000 - 1, y + 2000 + 1)])
            cad_features.append({"geometry": mapping(cad), "properties": {"address": str(number)}})
            map_features.append({"geometry": mapping(mapped), "properties": {"addr:housenumber": str(number), "building:levels": "4"}})
        return cad_features, map_features

    def test_address_match_recovers_transform_and_rejects_wrong_address(self):
        lon0, lat0 = 37.75, 55.62
        cad_features, map_features = self.addressed_buildings()
        result = module.candidate_alignment({"features": cad_features}, {"features": map_features})
        self.assertEqual(result["inliers"], 4)
        self.assertLess(result["rmse_m"], 0.01)
        self.assertEqual(result["status"], "candidate_address_alignment_not_survey_control")
        px, py = module.map_to_cad((lon0, lat0), result)
        self.assertAlmostEqual(px, 1000, delta=0.1)
        self.assertAlmostEqual(py, 2000, delta=0.1)

    def test_generic_package_compiles_without_site_alignment(self):
        cad_buildings, map_buildings = self.addressed_buildings()
        surface = Polygon([(990, 1990), (1080, 1990), (1080, 2080), (990, 2080)])
        controls = [
            {"geometry": {"type": "Point", "coordinates": [x, y]}, "properties": {"z_m": 100 + 0.01 * x + 0.02 * y}}
            for x, y in [(990, 1990), (1080, 1990), (990, 2080), (1080, 2080),
                         (1020, 2010), (1060, 2060), (1040, 2040)]
        ]
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            for name, features in {"surfaces": [{"geometry": mapping(surface), "properties": {"class": "road", "source_handle": "A1"}}],
                                   "controls": controls, "cad_buildings": cad_buildings,
                                   "map_buildings": map_buildings}.items():
                (base / f"{name}.json").write_text(json.dumps({"type": "FeatureCollection", "features": features}))
            manifest = {"schema": "green-atlas.metric-scene-input.v1", "cad_origin": "autocad_capture_derived",
                        "vertical_datum": "test datum", "inputs": {
                            name: {"path": f"{filename}.json"} for name, filename in {
                                "cad_surfaces": "surfaces", "elevation_controls": "controls",
                                "cad_buildings": "cad_buildings", "map_buildings": "map_buildings"}.items()}}
            (base / "manifest.json").write_text(json.dumps(manifest))
            packet = module.compile_package(base / "manifest.json", base / "out")
            self.assertEqual(packet["alignment"]["inliers"], 4)
            self.assertEqual(packet["status"], "candidate_scene_requires_review")
            self.assertGreater(len(packet["surfaces"][0]["triangles"]), 0)
            self.assertEqual(packet["summary"]["buildings_without_height"], 4)
            self.assertTrue(all(row["alignment_status"] == "authored_cad_xy" for row in packet["buildings"]))
            self.assertTrue((base / "out/receipt.json").is_file())

    def test_height_from_levels_is_marked_estimate(self):
        self.assertEqual(module.building_height({"building:levels": "9"}),
                         (27.0, "estimated_from_floor_count_3m"))
        self.assertEqual(module.building_height({}), (None, "unknown"))


if __name__ == "__main__":
    unittest.main()
