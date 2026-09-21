"""Failure controls for the native comparison harness; no AutoCAD required."""
import json
from pathlib import Path
import tempfile
import unittest

import ezdxf

from verify_native_region_boundaries import verify


class NativeBoundaryVerificationTests(unittest.TestCase):
    def fixture(self, directory: Path, *, gap=False, area=100.0, extra=False):
        (directory / "manifest.json").write_text(json.dumps({
            "source_sha256": "a" * 64,
            "rows": [{"target_layer": "SOURCE_A"}],
        }))
        (directory / "native-measurements.tsv").write_text(
            f"layer\tarea\tperimeter\nSOURCE_A\t{area}\t40\n")
        doc = ezdxf.new()
        doc.layers.new("SOURCE_A")
        points = [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]
        for a, b in list(zip(points, points[1:]))[:3 if gap else 4]:
            doc.modelspace().add_line(a, b, dxfattribs={"layer": "SOURCE_A"})
        if extra:
            doc.modelspace().add_line((20, 0), (21, 0), dxfattribs={"layer": "SOURCE_A"})
        doc.saveas(directory / "native-boundaries.dxf")

    def test_complete_square_matches_native_control(self):
        with tempfile.TemporaryDirectory() as path:
            directory = Path(path)
            self.fixture(directory)
            result = verify(directory, 1e-6, include_geometry=True)
            self.assertEqual(result["counts"], {"local_comparison_pass": 1})
            self.assertEqual(result["rows"][0]["geometry"]["type"], "Polygon")

    def test_open_boundary_is_not_admitted(self):
        with tempfile.TemporaryDirectory() as path:
            directory = Path(path)
            self.fixture(directory, gap=True)
            result = verify(directory, 1e-6, include_geometry=True)
            self.assertEqual(result["counts"], {"review": 1})
            self.assertNotIn("geometry", result["rows"][0])

    def test_wrong_native_area_is_not_admitted(self):
        with tempfile.TemporaryDirectory() as path:
            directory = Path(path)
            self.fixture(directory, area=90)
            self.assertEqual(verify(directory, 1e-6)["counts"], {"review": 1})

    def test_stray_segment_cannot_hide_behind_valid_polygon(self):
        with tempfile.TemporaryDirectory() as path:
            directory = Path(path)
            self.fixture(directory, extra=True)
            self.assertEqual(verify(directory, 1e-6)["counts"], {"review": 1})

    def test_duplicate_measurements_rejected(self):
        with tempfile.TemporaryDirectory() as path:
            directory = Path(path)
            self.fixture(directory)
            report = directory / "native-measurements.tsv"
            report.write_text(report.read_text() + "SOURCE_A\t100\t40\n")
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                verify(directory, 1e-6)

    def test_missing_manifest_coverage_rejected(self):
        with tempfile.TemporaryDirectory() as path:
            directory = Path(path)
            self.fixture(directory)
            (directory / "manifest.json").write_text(json.dumps({
                "source_sha256": "a" * 64, "rows": [{"target_layer": "SOURCE_B"}],
            }))
            with self.assertRaisesRegex(ValueError, "coverage"):
                verify(directory, 1e-6)

    def test_infinite_perimeter_cannot_expand_tolerance(self):
        with tempfile.TemporaryDirectory() as path:
            directory = Path(path)
            self.fixture(directory)
            (directory / "native-measurements.tsv").write_text(
                "layer\tarea\tperimeter\nSOURCE_A\t100\tinf\n")
            with self.assertRaisesRegex(ValueError, "finite"):
                verify(directory, 1e-6)


if __name__ == "__main__":
    unittest.main()
