"""Run current production reader on a complete DXF, no project or DB writes."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'apps/api'))
from app.dxf_import.adapters import EzdxfReader
from app.dxf_import.capacity import SourceGeometryCapacity
from app.geometry.geojson_size import coordinate_count

source, output = map(Path, sys.argv[1:3])
budget=int(sys.argv[3]) if len(sys.argv)>3 else 2_000_000
feature_budget=int(sys.argv[4]) if len(sys.argv)>4 else 100_000
assert not output.exists()
start = time.monotonic()
result = dict(source=str(source), sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
              diagnostic_coordinate_budget=budget, diagnostic_feature_budget=feature_budget,
              production_policy_changed=False)
try:
    imported = EzdxfReader(capacity=SourceGeometryCapacity(max_coordinates=budget,
        max_features=feature_budget)).read(source.name, source.read_bytes())
    features = imported.geometry.feature_collection['features']
    result.update(status='completed', entity_count=imported.entity_count,
                  features=len(features), coordinates=sum(coordinate_count(f['geometry']) for f in features),
                  feature_entity_types=dict(Counter(f['properties'].get('entity_type') for f in features)),
                  warnings=imported.warnings, units=imported.units, bounds=imported.bounds,
                  region_features=[f for f in features if f['properties'].get('entity_type')=='REGION'],
                  blocking_features=sum(bool(f['properties'].get('source_analysis_blocking')) for f in features),
                  unexpanded_arrays=sum(bool(f['properties'].get('source_unexpanded_array')) for f in features),
                  context_only=sum(bool(f['properties'].get('source_context_only')) for f in features))
except Exception:
    result.update(status='failed',error=traceback.format_exc())
result['seconds']=time.monotonic()-start
output.write_text(json.dumps(result, indent=2, ensure_ascii=False)+'\n', encoding='utf8')
print(json.dumps(result, ensure_ascii=False))
sys.exit(0 if result['status']=='completed' else 1)
