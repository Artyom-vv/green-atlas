from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

import ezdxf


MODULE_PATH = Path(__file__).with_name("extract_topographic_elevation_controls.py")
SPEC = importlib.util.spec_from_file_location("extract_topographic_elevation_controls", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class ElevationControlTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.directory, True)

    def source(self) -> Path:
        document = ezdxf.new("R2018")
        block = document.blocks.new("PIKET")
        block.add_arc((0, 0), 0.2, 0, 270, dxfattribs={"layer": "Горизонтали"})
        model = document.modelspace()
        model.add_blockref("PIKET", (0, 0), dxfattribs={"layer": "Горизонтали"})
        for index, x in enumerate((10, 20), start=1):
            name = f"PIKET_{index}"
            current = document.blocks.new(name)
            current.add_arc((0, 0), 0.2, 0, 270, dxfattribs={"layer": "Горизонтали"})
            model.add_blockref(name, (x, 0), dxfattribs={"layer": "Горизонтали"})
        for text, insert in (
            ("150.20", (0.25, 0.25)), ("150.00", (0.25, -0.75)),
            ("150.40", (10.25, 0.25)),
            ("150.70", (20.25, 0.25)), ("150.55", (20.25, -0.75)),
        ):
            model.add_text(text, dxfattribs={
                "insert": insert, "height": 0.25, "layer": "Горизонтали"
            })
        path = self.directory / "topography.dxf"
        document.saveas(path)
        return path

    def surfaces(self) -> Path:
        path = self.directory / "surfaces.geojson"
        path.write_text(json.dumps({
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[-2,-2],[22,-2],[22,0],[ -2,0],[-2,-2]]]}, "properties": {"class": "road"}},
                {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[-2,0],[22,0],[22,2],[-2,2],[-2,0]]]}, "properties": {"class": "sidewalk"}},
            ],
        }))
        return path

    def test_unique_geometry_extracts_single_and_curb_pairs(self):
        packet = MODULE.extract(self.source(), self.surfaces())
        self.assertEqual(packet["quality"]["markers"], 3)
        self.assertEqual(packet["quality"]["numeric_labels"], 5)
        self.assertEqual(packet["quality"]["accepted_label_associations"], 5)
        self.assertEqual(sum(row["kind"] == "single_spot_elevation" for row in packet["controls"]), 1)
        pairs = [row for row in packet["controls"] if row["kind"] == "curb_pair_elevation"]
        self.assertEqual(len(pairs), 4)
        self.assertEqual({row["curb_role"] for row in pairs}, {"upper", "lower"})

    def test_distant_label_is_not_associated(self):
        source = self.source()
        document = ezdxf.readfile(source)
        document.modelspace().add_text("151.00", dxfattribs={
            "insert": (100, 100), "height": 0.25, "layer": "Горизонтали"
        })
        document.saveas(source)
        packet = MODULE.extract(source, self.surfaces())
        distant = next(row for row in packet["label_associations"] if row["text"] == "151.00")
        self.assertEqual(distant["association_status"], "rejected_ambiguous_or_distant")


if __name__ == "__main__":
    unittest.main()
