"""Minimal upstream ezdxf MULTILEADER failure; no original dataset required."""
import json
from pathlib import Path
import sys
import traceback

import ezdxf
from ezdxf.math import Vec2
from ezdxf.render.mleader import ConnectionSide

doc = ezdxf.new('R2018')
block = doc.blocks.new('WithAnnotation')
block.add_line((0, 0), (1, 0))
builder = block.add_multileader_mtext('Standard')
builder.set_content('Annotation', char_height=0.25)
builder.add_leader_line(ConnectionSide.left, [Vec2(1, 1), Vec2(2, 2)])
builder.build(insert=Vec2(4, 4))
leader = block.query('MULTILEADER')[0]
for data in leader.context.leaders:
    data.dogleg_length = 0
leader.dxf.has_dogleg = 0
block.add_line((0, 1), (1, 1))
insert = doc.modelspace().add_blockref(block.name, (0, 0))
report = dict(ezdxf_version=ezdxf.__version__, expected_entities=3,
              scope='SDK identity transform with disabled zero-length dogleg')
try:
    report.update(status='completed', entities=len(list(insert.virtual_entities())))
except Exception:
    report.update(status='failed', error=traceback.format_exc())
Path(sys.argv[1]).write_text(json.dumps(report,indent=2)+'\n',encoding='utf8')
print(json.dumps(report))
