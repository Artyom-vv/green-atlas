import importlib.util
import json
import unittest
from pathlib import Path

from shapely.geometry import shape


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "compile_render_scene_packet", ROOT / "scripts/cad-lab/compile_render_scene_packet.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


class RenderScenePacketTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        audit = ROOT / ".runtime/deterministic-render-audit-20260919"
        cls.packet = MODULE.compile_packet(
            audit / "source-contract.json",
            audit / "authored-surfaces.geojson",
            ROOT / ".runtime/terrain-control-20260918/mesh/terrain.json",
            ROOT / ".runtime/terrain-control-20260918/road-trial/terrain.json",
            ROOT / ".runtime/cad-vegetation-20260919/trees.json",
        )

    def test_area_partition_preserves_unknown(self):
        quality = self.packet["quality"]
        self.assertAlmostEqual(
            quality["authored_xy_area_m2"] + quality["unknown_xy_area_m2"],
            quality["scope_area_m2"],
            places=5,
        )
        self.assertAlmostEqual(
            shape(self.packet["geometry"]["unknown_xy_geometry"]).area,
            quality["unknown_xy_area_m2"],
            places=5,
        )

    def test_render_meshes_never_extrapolate_height(self):
        quality = self.packet["quality"]
        for row in quality["by_class"].values():
            self.assertLessEqual(row["renderable_estimated_z_area_m2"], row["authored_xy_area_m2"] + 1e-6)
            self.assertAlmostEqual(
                row["renderable_estimated_z_area_m2"] + row["xy_known_z_unknown_area_m2"],
                row["authored_xy_area_m2"],
                places=5,
            )
        self.assertGreater(quality["by_class"]["road"]["renderable_estimated_z_area_m2"], 500)
        self.assertLess(quality["by_class"]["sidewalk"]["renderable_estimated_z_area_m2"], 30)

    def test_vegetation_is_source_backed_and_disabled(self):
        candidates = self.packet["vegetation_candidates"]
        self.assertEqual(len(candidates), 7)
        self.assertEqual(sum(row["position_local"][2] is not None for row in candidates), 2)
        self.assertTrue(all(not row["render_enabled"] for row in candidates))
        self.assertTrue(all(row["source"]["handle"] for row in candidates))

    def test_external_context_and_unlicensed_assets_are_gated(self):
        self.assertFalse(self.packet["quality"]["external_osm_context_enabled"])
        botaniq = next(row for row in self.packet["asset_registry"] if row["id"] == "local.botaniq_6_8")
        self.assertEqual(botaniq["license"], "unverified")
        self.assertFalse(botaniq["render_enabled"])


if __name__ == "__main__":
    unittest.main()
