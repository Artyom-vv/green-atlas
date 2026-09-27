import importlib.util
import tempfile
import unittest
from pathlib import Path

import ezdxf

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "probe_aoi_candidate", ROOT / "scripts/cad-lab/probe_aoi_candidate.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


class AoiCandidateProbeTest(unittest.TestCase):
    def test_extracts_only_boundary_context_and_checks_reader(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source" / "drawing.dxf"
            source.parent.mkdir()
            document = ezdxf.new("R2018")
            document.units = 6
            boundary = document.modelspace().add_lwpolyline(
                [(0, 0), (20, 0), (20, 20), (0, 20)], close=True
            )
            inside = document.modelspace().add_line((2, 2), (18, 18))
            outside = document.modelspace().add_line((200, 200), (210, 210))
            document.saveas(source)

            report = MODULE.probe_source(
                source, boundary.dxf.handle, root / "workpackages"
            )

            self.assertEqual(report["ordinary_reader"]["status"], "readable")
            self.assertFalse(report["prepared"]["calculation_ready"])
            self.assertGreaterEqual(report["prepared"]["selected_entities"], 2)
            self.assertGreaterEqual(report["prepared"]["excluded_by_bounds"], 1)
            prepared = ezdxf.readfile(report["prepared"]["drawing_path"])
            handles = {
                value.dxf.get("handle") for value in prepared.modelspace()
            }
            self.assertTrue(handles)
            self.assertNotIn(outside.dxf.handle, handles)
            self.assertIsNotNone(inside.dxf.handle)


if __name__ == "__main__":
    unittest.main()
