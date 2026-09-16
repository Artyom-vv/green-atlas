"""Control: identical ROI entities wrapped in one identity INSERT."""
import io
import json
from pathlib import Path
import sys
import time
import ezdxf
from ezdxf import xref

ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT/'.runtime/cad-roi-20260916'
sys.path.insert(0, str(ROOT/'apps/api'))
from app.dxf_import.adapters import EzdxfReader

began = time.monotonic()
source = ezdxf.readfile(LAB/'large-block-roi-100m.dxf')
target = ezdxf.new(source.dxfversion)
target.units = source.units
block = target.blocks.new('ROI_WRAPPED')
loader = xref.Loader(source, target)
loader.load_modelspace(target_layout=block)
loader.execute()
target.modelspace().add_blockref(block.name, (0, 0, 0))
stream = io.StringIO()
target.write(stream)
imported = EzdxfReader().read('roi-wrapped.dxf', stream.getvalue().encode())
features = imported.geometry.feature_collection['features']
report = dict(control='same fragment, identity block rather than modelspace entities',
              source_entities=len(source.modelspace()), block_entities=len(block),
              features=len(features),
              blocking_features=sum(bool(f['properties'].get('source_analysis_blocking')) for f in features),
              unexpanded_arrays=sum(bool(f['properties'].get('source_unexpanded_array')) for f in features),
              warnings=imported.warnings, seconds=time.monotonic()-began)
with (LAB/'wrapped-control.json').open('x') as f:
    json.dump(report, f, ensure_ascii=False, indent=2)
print(json.dumps(report, ensure_ascii=False))
assert report['source_entities'] == report['block_entities']
assert report['blocking_features'] == 1
