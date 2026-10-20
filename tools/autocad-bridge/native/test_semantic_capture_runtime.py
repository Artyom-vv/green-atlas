"""Explicit, bounded AutoCAD Core acceptance on tiny synthetic DWGs.

Run this file with --production-library and --output. It never loads a plugin
into the user's editor or writes an application project. Normal pytest skips
the Core run; the pure catalogue tests cover bounded retention separately.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import plistlib
import shutil
import signal
import subprocess
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
NATIVE = Path(__file__).parent
ACAD = Path("/Applications/Autodesk/AutoCAD 2027/AutoCAD 2027.app")
CORE = ACAD / "Contents/Helpers/AcCoreConsole.app/Contents/MacOS/AcCoreConsole"


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def dump(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def build(root: Path) -> Path:
    bundle = root / "SemanticFixture.dbx"
    binary = bundle / "Contents/MacOS/SemanticFixture"
    binary.parent.mkdir(parents=True)
    with (bundle / "Contents/Info.plist").open("xb") as stream:
        plistlib.dump(
            {
                "CFBundleExecutable": "SemanticFixture",
                "CFBundleIdentifier": "ru.green-atlas.semantic-fixture-test",
                "CFBundleName": "Semantic Fixture Test",
                "CFBundlePackageType": "BNDL",
                "CFBundleVersion": "1",
            },
            stream,
        )
    frameworks = ACAD / "Contents/Frameworks"
    subprocess.run(
        [
            "xcrun",
            "clang++",
            "-std=c++17",
            "-arch",
            "x86_64",
            "-bundle",
            "-O1",
            "-mmacosx-version-min=14.0",
            "-DNDEBUG",
            "-D_ADESK_MAC_",
            "-DOSX_SYSTEM",
            "-D_NATIVE_WCHAR_T_DEFINED",
            "-DUNICODE",
            "-DACDB_EXT",
            "-Wno-deprecated-declarations",
            "-Wno-nonportable-include-path",
            "-Wno-extra-tokens",
            "-include",
            str(NATIVE / "prefix.pch"),
            "-I",
            str(REPO / ".local/objectarx-2027/inc"),
            "-I",
            str(NATIVE),
            "-L",
            str(frameworks),
            "-F",
            str(frameworks),
            "-lacfirst",
            "-lwinapi",
            "-lacdb",
            "-laccore",
            "-lgelib",
            "-lAcPal",
            str(NATIVE / "test_semantic_capture_fixture.cpp"),
            str(NATIVE / "file_io.cpp"),
            "-o",
            str(binary),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["codesign", "--force", "--sign", "-", str(bundle)],
        check=True,
        capture_output=True,
        text=True,
    )
    return bundle


def stop(child: subprocess.Popen[bytes]) -> None:
    if child.poll() is not None:
        return
    os.killpg(child.pid, signal.SIGTERM)
    try:
        child.wait(timeout=3)
    except subprocess.TimeoutExpired:
        os.killpg(child.pid, signal.SIGKILL)
        child.wait()


def run(
    root: Path, bundle: Path, library: Path, symbol: str, action: str, overflow: bool
) -> dict:
    stage = root / action
    stage.mkdir()
    profile = stage / "profile"
    profile.mkdir()
    bootstrap = stage / "Bootstrap.dwg"
    shutil.copyfile(
        ACAD / "Contents/Resources/UserDataCache/en-us/Template/acadiso.dwt", bootstrap
    )
    script = stage / "run.scr"
    script.write_text(
        '(setvar "FILEDIA" 0)\n(setvar "APPAUTOLOAD" 0)\n(setvar "SECURELOAD" 2)\n'
        '(setvar "LEGACYCODESEARCH" 0)\n(setvar "ACADLSPASDOC" 0)\n'
        '(setvar "TRUSTEDPATHS" (getenv "GA_SEMANTIC_TRUSTED"))\n'
        '(arxload (getenv "GA_SEMANTIC_BUNDLE"))\n'
        "GASEMANTICFIXTURE\n_QUIT\n_Y\n\n",
        encoding="ascii",
    )
    env = dict(
        os.environ,
        GA_SEMANTIC_ROOT=str(root),
        GA_SEMANTIC_ACTION=action,
        GA_SEMANTIC_BUNDLE=str(bundle),
        GA_SEMANTIC_LIBRARY=str(library),
        GA_SEMANTIC_ARCHIVE_SYMBOL=symbol,
        GA_SEMANTIC_OVERFLOW="1" if overflow else "0",
        GA_SEMANTIC_TRUSTED=str(root) + "/...;" + str(library.parent) + "/...",
    )
    started = time.monotonic()
    with (stage / "core.log").open("xb") as log:
        child = subprocess.Popen(
            [
                "/usr/bin/arch",
                "-x86_64",
                str(CORE),
                "/i",
                str(bootstrap),
                "/s",
                str(script),
                "/isolate",
                "ga-semantic-" + uuid.uuid4().hex,
                str(profile),
            ],
            cwd=stage,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            child.wait(timeout=120)
        finally:
            stop(child)
    result = {
        "exit_code": child.returncode,
        "seconds": round(time.monotonic() - started, 3),
    }
    dump(stage / "engine.json", result)
    error = root / (action + "-error.json")
    if error.exists():
        raise RuntimeError(json.loads(error.read_bytes())["error"])
    if child.returncode or not (root / (action + "-complete.json")).exists():
        raise RuntimeError(f"Core {action} failed; inspect {stage / 'core.log'}")
    return result


def transformed(
    point: tuple[float, float],
    position: tuple[float, float],
    angle: float,
    scale: tuple[float, float],
) -> tuple[float, float]:
    x, y = point[0] * scale[0], point[1] * scale[1]
    return (
        position[0] + x * math.cos(angle) - y * math.sin(angle),
        position[1] + x * math.sin(angle) + y * math.cos(angle),
    )


def same(actual: list[float], expected: tuple[float, float]) -> bool:
    return (
        len(actual) == 3
        and abs(actual[2]) < 1e-10
        and all(
            math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-8)
            for a, b in zip(actual[:2], expected)
        )
    )


def evaluate(root: Path, before: dict[str, str], overflow: bool) -> dict[str, bool]:
    receipt = json.loads((root / "Native/session.json").read_bytes())
    instances = json.loads((root / "Native/instances.json").read_bytes())
    semantic = json.loads((root / "Native/semantics.json").read_bytes())
    rows = semantic["rows"]
    display = json.loads((root / "Drawing.autocad.json").read_bytes())
    inventory = json.loads((root / "Drawing.autocad.json.inventory.json").read_bytes())
    coverage = {
        "/".join((*row["instance_chain"], row["handle"])): row
        for row in display["coverage"]
    }
    indexed = {row["route"]: row for row in inventory["objects"]}
    archived = {row["source_route"]: row for row in instances}
    table_routes = {
        route for route, row in coverage.items() if row["entity_type"] == "AcDbTable"
    }
    annotation_routes = {
        route
        for route in coverage
        if any(route.startswith(owner + "/") for owner in table_routes)
    }
    annotation_geometry = [
        row
        for key in ("paths", "points", "regions", "area_proposals")
        for row in display.get(key, [])
        if "/".join((*row.get("instance_chain", ()), row["handle"]))
        in annotation_routes
    ]
    values = {}
    for row in rows:
        values.setdefault(row["value"], []).append(row)
    identity_keys = (
        "source_route",
        "archive_route",
        "archive_file",
        "class",
        "authored_layer",
        "effective_layer",
        "transform",
        "unavailable",
    )
    expected_inner = [
        transformed(transformed((1, 2), (10, 20), 0.15, (1, 1)), pos, angle, scale)
        for pos, angle, scale in [
            ((3, 3), 0.3, (0.8, 0.8)),
            ((30, 40), -0.2, (1.2, 1.2)),
        ]
    ]
    expected_attribute_local = transformed((0.25, 0.5), (10, 20), 0.15, (1, 1))
    expected_attributes = [
        transformed(expected_attribute_local, pos, angle, scale)
        for pos, angle, scale in [
            ((3, 3), 0.3, (0.8, 0.8)),
            ((30, 40), -0.2, (1.2, 1.2)),
        ]
    ]
    expected_water = [(102, 203), transformed((2, 3), (-50, 60), 0.35, (2, 0.5))]
    expected_lawn = [
        (3, 4),
        (121, 232),
        transformed((21, 32), (-50, 60), 0.35, (2, 0.5)),
    ]

    def anchors(value, expected):
        group = values.get(value, [])
        return len(group) == len(expected) and all(
            sum(same(row["anchor_wcs"], point) for row in group) == 1
            for point in expected
        )

    checks = {
        "source_files_preserved": all(
            digest(root / "source" / name) == sha for name, sha in before.items()
        ),
        "session3": receipt["schema"] == "green-atlas.native-session/3",
        "semantic_digest": receipt["semantics_sha256"]
        == digest(root / "Native/semantics.json"),
        "semantic_size": receipt["semantics_bytes"]
        == (root / "Native/semantics.json").stat().st_size,
        "semantic_instance_binding": semantic["instances_sha256"]
        == receipt["instances_sha256"]
        == digest(root / "Native/instances.json"),
        "semantic_display_binding": semantic["display_sha256"]
        == receipt["display_sha256"]
        == digest(root / "Drawing.autocad.json"),
        "exact_row_identity": all(
            all(
                row[key] == instances[row["instance_index"]][key]
                for key in identity_keys
            )
            for row in rows
        ),
        "unused_definition_absent": "SOURCE_ONLY_UNUSED" not in values,
        "repeated_layer0_anchors": anchors("INNER_LABEL", expected_inner),
        "layer0_inheritance": all(
            row["authored_layer"] == "0" and row["effective_layer"] == "DESIGN"
            for row in values.get("INNER_LABEL", [])
        ),
        "actual_attribute_anchors": anchors("d=250", expected_attributes),
        "attribute_owner_coordinates": all(
            same(row["anchor_local"], expected_attribute_local)
            for row in values.get("d=250", [])
        ),
        "actual_attribute_origin": all(
            row["origin"] == "attribute_instance_value"
            for row in values.get("d=250", [])
        ),
        "default_origin_separated": len(values.get("d=999", [])) == 2
        and all(
            row["origin"] == "attribute_definition_default" for row in values["d=999"]
        ),
        "transformed_repeated_xref": anchors("d=400ст.", expected_water),
        "nested_xref_anchors": anchors("ГАЗОН", expected_lawn),
        "nested_xref_own_file": len(
            {
                row["archive_file"]
                for row in values.get("ГАЗОН", [])
                if row["archive_file"] != "host.dwg"
            }
        )
        == 1
        and sum(row["archive_file"] != "host.dwg" for row in values.get("ГАЗОН", []))
        == 2,
        "overflow_addressed": len(
            [row for row in rows if row["status"] == "value_limit"]
        )
        == int(overflow)
        and all(
            row["value"] == ""
            and row["tag"] == ""
            and row["anchor_local"] is None
            and row["anchor_wcs"] is None
            for row in rows
            if row["status"] == "value_limit"
        ),
        "completeness_honest": semantic["complete"] is not overflow
        and semantic["eligible_count"] == semantic["emitted_count"] == len(rows)
        and sum(
            row["source_route"] not in annotation_routes
            and row["value"] not in {"TABLE_NOTE", "TABLE_DEFAULT"}
            for row in rows
        )
        == 11 + int(overflow)
        and semantic["omitted_count"] == 0,
        "annotation_subtree_retained": len(table_routes) == 1
        and len(annotation_routes) > 5
        and {coverage[route]["entity_type"] for route in annotation_routes}
        >= {"AcDbLine", "AcDbPolyline", "AcDbPoint", "AcDbBlockReference"},
        "annotation_has_no_calculation_shapes": not annotation_geometry
        and all(coverage[route]["status"] == "context" for route in annotation_routes),
        "full_capture_identity": set(coverage) == set(indexed) == set(archived)
        and all(
            indexed[route]["context"] == (row["status"] == "context")
            and indexed[route]["entity_type"]
            == archived[route]["class"]
            == row["entity_type"]
            and indexed[route]["layer"]
            == archived[route]["effective_layer"]
            == row["layer"]
            and archived[route]["authored_layer"] == row["source_layer"]
            for route, row in coverage.items()
        ),
        "annotation_semantics_preserved": any(
            row["source_route"] in annotation_routes and row["value"] == "TABLE_NOTE"
            for row in rows
        )
        and any(
            row["source_route"] in annotation_routes
            and row["value"] == "TABLE_DEFAULT"
            and row["origin"] == "attribute_definition_default"
            for row in rows
        ),
        "same_definition_outside_annotation_is_native": all(
            len(
                [
                    row
                    for row in display[key]
                    if row["handle"]
                    in {
                        coverage[route]["handle"]
                        for route in annotation_routes
                        if coverage[route]["entity_type"] == entity_type
                    }
                ]
            )
            == 1
            for key, entity_type in (
                ("paths", "AcDbLine"),
                ("paths", "AcDbPolyline"),
                ("points", "AcDbPoint"),
            )
        ),
        "source_map_has_geometry": any(
            row["layer"] == "L009" and row["native_area_units2"] == 35
            for row in display["regions"]
        )
        and any(row["layer"] == "SITE" and row["closed"] for row in display["paths"])
        and any(
            row["layer"] == "WATER" and not row["closed"] for row in display["paths"]
        ),
    }
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--production-library", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--include-overflow", action="store_true")
    args = parser.parse_args()
    library, root = args.production_library.resolve(strict=True), args.output.resolve()
    if not library.is_relative_to(REPO / ".runtime/autocad-bridge/macos"):
        parser.error("Use a native product build in this checkout")
    if not root.is_relative_to(REPO / "output") or not root.name.startswith(
        "native-semantic-"
    ):
        parser.error("Use a new output/native-semantic-... directory")
    root.mkdir(parents=True, exist_ok=False)
    if shutil.disk_usage(root).free < 2 * 1024**3:
        raise RuntimeError("Less than 2 GiB free; no Core test")
    qualified = {
        "installed_product_changed": False,
        "library_sha256": digest(library),
        "authored_ground_truth": {
            "SITE": {
                "role": "site_boundary",
                "geometry": "closed_polyline",
                "area_units2": 144,
            },
            "WATER": {
                "role": "water_network_axis",
                "geometry": "line",
                "installation": "unknown",
                "extent": "unknown",
            },
            "L009": {
                "role": "lawn",
                "geometry": "hatch",
                "area_units2": 35,
                "annotation": "ГАЗОН",
            },
        },
    }
    try:
        symbols = subprocess.run(
            ["nm", "-gU", str(library)], check=True, capture_output=True, text=True
        ).stdout
        candidates = {
            line.split()[-1]
            for line in symbols.splitlines()
            if "capturePackage" in line
        }
        if len(candidates) != 1:
            raise RuntimeError("Exactly one production archive function required")
        # Mach-O nm includes the linker underscore; dlsym uses the ABI name.
        symbol = candidates.pop().removeprefix("_")
        bundle = build(root)
        qualified["seed"] = run(
            root, bundle, library, symbol, "seed", args.include_overflow
        )
        before = {path.name: digest(path) for path in (root / "source").glob("*.dwg")}
        qualified["source_sha256"] = before
        qualified["source_bytes"] = sum(
            path.stat().st_size for path in (root / "source").glob("*.dwg")
        )
        qualified["capture"] = run(
            root, bundle, library, symbol, "capture", args.include_overflow
        )
        qualified["checks"] = evaluate(root, before, args.include_overflow)
        qualified["passed"] = all(qualified["checks"].values())
    except Exception as error:
        qualified["passed"] = False
        qualified["error"] = str(error)
        if isinstance(error, subprocess.CalledProcessError) and error.stderr:
            (root / "build-error.log").write_text(error.stderr, encoding="utf-8")
            print(error.stderr[-4000:], flush=True)
        raise
    finally:
        dump(root / "receipt.json", qualified)
        print(json.dumps(qualified, ensure_ascii=False), flush=True)
    return int(not qualified["passed"])


if __name__ == "__main__":
    raise SystemExit(main())
