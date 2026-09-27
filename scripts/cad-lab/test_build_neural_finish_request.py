from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


MODULE_PATH = Path(__file__).with_name("build_neural_finish_request.py")
SPEC = importlib.util.spec_from_file_location("build_neural_finish_request", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class NeuralFinishRequestTest(unittest.TestCase):
    def test_default_scenario_protects_hard_surfaces_and_does_not_invent_age(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            world = root / "world.json"
            world.write_text(json.dumps({
                "inventory_vegetation": [{
                    "inventory_id": 10, "diameter_cm": 28.0,
                }],
            }))
            scene = root / "scene.json"
            scene_payload = {
                "scene_id": "test",
                "source": {"world": str(world), "world_sha256": MODULE.sha256(world)},
                "vegetation": [{
                    "inventory_id": 10, "source_species": "Липа", "asset_species": "Tilia-europaea",
                    "height_m": 13.0, "condition_description": "раскидистая крона",
                    "position_status": "matched_marker_on_exact_project_lawn",
                }],
                "buildings": [{"class": "retail", "height_status": "render_estimate_from_floor_count"}],
                "audit": {
                    "buildings_skipped": {"unknown_height": 2},
                    "surface_stats": {
                        "road": {"area_m2": 100.0}, "sidewalk": {"area_m2": 20.0}, "lawn": {"area_m2": 30.0},
                    },
                },
            }
            scene.write_text(json.dumps(scene_payload))
            render = root / "render.png"; render.write_bytes(b"render")
            semantic = root / "semantic.png"; semantic.write_bytes(b"semantic")
            request = MODULE.build_request(scene, render, semantic, "facade_vegetation", [])
            self.assertEqual(request["facts"]["vegetation"]["records"][0]["age_years"], None)
            self.assertIn("road", request["scenario"]["protected_classes"])
            self.assertIn("lawn", request["scenario"]["protected_classes"])
            self.assertEqual(request["facts"]["vehicles"]["count"], 0)
            self.assertTrue(request["facts"]["buildings"]["completeness_gate"]["small_buildings_required"])
            self.assertIn("Do not redraw their texture", request["prompt"])

    def test_supervised_scenario_is_explicitly_human_reviewed(self) -> None:
        self.assertTrue(MODULE.SCENARIOS["supervised_full_finish"]["human_review_required"])
        self.assertFalse(MODULE.SCENARIOS["facade_vegetation"]["human_review_required"])

    def test_production_scenario_rejects_external_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            reference = Path(temporary) / "chat-reference.png"
            reference.write_bytes(b"not relevant; rejection precedes reading")
            with self.assertRaisesRegex(ValueError, "reject external appearance references"):
                MODULE.build_request(
                    Path("scene.json"), Path("render.png"), Path("semantic.png"),
                    "facade_vegetation", [reference],
                )


if __name__ == "__main__":
    unittest.main()
