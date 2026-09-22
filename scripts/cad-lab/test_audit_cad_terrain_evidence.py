from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import tempfile
import unittest

import ezdxf


MODULE_PATH = Path(__file__).with_name("audit_cad_terrain_evidence.py")
SPEC = importlib.util.spec_from_file_location("audit_cad_terrain_evidence", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class CadTerrainEvidenceTests(unittest.TestCase):
    def write(self, document) -> Path:
        directory = Path(tempfile.mkdtemp())
        path = directory / "source.dxf"
        document.saveas(path)
        self.addCleanup(shutil.rmtree, directory, True)
        return path

    def test_numeric_height_label_does_not_pass_terrain_gate(self):
        document = ezdxf.new("R2018")
        model = document.modelspace()
        model.add_lwpolyline([(0, 0), (10, 0), (10, 10)], dxfattribs={"elevation": 0})
        model.add_text("154.23", dxfattribs={"insert": (1, 1, 0)})
        result = MODULE.audit_dxf(self.write(document))
        self.assertEqual(
            result["terrain_assessment"]["status"],
            "planar_geometry_with_numeric_annotations_not_a_terrain_surface",
        )
        self.assertFalse(result["terrain_assessment"]["terrain_gate_passed"])

    def test_3d_face_is_reported_but_not_silently_admitted(self):
        document = ezdxf.new("R2018")
        document.modelspace().add_3dface([(0, 0, 10), (1, 0, 11), (0, 1, 12)])
        result = MODULE.audit_dxf(self.write(document))
        self.assertEqual(
            result["terrain_assessment"]["status"],
            "explicit_terrain_or_mesh_entity_present_requires_topology_review",
        )
        self.assertGreater(result["modelspace"]["coordinate_z"]["nonzero_samples"], 0)
        self.assertFalse(result["terrain_assessment"]["terrain_gate_passed"])


if __name__ == "__main__":
    unittest.main()
