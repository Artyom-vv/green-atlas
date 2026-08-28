"""Generate the small, valid DXF used by demos and end-to-end tests."""

from pathlib import Path

import ezdxf


target = Path(__file__).with_name("site.dxf")
document = ezdxf.new("R2013", setup=True)
document.units = ezdxf.units.M
for name, color in (
    ("SITE_BORDER", 3),
    ("BUILDING", 8),
    ("ROAD", 9),
    ("UTIL_WATER", 5),
    ("UTIL_HEAT", 30),
    ("GREEN_EXISTING", 94),
):
    document.layers.add(name, color=color)

modelspace = document.modelspace()
modelspace.add_lwpolyline([(0, 0), (120, 0), (120, 90), (0, 90)], close=True, dxfattribs={"layer": "SITE_BORDER"})
modelspace.add_lwpolyline([(14, 58), (40, 58), (40, 81), (14, 81)], close=True, dxfattribs={"layer": "BUILDING"})
modelspace.add_lwpolyline([(84, 55), (107, 55), (107, 78), (84, 78)], close=True, dxfattribs={"layer": "BUILDING"})
modelspace.add_lwpolyline([(0, 7), (120, 7)], dxfattribs={"layer": "ROAD"})
modelspace.add_lwpolyline([(7, 0), (7, 90)], dxfattribs={"layer": "ROAD"})
modelspace.add_line((-5, 42), (125, 42), dxfattribs={"layer": "UTIL_WATER"})
modelspace.add_line((75, -5), (75, 95), dxfattribs={"layer": "UTIL_HEAT"})
for center, radius in (((51, 68), 2.8), ((60, 72), 3.2), ((112, 26), 2.4), ((26, 28), 3.0), ((47, 22), 2.5), ((94, 30), 2.7)):
    modelspace.add_circle(center, radius, dxfattribs={"layer": "GREEN_EXISTING"})
document.saveas(target)
print(target)
