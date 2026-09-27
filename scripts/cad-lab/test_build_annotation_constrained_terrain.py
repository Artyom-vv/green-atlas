from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest


MODULE_PATH = Path(__file__).with_name("build_annotation_constrained_terrain.py")
SPEC = importlib.util.spec_from_file_location("build_annotation_constrained_terrain", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class AnnotationTerrainTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.directory, True)

    def inputs(self, steep: bool = False) -> tuple[Path, Path]:
        surfaces = self.directory / "surfaces.geojson"
        surfaces.write_text(json.dumps({
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [[[0,0],[10,0],[10,10],[0,10],[0,0]]]},
                "properties": {"class": "road"},
            }],
        }))
        controls = []
        for index, (x, y, z) in enumerate(((1,1,100), (9,1,100), (1,9,120 if steep else 101))):
            controls.append({
                "id": f"c{index}", "xy": [x,y], "z_m": z,
                "kind": "single_spot_elevation", "semantic_class": "road",
                "marker_handle": f"m{index}", "labels": [{"label_handle": f"l{index}"}],
                "vertical_status": "test",
            })
        packet = self.directory / "controls.json"
        packet.write_text(json.dumps({
            "surface_source": {"sha256": MODULE.sha256(surfaces)},
            "controls": controls,
        }))
        return surfaces, packet

    def test_builds_only_inside_class_and_support_hull(self):
        surfaces, controls = self.inputs()
        terrain = MODULE.build(surfaces, controls)
        self.assertGreater(terrain["coverage"]["terrain_area_m2"], 0)
        self.assertLess(terrain["coverage"]["terrain_area_m2"], 100)
        self.assertGreater(len(terrain["triangles"]), 0)
        self.assertTrue(all(row == "road" for row in terrain["triangle_classes"]))

    def test_rejects_unphysical_support_slope(self):
        surfaces, controls = self.inputs(steep=True)
        terrain = MODULE.build(surfaces, controls)
        self.assertEqual(terrain["coverage"]["terrain_area_m2"], 0)
        self.assertEqual(
            terrain["coverage"]["by_class"]["road"]["rejected"]["support_triangle_too_steep"], 1
        )


if __name__ == "__main__":
    unittest.main()
