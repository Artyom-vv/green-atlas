from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "landxml", ROOT / "scripts/cad-lab/import_landxml_terrain.py"
)
landxml = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(landxml)


SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<LandXML xmlns="http://www.landxml.org/schema/LandXML-1.2">
  <Surfaces>
    <Surface name="Existing ground">
      <Definition surfType="TIN">
        <Pnts>
          <P id="1">1000 2000 150.0</P>
          <P id="2">1000 2010 150.2</P>
          <P id="3">1010 2010 150.4</P>
          <P id="4">1010 2000 150.1</P>
        </Pnts>
        <Faces><F>1 2 3</F><F>1 3 4</F></Faces>
        <Breaklines><Breakline><PntList3D>1000 2000 150.0 1000 2010 150.2</PntList3D></Breakline></Breaklines>
      </Definition>
    </Surface>
  </Surfaces>
</LandXML>
"""


class LandXmlTerrainTests(unittest.TestCase):
    def test_import_preserves_tin_ids_and_converts_axis_order(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "terrain.xml"
            path.write_text(SAMPLE)
            packet = landxml.import_landxml(
                path, "EPSG:9999", "Moscow height system verified",
                "northing-easting-elevation",
            )
        self.assertEqual(packet["status"], "admitted_engineering_tin")
        self.assertEqual(packet["surface"]["point_ids"], ["1", "2", "3", "4"])
        self.assertEqual(packet["surface"]["vertices"][0], [2000.0, 1000.0, 150.0])
        self.assertEqual(packet["surface"]["triangles"], [[0, 1, 2], [0, 2, 3]])
        self.assertEqual(packet["quality"]["breaklines"], 1)
        self.assertAlmostEqual(packet["surface"]["triangle_area_sum_xy_m2"], 100.0)

    def test_unknown_coordinate_reference_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "terrain.xml"
            path.write_text(SAMPLE)
            with self.assertRaisesRegex(ValueError, "horizontal CRS"):
                landxml.import_landxml(
                    path, "unknown", "Moscow height system",
                    "northing-easting-elevation",
                )

    def test_missing_face_point_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "terrain.xml"
            path.write_text(SAMPLE.replace("<F>1 2 3</F>", "<F>1 2 99</F>"))
            with self.assertRaisesRegex(ValueError, "missing points"):
                landxml.import_landxml(
                    path, "EPSG:9999", "Moscow height system",
                    "northing-easting-elevation",
                )


if __name__ == "__main__":
    unittest.main()
