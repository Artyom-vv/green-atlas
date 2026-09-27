"""Verify offline controls and optionally apply plants ONLY in a disposable project.

The caller must explicitly supply --apply-test-copy to write. No source drawing
or user reference project is opened, rebound, or modified by this diagnostic.
"""

import argparse
import json
import os
from pathlib import Path
from time import monotonic

from app.composition import create_runtime
from app.planning.change_contracts import PlanChangeSetApplyRequest
from app.planning.pattern_contracts import FillPatternRequest

CONTROLS = (
    ("building", 15994.606306, -5245.249309),
    ("road", 16095, -5397.5),
    ("road", 16092.001603, -5397.977498),
)
KUST_SOURCE_SHA = "36ec0f0db3a2ac88c575a9460283a2498504215a4b0daaf98aa5cb18fb2a0a8a"


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("project")
    parser.add_argument("preview_receipt", type=Path)
    parser.add_argument("--apply-test-copy", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    runtime = create_runtime(args.database)
    result = {}
    try:
        project = runtime.application.get(args.project)
        session = project.source_file.native_session
        assert session.source_sha256 == KUST_SOURCE_SHA, "Controls belong to the frozen Kustanayskaya fixture"
        try:
            os.kill(session.pid, 0)
        except ProcessLookupError:
            result["cad_process_stopped"] = True
        else:
            raise ValueError("Stop the disposable CAD session before this offline test")
        geometry = runtime.application.geometry
        result["controls"] = []
        for role, x, y in CONTROLS:
            violation = geometry.position_violation(project, x, y, .65, "shrub")
            assert violation is not None and violation.code == "NATIVE_OCCUPIED"
            result["controls"].append({"role": role, "x": x, "y": y,
                                       "code": violation.code, "layer": violation.source_layer})
        request = FillPatternRequest.model_validate(json.loads(args.preview_receipt.read_text())["request"])
        started = monotonic()
        while True:
            preview = runtime.application.preview_pattern(project.id, request)
            if all(d.stop_reason == "resolution" for d in preview.search_domains):
                break
        result.update(preview_s=monotonic() - started, accepted=preview.accepted_count,
                      requested=preview.requested_count, applied=False)
        assert preview.accepted_count == request.target_count
        assert preview.change_set and preview.change_set.can_apply
        if args.apply_test_copy:
            assert not project.plan.objects, "Never overwrite an existing planting plan"
            start = monotonic()
            change = preview.change_set
            saved = runtime.application.apply_change_set(project.id, PlanChangeSetApplyRequest(
                preview_id=change.id, digest=change.digest, base_plan_version=project.plan.version,
            ))
            result.update(applied=True, saved_count=len(saved.plan.objects), apply_s=monotonic() - start)
            assert len(saved.plan.objects) == request.target_count
        result["measurement_backend"] = "prepared_geometry"
    finally:
        runtime.close()
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
