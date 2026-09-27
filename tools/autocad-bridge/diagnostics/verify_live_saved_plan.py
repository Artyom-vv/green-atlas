"""Read-only fresh-runtime validation and exact persistence check of UI plantings."""

import argparse
import hashlib
import json
import time
from pathlib import Path

from app.composition import create_runtime
from app.native_query.live_runtime import LiveBinding, binding_path


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    binding = LiveBinding.model_validate_json(binding_path(args.database).read_bytes())
    with Path(binding.session.source_path).open("rb") as source:
        current_sha = hashlib.file_digest(source, "sha256").hexdigest()
    if current_sha != binding.session.source_sha256:
        raise ValueError("DXF changed since capture")
    receipt = {
        "project_id": binding.project_id,
        "source_sha256": current_sha,
        "snapshot_sha256": binding.session.snapshot_sha256,
        "linear_layers": sorted(binding.linear_layers),
        "replays": [],
    }
    original = None
    for _ in range(2):
        runtime = create_runtime(args.database)
        start = time.monotonic()
        try:
            project = runtime.application.get(binding.project_id)
            plan = project.plan
            if not plan or not plan.objects:
                raise ValueError("No saved planting to verify")
            persisted = plan.model_dump(mode="json")
            if original is not None and persisted != original:
                raise ValueError("Saved plan differs between independent reloads")
            original = persisted
            issues = runtime.application.validator.validate_plan(project, plan)
            record = {
                "elapsed_s": time.monotonic() - start,
                "plan_version": plan.version,
                "state_version": project.state_version,
                "object_count": len(plan.objects),
                "issues": [i.model_dump(mode="json") for i in issues],
            }
            receipt["replays"].append(record)
            if any(
                i.severity == "error" or i.code != "SOURCE_GEOMETRY_PARTIAL"
                for i in issues
            ):
                raise ValueError(
                    "Stored planting has a local conflict or local unknown"
                )
            print(
                json.dumps({k: v for k, v in record.items() if k != "issues"}),
                flush=True,
            )
        finally:
            runtime.close()
    if receipt["replays"][0]["issues"] != receipt["replays"][1]["issues"]:
        raise ValueError("Native validator replies differ between cold replays")
    receipt["saved_plan"] = original
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
