"""python -m app.cad_import convert FILE --converter EXE --cache DIR."""

import argparse
import io
import json
import sys
from pathlib import Path

from pydantic import TypeAdapter

from app.cad_import.conversion import LibreDwgConverter
from app.cad_import.package import PackageInspector, write_package
from app.cad_import.package_contracts import ReferenceOverride


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="Reproducible CAD intake; originals stay untouched"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    convert = commands.add_parser(
        "convert", help="Convert a DWG and retain conversion evidence"
    )
    convert.add_argument("source", type=Path)
    convert.add_argument("--converter", type=Path, required=True)
    convert.add_argument("--cache", type=Path, required=True)
    package = commands.add_parser(
        "inspect-package", help="Check one CAD entry and all relative XREF dependencies"
    )
    package.add_argument("root", type=Path)
    package.add_argument("--entry", type=Path, required=True)
    package.add_argument("--output", type=Path, required=True)
    package.add_argument("--converter", type=Path, required=True)
    package.add_argument("--cache", type=Path, required=True)
    package.add_argument(
        "--xref-map",
        type=Path,
        help="Explicit reference overrides with SHA-256 and reasons",
    )
    args = parser.parse_args()
    try:
        converter = LibreDwgConverter(args.converter, args.cache)
        if args.command == "inspect-package":
            overrides = (
                TypeAdapter(list[ReferenceOverride]).validate_json(
                    args.xref_map.read_bytes()
                )
                if args.xref_map
                else []
            )
            manifest = PackageInspector(converter).inspect(
                args.root, args.entry, overrides
            )
            write_package(manifest, args.output)
            print(
                json.dumps(
                    {
                        "manifest": str(args.output.resolve()),
                        "status": manifest.status,
                        "drawings": len(manifest.drawings),
                        "references": len(manifest.references),
                        "blockers": manifest.blockers,
                        "calculation_ready": manifest.calculation_ready,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 2 if manifest.status == "blocked" else 0
        result = converter.convert(args.source)
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "path": str(result.path),
                "evidence": str(result.evidence_path),
                "cache_hit": result.cache_hit,
                "integrity": result.evidence.integrity,
                "diagnostics": len(result.evidence.diagnostics),
                "entities": result.evidence.inspection.modelspace_entities,
                "xrefs": result.evidence.inspection.xrefs,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
