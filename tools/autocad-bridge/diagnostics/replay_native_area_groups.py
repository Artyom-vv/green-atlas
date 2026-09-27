"""Compare the UI's actual native window runner before/after group integration.

Explicit archived experimental layer rules, independent packages, no service
restart, no mutation of user projects. A lifecycle warning remains a failure
of product qualification even when this geometry comparison is successful.
"""
import argparse
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

from ui_native_runner import PilotWindowRunner


def main():
    parser = argparse.ArgumentParser(__doc__)
    for key in ("baseline", "rules", "before", "after"):
        parser.add_argument("--" + key, type=Path, required=True)
    args = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="ga-group-window-", dir="/private/tmp"))
    policies = json.loads(args.rules.read_bytes())["calculation"]["layer_rules"]

    class ReplayRunner(PilotWindowRunner):
        @staticmethod
        def rules(project):
            return {r["layer"]: (r["role"], r["clearance"]) for r in policies}

    cases = {
        "joined_building_interior": (16120.65555935676, -5262.936627369001),
        "closed_building_7BF": (15933.635923305208, -4960.738540876491),
        "road_24": (16092.001603, -5397.977498),
        "open_building_13": (15994.606306, -5245.249309),
    }
    summary = {"scope": "full available package, same experimental rules, native UI runner; no project changes",
               "root": str(root), "cases": {}}
    print(json.dumps({"root": str(root)}), flush=True)
    for label, bundle in (("before", args.before), ("after", args.after)):
        runner = ReplayRunner(args.baseline, bundle, root / label)
        for name, point in cases.items():
            answers = runner(SimpleNamespace(id="group-integration-control"), [point])
            path = Path(runner.calls[-1])
            receipt = json.loads(path.read_bytes())
            native = json.loads((path.parent / "native.json").read_bytes())
            calculation = native["inventory"]["window_inventory"]["calculation"]
            summary["cases"].setdefault(name, {})[label] = {
                "answer": answers[0], "receipt": str(path), "engine": receipt["engine"],
                "groups": [row for row in calculation["object_ledger"] if row.get("native_group_members")],
                "accepted_groups": calculation.get("accepted_native_groups", 0),
                "rejected_groups": calculation.get("rejected_native_groups", []),
            }
            (root / "comparison.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    # Negative controls remain blocked, and joined walls are a real native area
    # in the consumer used by UI (not just in a selected-object experiment).
    joined = summary["cases"]["joined_building_interior"]["after"]
    expected_members = {"6E16/ACE2", "6E16/E201"}
    summary["joined_area_in_consumer"] = any(
        set(g["native_group_members"]) == expected_members and g["area_ready"] for g in joined["groups"])
    summary["known_negative_controls_blocked"] = all(
        summary["cases"][name]["after"]["answer"]["result"] == "blocked"
        for name in ("joined_building_interior", "closed_building_7BF", "road_24"))
    summary["all_processes_exit_zero"] = all(
        result["engine"]["exit_code"] == 0 for case in summary["cases"].values() for result in case.values())
    summary["product_qualified"] = False
    (root / "comparison.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps({key: value for key, value in summary.items() if key != "cases"}), flush=True)


if __name__ == "__main__":
    main()
