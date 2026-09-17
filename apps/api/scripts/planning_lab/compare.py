"""Compare two intact offline reports, excluding wall-clock measurements."""

import argparse
import json
from pathlib import Path

from .evidence import digest


def compare_reports(first: dict, second: dict) -> dict:
    for report in (first, second):
        if report["content_sha256"] != digest(report["content"]):
            raise ValueError("Report content hash does not match")
    left, right = first["content"], second["content"]
    result_fields = (
        "outcome",
        "error",
        "result",
        "generation_calls",
        "validation_calls",
    )
    # Recompute from content rather than trusting an external result_sha256.
    return {
        "same_input": left["input_sha256"] == right["input_sha256"],
        "same_basis": left["basis"] == right["basis"],
        "same_result": all(left[key] == right[key] for key in result_fields),
        "scenario_seconds": [
            report["measurements"]["scenario_seconds"] for report in (first, second)
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("first", type=Path)
    parser.add_argument("second", type=Path)
    args = parser.parse_args()
    try:
        result = compare_reports(
            json.loads(args.first.read_bytes()), json.loads(args.second.read_bytes())
        )
    except (ValueError, KeyError, OSError) as error:
        parser.error(str(error))
    print(json.dumps(result, indent=2))
    return 0 if result["same_input"] and result["same_result"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
