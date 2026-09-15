from pathlib import Path
import argparse
import json
import sys


API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

from app.main import app  # noqa: E402


parser = argparse.ArgumentParser(description="Export the API contract without running an HTTP server.")
parser.add_argument("--output", type=Path, default=API_ROOT / "openapi.json")
target = parser.parse_args().output
target.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2), encoding="utf-8")
print(target)

