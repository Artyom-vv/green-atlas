"""The preparation comparison must ignore defaults, not changed geometry."""
import io
import unittest

import ezdxf

from prepare_native_planar_dxf import graphic_fingerprints, replace_boundary


class PlanarFingerprintTests(unittest.TestCase):
    def test_replacement_preserves_hatch_links_through_file_roundtrip(self):
        doc = ezdxf.new("R2013")
        owner = doc.blocks.new("B")
        region = owner.add_region(dxfattribs={"color": 3})
        handle = region.dxf.handle
        coords = [(0, 0), (2, 0), (2, 2), (0, 2), (0, 0)]
        hatch = owner.add_hatch()
        path = hatch.paths.add_polyline_path(coords[:-1], is_closed=True)
        hatch.associate(path, [region])
        before = graphic_fingerprints(doc, {handle})
        replacement = replace_boundary(doc, owner, region, coords)
        self.assertEqual(replacement.dxf.handle, handle)
        self.assertEqual(replacement.dxf.color, 3)
        self.assertEqual(before, graphic_fingerprints(doc, {handle}))
        stream = io.StringIO()
        doc.write(stream)
        stream.seek(0)
        reopened = ezdxf.read(stream)
        self.assertEqual(reopened.entitydb[handle].dxftype(), "LWPOLYLINE")
        self.assertEqual(reopened.entitydb[handle].get_reactors(), [hatch.dxf.handle])
        self.assertEqual(reopened.entitydb[hatch.dxf.handle].paths[0].source_boundary_objects, [handle])

    def test_sdk_roundtrip_with_explicit_defaults_and_multiple_sequences(self):
        doc = ezdxf.new()
        doc.blocks.new("B").add_line((0, 0), (1, 0))
        for x in range(3):
            doc.modelspace().add_blockref("B", (x, 0), dxfattribs={"xscale": 1})
        doc.modelspace().add_lwpolyline([(0, 0), (1, 2)], dxfattribs={"const_width": 0})
        before = graphic_fingerprints(doc, set())
        stream = io.StringIO()
        doc.write(stream)
        stream.seek(0)
        self.assertEqual(before, graphic_fingerprints(ezdxf.read(stream), set()))

    def test_changed_geometry_is_not_normalized_away(self):
        doc = ezdxf.new()
        line = doc.modelspace().add_line((0, 0), (1, 0))
        before = graphic_fingerprints(doc, set())
        line.dxf.end = (2, 0)
        self.assertNotEqual(before, graphic_fingerprints(doc, set()))

    def test_changed_nondefault_scale_is_not_normalized_away(self):
        doc = ezdxf.new()
        doc.blocks.new("B")
        insert = doc.modelspace().add_blockref("B", (0, 0))
        before = graphic_fingerprints(doc, set())
        insert.dxf.xscale = 2
        self.assertNotEqual(before, graphic_fingerprints(doc, set()))

    def test_native_sequence_layout_owner_and_sdk_parent_are_equivalent(self):
        doc = ezdxf.new()
        doc.blocks.new("B")
        insert = doc.modelspace().add_blockref("B", (0, 0))
        insert.seqend.dxf.owner = insert.dxf.owner
        before = graphic_fingerprints(doc, set())
        insert.seqend.dxf.owner = insert.dxf.handle
        self.assertEqual(before, graphic_fingerprints(doc, set()))
        insert.seqend.dxf.owner = "INVALID"
        with self.assertRaisesRegex(ValueError, "Unexpected SEQEND owner"):
            graphic_fingerprints(doc, set())


if __name__ == "__main__":
    unittest.main()
