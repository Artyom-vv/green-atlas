"""Try independent source groups against existing assembly and upload/reader gates.

Uses official eTransmit root declarations plus explicit plan roots. Never
silently repairs bindings, unit metadata, source entities, or layer mappings.
"""

import contextlib
import io
import json
import os
from pathlib import Path, PureWindowsPath
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps/api"))
from app.cad_import.policy import ConversionPolicy
from app.cad_import.process import run_converter
from app.dxf_import.limits import MAX_DXF_CONTENT_BYTES
from prepare_audit_group import main as prepare_group


def main(summary_path: Path):
    summary_path = summary_path.resolve()
    summary = json.loads(summary_path.read_text(encoding="utf8"))
    extra_path = summary_path.parent / "extra-group-roots.json"
    extra_roots = set(json.loads(extra_path.read_text(encoding="utf8"))["roots"]) if extra_path.exists() else set()
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONPATH": str(ROOT / "apps/api")}
    policy = ConversionPolicy(timeout_seconds=300, max_memory_bytes=4096 * 1024**2,
                              max_log_bytes=16 * 1024**2)
    results = []
    for street in summary["streets"]:
        nodes = {n["path"]: n for n in street["files"]}
        declared = {p["matched_root"] for p in street["declared_packages"] if p["matched_root"]}
        declared_names = {PureWindowsPath(p["declared"]).name.casefold() for p in street["declared_packages"]}
        for group in street["root_checks"]:
            root = group["root"]
            row = nodes[root]
            if row.get("archive") or (row["role_hint"] != "source" and root not in extra_roots):
                continue
            stem = Path(root).stem.casefold()
            is_plan = any(w in stem for w in ("апот", "генплан", "генеральный", "гп и пб"))
            if root not in declared and Path(root).name.casefold() not in declared_names and not is_plan and root not in extra_roots:
                continue
            record = {"street": street["street"], "root": root, "member_count": len(group["files"]),
                      "published": False, "regulatory_mapping_verified": False}
            if group["pending"]:
                record.update(status="pending_inventory", pending=group["pending"])
            elif not group["exact_links_readable"]:
                record.update(status="reference_review_or_read_failure", unresolved=group["unresolved"],
                              failed=group["failed"], cycles=group["cycles"])
            else:
                with contextlib.redirect_stdout(io.StringIO()):
                    profile = prepare_group(summary_path, root)
                folder = Path(profile["directory"])
                # A drawing without active external dependencies already IS a
                # prepared whole source. Re-saving it would test an unnecessary
                # SDK rewrite, rather than the existing import admission path.
                if len(group["files"]) == 1:
                    folder = folder / "direct"
                    folder.mkdir(exist_ok=True)
                qualification = folder / "qualification.json"
                if qualification.exists():
                    results.append(json.loads(qualification.read_text(encoding="utf8")))
                    continue
                record["directory"] = str(folder)
                assembly_report = folder / "assembly.json"
                assembly_receipt = folder / "assembly-process.json"
                assembled = folder / "assembled.dxf"
                if len(group["files"]) == 1 and not assembly_report.exists():
                    if not assembled.exists():
                        os.link(profile["root"], assembled)
                    assembly_report.write_text(json.dumps({
                        "scope": "already self-contained source; no SDK rewrite or binding required",
                        "root": root, "output": str(assembled), "bytes": assembled.stat().st_size,
                        "sha256": profile["files"][0]["dxf_sha256"], "bindings": [], "missing": [],
                        "all_active_references_resolved": True, "geometry_fidelity_qualified": False,
                        "project_published": False,
                    }, ensure_ascii=False, indent=2), encoding="utf8")
                if not assembly_report.exists() and not assembly_receipt.exists():
                    try:
                        run = run_converter(
                            [sys.executable, "-X", "utf8", str(ROOT / "scripts/cad-lab/assemble_dxf_fixture.py"),
                             profile["package"], profile["root"], str(assembled), str(assembly_report)],
                            assembled, folder / "assembly-process.log", policy, environment=env)
                        receipt = {**run.__dict__, "status": "completed" if run.exit_code == 0 else "failed"}
                    except Exception as error:
                        receipt = {"status": "failed", "error": str(error)}
                    assembly_receipt.write_text(json.dumps(receipt, indent=2), encoding="utf8")
                if not assembly_report.exists():
                    record.update(status="assembly_failed", process=json.loads(assembly_receipt.read_text(encoding="utf8")))
                    log = (folder / "assembly-process.log").read_text(encoding="utf8", errors="replace")
                    record["error_tail"] = log[-5000:]
                else:
                    assembly = json.loads(assembly_report.read_text(encoding="utf8"))
                    record["assembly"] = assembly
                    if assembled.stat().st_size > MAX_DXF_CONTENT_BYTES:
                        record.update(status="ordinary_upload_limit", bytes=assembled.stat().st_size,
                                      ordinary_upload_limit_bytes=MAX_DXF_CONTENT_BYTES)
                    else:
                        reader_report, reader_receipt = folder / "reader.json", folder / "reader-process.json"
                        if not reader_report.exists() and not reader_receipt.exists():
                            try:
                                run = run_converter(
                                    [sys.executable, "-X", "utf8", str(ROOT / "scripts/cad-lab/probe_street_project.py"),
                                     str(assembled), str(reader_report)], reader_report,
                                    folder / "reader-process.log", policy, environment=env)
                                receipt = {**run.__dict__, "status": "completed" if run.exit_code == 0 else "failed"}
                            except Exception as error:
                                receipt = {"status": "failed", "error": str(error)}
                            reader_receipt.write_text(json.dumps(receipt, indent=2), encoding="utf8")
                        if reader_report.exists():
                            reader = json.loads(reader_report.read_text(encoding="utf8"))
                            record["reader"] = {k: v for k, v in reader["reader"].items() if k != "layers"}
                            record["calculation"] = reader.get("calculation_with_unverified_default_mapping")
                            record["reader_process"] = json.loads(reader_receipt.read_text(encoding="utf8")) if reader_receipt.exists() else None
                            record["status"] = "reader_failed" if reader["reader"]["status"] != "completed" else "readable_mapping_requires_review"
                        else:
                            record.update(status="reader_process_failed", process=json.loads(reader_receipt.read_text(encoding="utf8")))
                qualification.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf8")
            results.append(record)
            (summary_path.parent / "group-qualification.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf8")
            print(json.dumps({"street": record["street"], "root": root, "status": record["status"]}, ensure_ascii=False), flush=True)
    (summary_path.parent / "group-qualification.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf8")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
