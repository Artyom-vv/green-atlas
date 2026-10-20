"""Сборочный CLI состава поставки; check только читает файлы и не запускает приложение."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "apps/api"))


def main() -> int:
    from app.distribution.contracts import (
        PackageLayout,
        PackageManifest,
        QualificationEvidence,
    )
    from app.distribution.preflight import build_manifest, check_package, macos_layout

    parser = argparse.ArgumentParser(
        description="Проверка собственного desktop payload"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--package", type=Path, required=True)
    build.add_argument("--source-root", type=Path, required=True)
    build.add_argument("--source-manifest", type=Path, required=True)
    build.add_argument("--platform", choices=("macos", "windows"), default="macos")
    build.add_argument(
        "--layout", type=Path, help="Явный layout; без него читается Mac bundle"
    )
    build.add_argument(
        "--qualification", type=Path, help="Отдельная квитанция реальной приёмки"
    )
    build.add_argument("--output", type=Path, required=True)
    check = commands.add_parser("check")
    check.add_argument("--package", type=Path, required=True)
    check.add_argument("--manifest", type=Path, required=True)
    check.add_argument("--discover-external", action="store_true")
    source = commands.add_parser("check-source")
    source.add_argument("--source-root", type=Path, required=True)
    source.add_argument("--source-manifest", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "check-source":
            from app.distribution.preflight import SourceReceipt, _verify_source

            sealed = args.source_manifest.read_bytes()
            rows = _verify_source(args.source_root, SourceReceipt.model_validate_json(sealed))
            print(json.dumps({
                "source_valid": True, "verified_files": len(rows),
                "source_manifest_sha256": hashlib.sha256(sealed).hexdigest(),
                "windows_execution_qualified": False,
            }))
            return 0
        if args.command == "build":
            from app.distribution.windows_layout import windows_layout

            layout = (
                PackageLayout.model_validate_json(args.layout.read_bytes())
                if args.layout
                else windows_layout(args.package)
                if args.platform == "windows"
                else macos_layout(args.package)
            )
            evidence = (
                QualificationEvidence.model_validate_json(
                    args.qualification.read_bytes()
                )
                if args.qualification
                else None
            )
            manifest = build_manifest(
                args.package,
                layout,
                source_root=args.source_root,
                source_manifest=args.source_manifest,
                qualification=evidence,
            )
            # Builder пишет только явно выбранный новый manifest; существующий не заменяет.
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(manifest.model_dump_json(indent=2, by_alias=True) + "\n")
            report = check_package(args.package, manifest)
        else:
            manifest = PackageManifest.model_validate_json(args.manifest.read_bytes())
            report = check_package(
                args.package, manifest, discover_external=args.discover_external
            )
        print(report.model_dump_json(indent=2, by_alias=True))
        return 0
    except (OSError, ValueError, KeyError) as error:
        print(
            json.dumps(
                {"payload_valid": False, "error": str(error)}, ensure_ascii=True
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
