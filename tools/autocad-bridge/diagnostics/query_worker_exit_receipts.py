"""Verify this investigation's evidence; never start or supervise a CAD process."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from query_worker_exit_probe import BASELINE, EVIDENCE, REPO
from run_direct_queries import digest


def normalized(value):
    if isinstance(value, dict):
        return {key: normalized(item) for key, item in value.items()
                if key not in {"request_sha256", "elapsed_ms", "prepare_ms"}}
    if isinstance(value, list):
        return [normalized(item) for item in value]
    return value


def differences(expected, actual, path="$"):
    if isinstance(expected, dict) and isinstance(actual, dict):
        result = []
        for key in sorted(expected.keys() | actual.keys()):
            result += differences(expected.get(key), actual.get(key), f"{path}.{key}")
        return result
    if isinstance(expected, list) and isinstance(actual, list) and len(expected) == len(actual):
        return [item for index, pair in enumerate(zip(expected, actual))
                for item in differences(*pair, f"{path}[{index}]")]
    return [] if expected == actual else [{"path": path, "baseline": expected, "actual": actual}]


def main():
    expected = json.loads((BASELINE / "native.json").read_text())
    output = {"schema": "green-atlas.query-worker-exit-investigation/1",
              "product_exit_zero_proven": False,
              "protocol": 1,
              "worker_build": "FjEdV1CF",
              "observed_fault": "libacdb: DbTerm -> acdbTerminate -> emptyOrigEntityCacheMaps, NULL access during exit",
              "safe_production_correction_proven": False,
              "runs": []}
    for folder in sorted(EVIDENCE.glob("ga-query-exit-*")):
        receipt_path = folder / "receipt.json"
        prepared = not receipt_path.is_file()
        if prepared:
            receipt_path = folder / "prepared.json"
        if not receipt_path.is_file():
            continue
        receipt = json.loads(receipt_path.read_text())
        root = Path(receipt["root"])
        row = {"run": folder.name, "manifest_sha256": digest(receipt_path),
               "kind": "prepared-debugger-or-direct" if prepared else "existing-run_core",
               "mode": receipt["mode"], "drawing": receipt["argv"][4],
               "security": receipt.get("security", "not-recorded"), "cleanup": receipt.get("cleanup", "default"),
               "lifecycle_diagnostic": receipt.get("lifecycle_diagnostic", False),
               "architecture": receipt["architecture"],
               "worker_binary_sha256": receipt["worker_binary_sha256"],
               "engine": receipt.get("engine"),
               "marker_exists": (root / "completed").exists(),
               "sources_and_copies_unchanged_now": all(
                   digest(Path(item["source"])) == item["sha256"] == digest(Path(item["copy"]))
                   for item in receipt["files"]),
               "recorded_artifacts_unchanged": all(
                   digest(folder / name) == sha for name, sha in receipt.get("artifact_sha256", {}).items())}
        observation = folder / "execution-observation.json"
        if observation.is_file():
            row["execution_observation"] = json.loads(observation.read_text())
            row["architecture"] = row["execution_observation"]["architecture"]
        for name in ("native.json", "build-command.json", "build.log"):
            if prepared and (root / name).is_file() and not (folder / name).exists():
                shutil.copy2(root / name, folder / name)
        native = folder / "native.json"
        if native.is_file():
            reply = json.loads(native.read_text())
            row["reply_exact_request_hash"] = reply["request_sha256"] == digest(root / "request.txt")
            row["reply_frozen_protocol1_semantic_parity"] = normalized(reply) == normalized(expected)
            row["reply_differences"] = differences(normalized(expected), normalized(reply))
            row["reply_sha256"] = digest(native)
            row["database_revision"] = reply["database_revision"]
            row["database_modified_flags"] = reply["database_modified_flags"]
            row["objects"] = len(reply["objects"])
            row["points"] = reply["point_count"]
        row["debugger_logs"] = {p.name: digest(p) for p in folder.glob("lldb*.txt")}
        # Debugger exit status is intentionally NOT promoted to a Core exit code.
        if row["debugger_logs"]:
            row["debugger_result"] = "stopped at documented breakpoint/fault; debugger ended target; not a clean process receipt"
        output["runs"].append(row)
    goodall = REPO / "artifacts/native-session-capture-20260923/final-saveas/receipt.json"
    scratch = json.loads(goodall.read_text())
    output["goodall_control"] = {"receipt": str(goodall), "sha256": digest(goodall),
                                  "engines": scratch["engines"],
                                  "native_binary_sha256": scratch["native_binary_sha256"],
                                  "same_drawing_or_worker": False}
    output["all_source_copies_unchanged"] = all(row["sources_and_copies_unchanged_now"] for row in output["runs"])
    output["all_recorded_artifacts_unchanged"] = all(row["recorded_artifacts_unchanged"] for row in output["runs"])
    output["all_existing_replies_have_exact_request_and_protocol1_parity"] = all(
        row["reply_exact_request_hash"] and row["reply_frozen_protocol1_semantic_parity"]
        for row in output["runs"] if "reply_sha256" in row)
    output["all_x86_64_replies_have_exact_request_and_protocol1_parity"] = all(
        row["reply_exact_request_hash"] and row["reply_frozen_protocol1_semantic_parity"]
        for row in output["runs"] if "reply_sha256" in row and row["architecture"] == "x86_64")
    (EVIDENCE / "verification.json").write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: value for key, value in output.items() if key.startswith("all_") or key.endswith("proven")}))
    for row in output["runs"]:
        print(row["run"], row["mode"], row["cleanup"], Path(row["drawing"]).name,
              row.get("engine") or row.get("execution_observation", {}).get("exit_code", "debugger"),
              "parity=", row.get("reply_frozen_protocol1_semantic_parity"))
        if row.get("reply_differences"):
            print(json.dumps(row["reply_differences"]))


if __name__ == "__main__":
    main()
