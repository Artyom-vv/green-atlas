"""Isolated shutdown experiment; reuse the existing diagnostic process runner.

Never imports a CAD parser, changes a source drawing, or treats exit 254 as OK.
All native files/profile/script/output are private copies under a new temp root.
"""
from __future__ import annotations

import argparse
import json
import plistlib
import shutil
import subprocess
import tempfile
from pathlib import Path

from run_direct_queries import CORE, digest, quoted, run_core


REPO = Path(__file__).resolve().parents[3]
BASELINE = Path("/private/tmp/ga-product-query-batch-20260923-01")
WORKER = REPO / ".runtime/autocad-bridge/query-worker/build-FjEdV1CF/GreenAtlasQuery.bundle"
EVIDENCE = REPO / "artifacts/native-query-exit-20260923"


def main() -> None:
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--mode", choices=("no-worker-quit", "no-worker-eof",
        "worker-load-quit", "worker-query-quit", "worker-query-eof",
        "worker-query-quit-single", "worker-query-unload-quit"), required=True)
    parser.add_argument("--drawing", choices=("product", "empty"), default="product")
    parser.add_argument("--security", choices=("trusted", "scratch-secureload-zero"), default="trusted")
    parser.add_argument("--entry", help="Alternative package-relative DWG for no-query controls")
    parser.add_argument("--cleanup", choices=("default", "close-document", "unload-xrefs", "audit-no-repair"), default="default")
    parser.add_argument("--lifecycle", action="store_true", help="Test explicit public Mac acdbTerminate at process exit, diagnostic only")
    parser.add_argument("--prepare-only", action="store_true", help="Prepare an isolated target for an interactive owned debugger")
    args = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="ga-query-exit-", dir="/private/tmp"))
    archive = EVIDENCE / root.name
    archive.mkdir(parents=True, exist_ok=False)
    receipt = {"mode": args.mode, "drawing": args.drawing, "security": args.security, "cleanup": args.cleanup,
               "lifecycle_diagnostic": args.lifecycle,
               "root": str(root), "evidence": str(archive),
               "baseline": str(BASELINE), "core": str(CORE), "architecture": "x86_64"}
    print(json.dumps({"started": receipt}), flush=True)
    files = []
    baseline = json.loads((BASELINE / "receipt.json").read_text())
    for row in baseline["files"]:
        source = Path(row["staged"])
        target = root / "package" / source.relative_to(BASELINE / "package")
        target.parent.mkdir(parents=True, exist_ok=True)
        if digest(source) != row["sha256"]:
            raise ValueError(f"Baseline copy changed: {source}")
        subprocess.run(["/bin/cp", "-c", "-p", str(source), str(target)], check=True)
        files.append({"source": str(source), "copy": str(target), "sha256": row["sha256"]})
    drawing = next(Path(row["copy"]) for row in files
                   if Path(row["copy"]).parent == root / "package")
    if args.entry:
        if args.mode.startswith("worker-query-"):
            parser.error("A query always uses the frozen protocol-1 host")
        candidate = (root / "package" / args.entry).resolve(strict=True)
        if not candidate.is_relative_to(root / "package"):
            parser.error("Entry outside private package")
        drawing = candidate
    if args.drawing == "empty":
        template = CORE.parents[4] / "Resources/UserDataCache/en-us/Template/acadiso.dwt"
        drawing = root / "Empty.dwg"
        shutil.copy2(template, drawing)
        files.append({"source": str(template), "copy": str(drawing), "sha256": digest(template)})
    bundle = root / "QueryWorker.dbx"
    shutil.copytree(WORKER, bundle)
    subprocess.run(["codesign", "--verify", "--strict", str(bundle)], check=True)
    receipt["worker_binary_sha256"] = digest(bundle / "Contents/MacOS/GreenAtlasBridge")
    if receipt["worker_binary_sha256"] != "7d3e36a079d6e61133db61f255a679cd46687279ee50bea2bb0bc93958b66afc":
        raise ValueError("Worker changed; protocol 1/2 comparisons must not be mixed")
    receipt["probe_source_sha256"] = digest(Path(__file__))
    receipt["core_binary_sha256"] = digest(CORE)
    receipt["core_info"] = plistlib.loads((CORE.parents[1] / "Info.plist").read_bytes())
    receipt["files"] = files
    # Freeze the original protocol-1 request; current shared API is migrating
    # independently to protocol 2. Change only this experiment's output path.
    frozen = (BASELINE / "request.txt").read_text().splitlines()
    if frozen[0] != "green-atlas.native-object-query/1":
        raise ValueError("Expected frozen protocol-1 evidence")
    frozen[1] = str(root / "native.json")
    encoded = ("\n".join(frozen) + "\n").encode()
    (root / "request.txt").write_bytes(encoded)
    lines = [f'(setvar "TRUSTEDPATHS" {quoted(str(root) + "/")})',
             '(setvar "FILEDIA" 0)',
             '(princ (strcat "\\nGA_EXIT_BEFORE_DBMOD=" (itoa (getvar "DBMOD"))))']
    if args.security == "scratch-secureload-zero":
        lines.insert(0, '(setvar "SECURELOAD" 0)')
    if args.mode.startswith("worker-"):
        lines.append(f'(arxload {quoted(bundle)})')
    if args.mode.startswith("worker-query-"):
        lines += ["GAQUERYOBJECTS", str(root / "request.txt")]
    lines += ['(princ (strcat "\\nGA_EXIT_AFTER_DBMOD=" (itoa (getvar "DBMOD"))))',
              f'(setq gaDone (open {quoted(root / "completed")} "w")) (close gaDone)']
    if args.lifecycle:
        # Reuse an existing diagnostic compiler, not a process adapter or worker.
        from capture_session_run import build
        lifecycle_source = Path(__file__).with_name("query_worker_exit_lifecycle.cpp")
        lifecycle_bundle = build(root, lifecycle_source)
        receipt["lifecycle_source_sha256"] = digest(lifecycle_source)
        receipt["lifecycle_binary_sha256"] = digest(lifecycle_bundle / "Contents/MacOS/CaptureSession")
        lines.append(f'(arxload {quoted(lifecycle_bundle)})')
    if args.mode == "worker-query-unload-quit":
        lines.append(f'(arxunload {quoted(bundle)})')
    if args.cleanup == "close-document":
        lines += ["_.CLOSE", "_Y"]
    elif args.cleanup == "unload-xrefs":
        lines += ["_.-XREF", "_UNLOAD", "*"]
    elif args.cleanup == "audit-no-repair":
        lines += ["_.AUDIT", "_N",
                  '(princ (strcat "\\nGA_EXIT_POST_AUDIT_DBMOD=" (itoa (getvar "DBMOD"))))']
    if not args.mode.endswith("eof"):
        lines += ["_QUIT", "_Y"]
    script = root / "query.scr"
    script.write_text("\n".join(lines) + ("\n" if args.mode.endswith(("eof", "single")) else "\n\n"))
    receipt["argv"] = ["/usr/bin/arch", "-x86_64", str(CORE), "/i", str(drawing),
                       "/s", str(script), "/isolate", f"ga-direct-{root.name}", str(root / "profile")]
    receipt["stdin"] = "DEVNULL"
    if args.prepare_only:
        (root / "profile").mkdir()
        (archive / "prepared.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2, default=str) + "\n")
        for name in ("query.scr", "request.txt"):
            shutil.copy2(root / name, archive / name)
        print(json.dumps({"prepared": str(archive / "prepared.json"), "argv": receipt["argv"]}), flush=True)
        return
    try:
        receipt["engine"] = run_core(root, drawing, script, 55)
        if (root / "native.json").is_file():
            reply = json.loads((root / "native.json").read_text())
            expected = json.loads((BASELINE / "native.json").read_text())
            if reply["request_sha256"] != digest(root / "request.txt"):
                raise ValueError("Reply does not belong to this exact request")
            def without_timings(value):
                if isinstance(value, dict):
                    return {key: without_timings(item) for key, item in value.items()
                            if key not in {"elapsed_ms", "prepare_ms", "request_sha256"}}
                if isinstance(value, list):
                    return [without_timings(item) for item in value]
                return value
            if without_timings(reply) != without_timings(expected):
                raise ValueError("Reply differs from frozen protocol-1 measurements/identity")
            receipt["reply_validated"] = True
            receipt["validation_method"] = "frozen protocol-1 full semantic parity + exact request hash"
            receipt["reply_summary"] = {"objects": len(reply["objects"]), "points": reply["point_count"],
                                        "database_modified_flags": reply["database_modified_flags"]}
        else:
            receipt["reply_validated"] = False
    except Exception as error:
        receipt["error"] = repr(error)
    finally:
        receipt["sources_and_copies_unchanged"] = all(
            digest(Path(row["source"])) == row["sha256"] == digest(Path(row["copy"])) for row in files)
        for name in ("core.log", "query.scr", "request.txt", "native.json", "build-command.json", "build.log"):
            if (root / name).is_file():
                shutil.copy2(root / name, archive / name)
        receipt["artifact_sha256"] = {path.name: digest(path) for path in archive.iterdir() if path.is_file()}
        (archive / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2, default=str) + "\n")
        print(json.dumps({key: receipt.get(key) for key in ("mode", "root", "evidence", "engine",
                         "reply_validated", "error", "sources_and_copies_unchanged")}), flush=True)


if __name__ == "__main__":
    main()
