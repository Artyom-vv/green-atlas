from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

from shapely.geometry import LineString


MODULE_PATH = Path(__file__).with_name("build_source_curb_mesh.py")
SPEC = importlib.util.spec_from_file_location("build_source_curb_mesh", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class SourceCurbMeshTests(unittest.TestCase):
    def test_only_mutual_nearest_gap_inside_threshold_is_joined(self):
        parts = [
            LineString([(0, 0), (1, 0)]),
            LineString([(1.2, 0), (2, 0)]),
            LineString([(3, 0), (4, 0)]),
        ]
        joins = MODULE.within_insert_joins(parts, max_gap=0.55)
        self.assertEqual(len(joins), 1)
        self.assertAlmostEqual(joins[0].length, 0.2)

    def test_height_interpolation_is_bounded_by_controls(self):
        left = {"station_m": 10.0, "lower_z_m": 150.0}
        right = {"station_m": 20.0, "lower_z_m": 151.0}
        self.assertAlmostEqual(MODULE.interpolate(left, right, 15.0, "lower_z_m"), 150.5)
        with self.assertRaises(ValueError):
            MODULE.interpolate(left, right, 9.0, "lower_z_m")


if __name__ == "__main__":
    unittest.main()
