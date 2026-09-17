"""Run from apps/api: python -m scripts.planning_lab case.json --output run.json."""

import argparse
import sys
from pathlib import Path

from .contracts import PlanningCase
from .evidence import publish_new
from .runner import run_case


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; choose a new report path")
    try:
        case = PlanningCase.model_validate_json(args.case.read_bytes())
        report = run_case(case)
        publish_new(args.output, report)
    except (ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 2
    print(f"{report['content']['outcome']}: {report['content_sha256']}")
    return 0 if report["content"]["outcome"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
