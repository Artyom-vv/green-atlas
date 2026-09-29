"""Export a completed, exact-input OpenAI proposal cache, without project data or secrets."""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps/api"))
from app.dxf_import.layer_contracts import Layer
from app.layer_recognition.providers import MODEL
from app.layer_recognition.service import LayerRecognitionService

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--database", type=Path, required=True)
parser.add_argument("--project-id", required=True)
parser.add_argument("--destination", type=Path, required=True)
args = parser.parse_args()
with sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True) as db:
    row = db.execute(
        "SELECT json_extract(payload,'$.layers'), "
        "json_extract(payload,'$.source_file.content_sha256'), "
        "json_extract(payload,'$.source_file.cad_snapshot_provenance.live_capture.original_disk_sha256') "
        "FROM projects WHERE id=?",
        (args.project_id,),
    ).fetchone()
if not row:
    raise SystemExit("Project not found")
layers = [Layer.model_validate(item) for item in json.loads(row[0])]
if not row[2] or len(row[2]) != 64:
    raise SystemExit("A verified live AutoCAD original DWG is required")
provider = "openai/" + MODEL
source_key = LayerRecognitionService.cache_key(provider, row[1], layers)
source = Path(str(args.database) + ".layer-recognition") / (source_key + ".json")
result = LayerRecognitionService._read_cache(source, layers, row[1], provider)
if result is None:
    raise SystemExit(
        "No completed result for the current model, prompt and exact input"
    )
args.destination.mkdir(parents=True, exist_ok=True)
result.source_sha256 = row[2]
preset_key = LayerRecognitionService.cache_key(provider, row[2], layers)
target = args.destination / (preset_key + ".json")
with target.open("x") as output:
    output.write(result.model_dump_json())
print(f"Exported {len(result.proposals)} proposals: {target}")
