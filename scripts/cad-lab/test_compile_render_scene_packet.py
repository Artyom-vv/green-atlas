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
            ROOT / ".runtime/terrain-control-20260920/class-constrained/terrain.json",
            ROOT / ".runtime/terrain-control-20260918/road-trial/terrain.json",
            ROOT / ".runtime/cad-vegetation-20260919/trees.json",
            audit / "street-objects.geojson",
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
        self.assertGreater(quality["by_class"]["sidewalk"]["renderable_estimated_z_area_m2"], 30)
        self.assertLess(quality["by_class"]["sidewalk"]["renderable_estimated_z_area_m2"], 50)

    def test_vegetation_is_source_backed_and_disabled(self):
        candidates = self.packet["vegetation_candidates"]
        self.assertEqual(len(candidates), 7)
        self.assertEqual(sum(row["position_local"][2] is not None for row in candidates), 1)
        self.assertEqual(sum(row["render_enabled"] for row in candidates), 1)
        enabled = next(row for row in candidates if row["render_enabled"])
        self.assertEqual(enabled["id"], "tree:1098")
        self.assertEqual(enabled["asset_id"], "blenderkit.ahorn_tree.1k")
        self.assertTrue(all(row["source"]["handle"] for row in candidates))

    def test_external_context_and_unlicensed_assets_are_gated(self):
        self.assertFalse(self.packet["quality"]["external_osm_context_enabled"])
        botaniq = next(row for row in self.packet["asset_registry"] if row["id"] == "local.botaniq_6_8")
        self.assertEqual(botaniq["license"], "unverified")
        self.assertFalse(botaniq["render_enabled"])
        ahorn = next(row for row in self.packet["asset_registry"] if row["id"] == "blenderkit.ahorn_tree.1k")
        self.assertEqual(ahorn["license"], "CC0")
        self.assertTrue(ahorn["render_enabled"])

    def test_street_objects_keep_source_points_but_need_assets(self):
        candidates = self.packet["street_object_candidates"]
        self.assertEqual(len(candidates), 22)
        self.assertEqual(sum(row["semantic_class"] == "utility_well" for row in candidates), 16)
        self.assertEqual(sum(row["semantic_class"] == "street_light" for row in candidates), 4)
        self.assertEqual(sum(row["semantic_class"] == "traffic_light" for row in candidates), 2)
        self.assertTrue(all(row["source"]["source_handle"] for row in candidates))
        self.assertTrue(all(not row["render_enabled"] for row in candidates))
        self.assertTrue(all(row["asset_gate"] == "blocked_no_verified_physical_3d_asset" for row in candidates))

    def test_pbr_material_sources_are_hash_gated(self):
        materials = self.packet["material_registry"]
        self.assertEqual(set(materials), {"road", "sidewalk"})
        self.assertTrue(all(row["license"] == "CC0" for row in materials.values()))
        self.assertTrue(all(row["render_enabled"] for row in materials.values()))
        self.assertTrue(all(source["valid"] for row in materials.values() for source in row["maps"].values()))


if __name__ == "__main__":
    unittest.main()
