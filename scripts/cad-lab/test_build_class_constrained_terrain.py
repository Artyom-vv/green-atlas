import importlib.util
import unittest
from pathlib import Path

from shapely.geometry import Polygon, shape
from shapely.ops import unary_union
import json


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "build_class_constrained_terrain", ROOT / "scripts/cad-lab/build_class_constrained_terrain.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


class ClassConstrainedTerrainTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.surfaces_path = ROOT / ".runtime/deterministic-render-audit-20260919/authored-surfaces.geojson"
        cls.mesh = MODULE.build(
            cls.surfaces_path,
            ROOT / ".runtime/terrain-control-20260918/mesh/terrain.json",
            ROOT / ".runtime/terrain-control-20260918/road-trial/terrain.json",
            ROOT / ".runtime/curb-corridor-audit-20260919/curb-corridor.json",
        )
        source = json.loads(cls.surfaces_path.read_text())
        cls.class_geometry = {
            key: unary_union([shape(row["geometry"]) for row in source["features"]
                              if row["properties"]["class"] == key])
            for key in ("sidewalk", "lawn")
        }

    def test_every_face_stays_inside_its_exact_surface_class(self):
        for face, semantic_class in zip(self.mesh["triangles"], self.mesh["triangle_classes"]):
            triangle = Polygon([self.mesh["vertices"][index]["xyz"][:2] for index in face])
            self.assertTrue(self.class_geometry[semantic_class].buffer(1e-7).covers(triangle))

    def test_no_curb_height_extrapolation(self):
        for semantic_class in ("sidewalk", "lawn"):
            for support in self.mesh["supports"][semantic_class]:
                if support["kind"] != "curb_top_between_reviewed_pairs":
                    continue
                low, high = support["control_station_range_m"]
                self.assertGreaterEqual(support["station_m"] + 1e-8, low)
                self.assertLessEqual(support["station_m"] - 1e-8, high)

    def test_both_classes_gain_measurable_coverage(self):
        self.assertGreater(self.mesh["coverage_by_class"]["sidewalk"]["area_xy_m2"], 25)
        self.assertGreater(self.mesh["coverage_by_class"]["lawn"]["area_xy_m2"], 100)
        self.assertEqual(len(self.mesh["triangles"]), len(self.mesh["triangle_classes"]))

    def test_status_and_limitations_remain_explicit(self):
        self.assertIn("not_survey_confirmed", self.mesh["status"])
        self.assertTrue(any("No height is extrapolated" in row for row in self.mesh["limitations"]))

    def test_trial_surface_has_no_extreme_slope_artifacts(self):
        self.assertLess(self.mesh["slope_by_class"]["sidewalk"]["max_ratio"], 0.10)
        self.assertLess(self.mesh["slope_by_class"]["lawn"]["max_ratio"], 0.10)


if __name__ == "__main__":
    unittest.main()
