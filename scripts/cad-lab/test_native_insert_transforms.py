"""Positive and adversarial checks for native placement evidence."""
import math
from pathlib import Path
import tempfile
import unittest

import ezdxf

from verify_native_insert_transforms import verify_transforms


class NativeTransformTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.receipt = Path(self.temp.name) / "transforms.tsv"
        self.doc = ezdxf.new()
        leaf = self.doc.blocks.new("leaf", base_point=(2, 3, 0))
        leaf.add_line((0, 0), (1, 0))
        parent = self.doc.blocks.new("parent")
        child = parent.add_blockref("leaf", (5, 6), dxfattribs={"rotation": 30})
        root = self.doc.modelspace().add_blockref("parent", (100, 200))
        self.entities = [root, child]
        self.placements = [{"block": "leaf", "chain": [
            {"handle": e.dxf.handle, "block": e.dxf.name} for e in self.entities],
            "matrix": list(child.matrix44() @ root.matrix44())}]
        self.rows = []
        for e in self.entities:
            values = [*e.dxf.insert, e.dxf.xscale, e.dxf.yscale, e.dxf.zscale,
                      math.radians(e.dxf.rotation), *e.dxf.extrusion,
                      *self.doc.blocks[e.dxf.name].block.dxf.base_point]
            self.rows.append([e.dxf.handle, *map(str, values)])
        self.write()

    def write(self):
        self.receipt.write_text("\n".join("\t".join(r) for r in self.rows))

    def check(self):
        return verify_transforms(self.doc, self.placements, self.receipt)

    def test_nested_nonzero_base_rotation(self):
        self.assertEqual(self.check()["native_insert_count"], 2)

    def test_missing_handle(self):
        self.rows.pop()
        self.write()
        with self.assertRaisesRegex(ValueError, "coverage"):
            self.check()

    def test_duplicate_handle(self):
        self.rows.append(self.rows[0])
        self.write()
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            self.check()

    def test_native_value_mismatch(self):
        self.rows[0][1] = "101"
        self.write()
        with self.assertRaisesRegex(ValueError, "differs from source"):
            self.check()

    def test_nonfinite_receipt(self):
        self.rows[0][1] = "nan"
        self.write()
        with self.assertRaisesRegex(ValueError, "Nonfinite"):
            self.check()

    def test_tampered_matrix(self):
        self.placements[0]["matrix"][12] += 1
        with self.assertRaisesRegex(ValueError, "matrix differs"):
            self.check()

    def test_nonfinite_matrix(self):
        self.placements[0]["matrix"][12] = float("nan")
        with self.assertRaisesRegex(ValueError, "Invalid inventory matrix"):
            self.check()

    def test_wrong_chain_order(self):
        self.placements[0]["chain"].reverse()
        with self.assertRaisesRegex(ValueError, "ownership"):
            self.check()

    def test_wrong_target(self):
        self.placements[0]["block"] = "parent"
        with self.assertRaisesRegex(ValueError, "target"):
            self.check()

    def test_minsert_not_silently_admitted(self):
        self.entities[0].dxf.column_count = 2
        self.entities[0].dxf.column_spacing = 10
        with self.assertRaisesRegex(ValueError, "MINSERT"):
            self.check()


if __name__ == "__main__":
    unittest.main()
