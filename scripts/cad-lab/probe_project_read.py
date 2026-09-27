"""Read a published snapshot and verify its immutable source in a bounded process."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps/api"))

from app.geometry.geojson_size import coordinate_count
from app.projects.adapters import SqliteProjectRepository


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("project_id")
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    args.database.resolve(strict=True)
    repository = SqliteProjectRepository(args.database)
    started = time.monotonic()
    try:
        project = repository.get(args.project_id)
        elapsed = time.monotonic() - started
        source = repository.get_source(project.id)
        assert source is not None and project.source_file is not None
        assert project.geometry is not None
        features = project.geometry.feature_collection["features"]
        digest = sha256(source).hexdigest()
        assert digest == project.source_file.content_sha256
        args.report.write_text(json.dumps({
            "project_id": project.id, "read_seconds": elapsed,
            "features": len(features), "coordinates": sum(coordinate_count(f["geometry"]) for f in features),
            "source_sha256": digest, "source_bytes": len(source),
            "editable": project.import_status.editability.value,
            "source_pending": project.source_review is not None,
            "state_version": project.state_version,
        }, indent=2), encoding="utf8")
    finally:
        repository.close()


if __name__ == "__main__":
    main()
