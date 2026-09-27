"""Repeatable DWG -> AutoCAD DXFOUT -> fresh AutoCAD DXF capture experiment.

Never parses CAD or saves the original drawing. Run one street at a time.
Inputs with external references must be staged with their dependencies first;
the report compares actual captured references, never assumes they survived.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import shutil
import signal
import subprocess
import time
from collections import Counter
from pathlib import Path

import ijson
from roundtrip_comparison import compare, stream_capture

DEFAULT_CORE = Path(
    "/Applications/Autodesk/AutoCAD 2027/AutoCAD 2027.app/Contents/Helpers/"
    "AcCoreConsole.app/Contents/MacOS/AcCoreConsole"
)


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def lisp(value: Path | str) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def stage_references(files: list[Path], root: Path) -> list[dict]:
    records = []
    for path in files:
        source = path.resolve(strict=True)
        destination = root / source.name
        if destination.exists():
            raise ValueError(f"Duplicate staged reference name: {source.name}")
        shutil.copy2(source, destination)
        records.append({"source": str(source), "staged": str(destination), "sha256": digest(source)})
    return records


def references_unchanged(records: list[dict]) -> bool:
    return all(digest(Path(r["source"])) == digest(Path(r["staged"])) == r["sha256"] for r in records)


def item(path: Path, prefix: str):
    with stream_capture(path) as stream:
        return next(ijson.items(stream, prefix, use_float=True), None)


def summarize(path: Path) -> dict:
    groups: Counter = Counter()
    identities = set()
    source = item(path, "source")
    with stream_capture(path) as stream:
        for row in ijson.items(stream, "coverage.item", use_float=True):
            identity = (row["handle"], tuple(row["instance_chain"]), row["entity_type"], row["source_layer"])
            if identity in identities:
                raise ValueError(f"Duplicate source instance: {identity}")
            identities.add(identity)
            if row["status"] == "unresolved":
                groups[(row["entity_type"], row["layer"], row.get("reason", ""))] += 1
    return {
        "source": source,
        "capture_mode": item(path, "capture_mode"),
        "plugin_version": item(path, "plugin_version"),
        "summary": item(path, "summary"),
        "instance_count": len(identities),
        "instance_identity_sha256": hashlib.sha256(
            json.dumps(sorted(identities), ensure_ascii=False).encode()
        ).hexdigest(),
        "unresolved": sum(groups.values()),
        "unresolved_groups": [
            {"entity_type": key[0], "layer": key[1], "reason": key[2], "count": count}
            for key, count in groups.most_common()
        ],
    }


def run_core(args, root: Path, drawing: Path, script_text: str, name: str) -> dict:
    script = root / f"{name}.scr"
    completion = root / f"{name}.completed"
    marker = f'(setq gaDone (open {lisp(completion)} "w")) (write-line "complete" gaDone) (close gaDone)\n'
    script_text = script_text.replace("_QUIT\n_Y\n", marker + "_QUIT\n_Y\n\n")
    script.write_text(script_text, encoding="utf-8")
    profile = root / f"profile-{name}"
    profile.mkdir()
    command = ["/usr/bin/arch", "-x86_64", str(args.core), "/i", str(drawing),
               "/s", str(script), "/isolate", f"ga-{root.name}-{name}", str(profile)]
    started = time.monotonic()
    with (root / f"{name}.log").open("wb") as log:
        child = subprocess.Popen(command, cwd=root, stdin=subprocess.DEVNULL,
                                 stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        completed_at = None
        stopped_after_completion = False
        while child.poll() is None:
            if completion.is_file() and completed_at is None:
                completed_at = time.monotonic()
            elapsed = time.monotonic() - started
            if elapsed > args.timeout or (completed_at and time.monotonic() - completed_at > 15):
                # macOS Core Console can spin during QUIT after the commands
                # returned. Only this owned process group is stopped; receipt
                # distinguishes this from a clean engine exit.
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait()
                stopped_after_completion = completed_at is not None
                if not stopped_after_completion:
                    raise RuntimeError(f"{name} exceeded {args.timeout}s; log: {root / (name + '.log')}")
                break
            time.sleep(0.25)
        code = child.returncode
    result = {"command": command, "exit_code": code,
              "commands_completed": completion.is_file(),
              "engine_shutdown_required_termination": stopped_after_completion,
              "seconds": round(time.monotonic() - started, 2)}
    # This Core Console also returns 254 after a completed QUIT. Preserve it
    # as an abnormal exit in the receipt, but validate the finished artifacts
    # instead of pretending a successful DXFOUT never happened.
    if code and not stopped_after_completion and not (code == 254 and completion.is_file()):
        raise RuntimeError(f"{name} failed ({code}); inspect {root / (name + '.log')}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--core", type=Path, default=DEFAULT_CORE)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--xref", type=Path, action="append", default=[],
                        help="Known companion DWG staged by basename; no name inference")
    parser.add_argument("--resume", action="store_true", help="Continue a recorded failed run after its verified DXFOUT")
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    bundle = args.bundle.resolve(strict=True)
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    previous = None
    if args.resume:
        previous = json.loads((root / "receipt.json").read_text())
        if not previous.get("error") or not previous.get("source_unchanged"):
            parser.error("Only a failed run with an unchanged source can be resumed")
        if previous["original"] != str(source) or previous["source_sha256_before"] != digest(source):
            parser.error("Resume source differs from the recorded original")
    elif any(root.iterdir()):
        parser.error("Output must be an empty directory; existing experiments are never overwritten")
    if source.suffix.lower() != ".dwg":
        parser.error("Round-trip input must be a DWG; DXF is produced by AutoCAD")
    if shutil.disk_usage(root).free < (600_000_000 if args.resume else 1_500_000_000):
        parser.error("Need at least 1.5 GB free for a bounded single-street experiment")
    before = digest(source)
    drawing = root / "Drawing.dwg"
    dxf = root / "Drawing.dxf"
    staged_bundle = root / "GreenAtlasBridge.dbx"
    if args.resume:
        if digest(source) != digest(drawing) or not dxf.is_file():
            parser.error("Staged DWG differs or DXFOUT result is missing")
        if digest(bundle / "Contents/MacOS/GreenAtlasBridge") != digest(staged_bundle / "Contents/MacOS/GreenAtlasBridge"):
            parser.error("Resume requires the same native executable")
        references = previous.get("references", [])
        if not references_unchanged(references) or {str(p.resolve()) for p in args.xref} != {r["source"] for r in references}:
            parser.error("Resume requires the same unchanged reference files")
    else:
        shutil.copy2(source, drawing)
        shutil.copytree(bundle, staged_bundle)
        references = stage_references(args.xref, root)
    subprocess.run(["codesign", "--verify", "--strict", str(staged_bundle)], check=True)
    info = plistlib.loads((staged_bundle / "Contents/Info.plist").read_bytes())
    preamble = (
        f'(setvar "TRUSTEDPATHS" {lisp(str(root) + "/")})\n'
        '(setvar "FILEDIA" 0)\n'
        f'(arxload {lisp(staged_bundle)})\n'
    )
    report = {
        "schema": "green-atlas.autocad-dxf-roundtrip/2",
        "original": str(source), "source_sha256_before": before,
        "plugin_version": info["CFBundleShortVersionString"],
        "native_binary_sha256": digest(staged_bundle / "Contents/MacOS/GreenAtlasBridge"),
        "references": references,
        "scope": "AutoCAD DXFOUT/reopen and coverage/region identity consistency; not geometric or GUI acceptance",
        "steps": [], "passed": False,
    }
    if previous:
        report["previous_attempt"] = previous
    try:
        # Commands run on a disposable DWG copy. DXFOUT's precision is explicit.
        if not args.resume:
            report["steps"].append(run_core(args, root, drawing, preamble +
                "GAEXPORTREGIONPROBE\n" +
                f'(command "_.DXFOUT" {lisp(dxf)} "16")\n' +
                "_QUIT\n_Y\n", "dwg-export"))
        if not dxf.is_file() or not dxf.stat().st_size:
            raise RuntimeError("AutoCAD did not produce a nonempty DXF")
        report["dxf_sha256"] = digest(dxf)
        report["dxf_bytes"] = dxf.stat().st_size
        report["dwg"] = summarize(Path(str(drawing) + ".green-atlas.geometry.json"))
        # A new AutoCAD process must read the DXF; the live DWG is not reused.
        report["steps"].append(run_core(args, root, dxf, preamble +
            "GAEXPORTREGIONPROBE\n_QUIT\n_Y\n", "dxf-reopen"))
        report["dxf"] = summarize(Path(str(dxf) + ".green-atlas.geometry.json"))
        report["same_instance_identities"] = (
            report["dwg"]["instance_identity_sha256"] == report["dxf"]["instance_identity_sha256"]
        )
        report["dxf_unchanged_by_capture"] = digest(dxf) == report["dxf_sha256"]
        report["comparison"] = compare(
            Path(str(drawing) + ".green-atlas.geometry.json"),
            Path(str(dxf) + ".green-atlas.geometry.json"),
        )
        report["passed"] = report["comparison"]["structure_consistent"] and report["dxf_unchanged_by_capture"]
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        report["error"] = str(error)
    finally:
        report["source_sha256_after"] = digest(source)
        report["source_unchanged"] = before == report["source_sha256_after"] == digest(drawing)
        report["references_unchanged"] = references_unchanged(references)
        report["passed"] = report["passed"] and report["source_unchanged"] and report["references_unchanged"]
        (root / "receipt.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: report.get(key) for key in
                     ["passed", "source_unchanged", "same_instance_identities", "error"]}), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
