"""Lifecycle A/B on owned copies; never accepts exit 254 or edits source CAD.

Both modes compile the same production kernel and query command. Side mode
changes only database ownership/loading/teardown, not the selected queries.
No package or calculation is attached to a user project by this experiment.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4

from app.native_query.contracts import NativeObjectQuery
from app.native_query.protocol import decode_reply, encode_request
from run_direct_queries import CORE, digest, quoted, run_core
from run_product_query_batch import stage_package

REPO = Path(__file__).resolve().parents[3]


def build(root: Path) -> Path:
    source = REPO / "tools/autocad-bridge/native"
    sdk = REPO / ".local/objectarx-2027"
    acad = CORE.parents[4]
    bundle = root / "QueryWorker.dbx"
    binary = bundle / "Contents/MacOS/GreenAtlasBridge"
    binary.parent.mkdir(parents=True)
    shutil.copy2(source / "Info.plist", bundle / "Contents/Info.plist")
    modules = ("direct_query_kernel", "native_affine_query", "native_area_group",
               "native_curve_query", "native_query_batch", "native_query_command",
               "xref_instance_access", "file_io", "native_area_candidates")
    command = ["xcrun", "clang++", "-std=c++17", "-arch", "x86_64",
               "-mmacosx-version-min=14.0", "-bundle", "-O2", "-DNDEBUG",
               "-D_ADESK_MAC_", "-DOSX_SYSTEM", "-D_NATIVE_WCHAR_T_DEFINED", "-DUNICODE", "-DACDB_EXT",
               "-Wno-deprecated-declarations", "-Wno-nonportable-include-path", "-Wno-extra-tokens",
               "-DDLLNAME_ACBR=AcBr.dbx", "-include", str(source / "prefix.pch"),
               "-I", str(source), "-I", str(sdk / "inc"), "-I", str(sdk / "utils/brep/inc"),
               "-L", str(acad / "Frameworks"), "-F", str(acad / "Frameworks"),
               "-lacfirst", "-lwinapi", "-lacdb", "-laccore", "-lgelib", "-lAcPal", "-lgelibx",
               str(acad / "Plugins/AcGeomentObj.dbx/AcGeomentObj"), str(acad / "Plugins/AcBr.dbx/AcBr"),
               str(Path(__file__).with_name("query_side_database_probe.cpp")),
               str(REPO / "tools/autocad-bridge/query-worker/package_query.cpp"),
               *(str(source / (module + ".cpp")) for module in modules), "-o", str(binary)]
    (root / "build-command.json").write_text(json.dumps(command, ensure_ascii=False, indent=2))
    with (root / "build.log").open("xb") as log:
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
    subprocess.run(["codesign", "--force", "--sign", "-", str(bundle)], check=True)
    subprocess.run(["codesign", "--verify", "--strict", str(bundle)], check=True)
    return bundle


def main() -> int:
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--mode", choices=("document", "side"), required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, help="Reuse the exact diagnostic A/B binary")
    parser.add_argument("--demand-load", type=int, choices=(0, 3),
                        help="Diagnostic only: set demand-loading before side DB read, in the isolated profile")
    parser.add_argument("--inventory", action="store_true", help="Read-only class/handle inventory, side DB only")
    parser.add_argument("--preload-module", action="append", default=[],
                        help="Diagnostic only: explicit module in the installed Autodesk Plugins directory")
    parser.add_argument("--disable-field-evaluation", action="store_true",
                        help="Diagnostic only: scoped public global field-evaluation API before side DB read")
    parser.add_argument("--discover-capture", type=Path, help="Native capture for addresses only, never sampled points")
    parser.add_argument("--layer", action="append", default=[], help="Exact source layer to discover across the complete capture")
    parser.add_argument("--groups-from", type=Path, help="Measure every unambiguous cycle produced by the native discovery")
    args = parser.parse_args()
    if (args.demand_load is not None or args.inventory or args.preload_module or args.disable_field_evaluation) and args.mode != "side":
        parser.error("Demand-load experiment must precede the drawing read; use side mode")
    if args.discover_capture and (args.mode != "document" or not args.layer):
        parser.error("Discovery requires document mode and explicit layers")
    root = Path(tempfile.mkdtemp(prefix="ga-side-query-", dir="/private/tmp"))
    receipt = {"mode": args.mode, "root": str(root), "scope": "lifecycle selected-instance A/B, not capture acceptance",
               "diagnostic_demand_load": args.demand_load,
               "diagnostic_disable_field_evaluation": args.disable_field_evaluation}
    files = []
    print(json.dumps(receipt), flush=True)
    try:
        if shutil.disk_usage(root).free < 2_000_000_000:
            raise RuntimeError("Less than 2 GB free")
        drawing, files = stage_package(json.loads(args.baseline.read_bytes()), root)
        if args.bundle:
            bundle = root / "QueryWorker.dbx"
            shutil.copytree(args.bundle, bundle)
            subprocess.run(["codesign", "--verify", "--strict", str(bundle)], check=True)
        else:
            bundle = build(root)
        receipt["binary_sha256"] = digest(bundle / "Contents/MacOS/GreenAtlasBridge")
        case = json.loads(args.case.read_bytes())
        if args.groups_from:
            discovery = json.loads(args.groups_from.read_bytes())
            case["targets"] = [dict(route=row["routes"][0], capability="area", additional_routes=row["routes"][1:])
                               for row in discovery["candidates"] if not row["error"]]
            receipt["discovery_sha256"] = digest(args.groups_from)
        query = NativeObjectQuery(request_id=uuid4().hex, source_sha256=digest(drawing), **case)
        encoded = encode_request(query, root / "native.json")
        (root / "request.txt").write_bytes(encoded)
        lines = [f'(setvar "TRUSTEDPATHS" {quoted(str(root) + "/")})',
                 '(setvar "FILEDIA" 0)', f'(arxload {quoted(bundle)})']
        if args.demand_load is not None:
            lines.append(f'(setvar "DEMANDLOAD" {args.demand_load})')
        receipt["preload_modules"] = []
        for name in args.preload_module:
            if Path(name).name != name or not name.endswith((".dbx", ".crx")):
                raise ValueError("Expected one module name under installed Autodesk Plugins")
            module = CORE.parents[4] / "Plugins" / name
            if not module.exists():
                raise ValueError(f"Installed native module not found: {name}")
            lines.append(f'(arxload {quoted(module)})')
            receipt["preload_modules"].append(name)
        if args.mode == "side":
            if args.disable_field_evaluation:
                (root / "field-evaluation-disabled").touch(exist_ok=False)
            if args.inventory:
                (root / "inventory-enabled").touch(exist_ok=False)
            (root / "side-request.txt").write_text(str(drawing) + "\n")
            lines.extend(["GASIDEDBQUERY", str(root / "side-request.txt")])
            drawing = root / "Empty.dwg"
            shutil.copy2(CORE.parents[4] / "Resources/UserDataCache/en-us/Template/acadiso.dwt", drawing)
        else:
            lines.append("GAQUERYOBJECTS")
        lines.append(str(root / "request.txt"))
        if args.discover_capture:
            capture = json.loads(args.discover_capture.read_bytes())
            if capture["source"]["sha256"] != query.source_sha256:
                raise ValueError("Address capture source SHA mismatch")
            routes = sorted({"/".join([*row["instance_chain"], row["handle"]])
                             for kind in ("paths", "regions") for row in capture[kind]
                             if row["layer"] in args.layer})
            if not routes:
                raise ValueError("No native addresses on the explicitly selected layers")
            (root / "discovery.txt").write_text(str(len(routes)) + "\n" + "\n".join(routes) + "\n")
            receipt["address_capture_sha256"] = digest(args.discover_capture)
            receipt["discovery_address_count"] = len(routes)
            receipt["discovery_layers"] = args.layer
            lines += ["GADISCOVERAREAS", str(root / "discovery.txt")]
        lines += [
                  f'(setq gaModules (open {quoted(root / "loaded-modules.txt")} "w")) (foreach gaModule (arx) (write-line gaModule gaModules)) (close gaModules)',
                  f'(setq gaDone (open {quoted(root / "completed")} "w")) (close gaDone)',
                  '_QUIT', '_Y', '']
        (root / "query.scr").write_text("\n".join(lines) + "\n")
        receipt["engine"] = run_core(root, drawing, root / "query.scr", 90)
        reply = decode_reply((root / "native.json").read_bytes(), query, request_bytes=encoded)
        receipt["reply_validated"] = True
        receipt["reply"] = reply.model_dump(mode="json", by_alias=True)
        receipt["side_database_destroyed"] = (root / "side-db-destroyed").is_file()
        if args.mode == "side":
            before = json.loads((root / "side-graph-before.json").read_bytes())
            after = json.loads((root / "side-graph-after.json").read_bytes())
            receipt["xref_graph"] = before
            receipt["xref_graph_unchanged"] = before == after
            if args.inventory:
                receipt["entity_inventory_unchanged"] = (
                    json.loads((root / "entities-before.json").read_bytes())
                    == json.loads((root / "entities-after.json").read_bytes()))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        receipt["error"] = str(error)
    finally:
        receipt["files"] = files
        receipt["sources_unchanged"] = bool(files) and all(
            digest(Path(row["source"])) == row["sha256"] == digest(Path(row["staged"])) for row in files)
        receipt["passed"] = (not receipt.get("error") and receipt["sources_unchanged"]
            and receipt.get("reply_validated") and receipt.get("engine", {}).get("exit_code") == 0
            and receipt["engine"]["commands_completed"] and not receipt["engine"]["owned_process_terminated"]
            and (args.mode != "side" or (receipt["side_database_destroyed"] and receipt["xref_graph_unchanged"]))
            and (not args.inventory or receipt.get("entity_inventory_unchanged") is True))
        (root / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: value for key, value in receipt.items() if key not in {"files", "reply", "xref_graph"}}, ensure_ascii=False), flush=True)
    return int(not receipt["passed"])


if __name__ == "__main__":
    raise SystemExit(main())
