"""Copy the selected official CAD files without conversion; stdlib-only.

This prepares a development fixture, not a product DWG importer. ZIP names
without the UTF-8 flag use CP866 in this particular official archive.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import stat
import tempfile
import unicodedata
import zipfile


PREFIX = "Пилотный проект 20 улиц/18. Кустанайская улица/Исходные данные/"
SCHEMA = "green-atlas-kustanayskaya-fixture-v1"


def decoded_name(info: zipfile.ZipInfo) -> str:
    if info.flag_bits & 0x800:
        return info.filename
    try:
        return info.filename.encode("cp437").decode("cp866")
    except UnicodeEncodeError:
        # A Unicode extra field may already have supplied the actual name,
        # even when the legacy header does not carry the UTF-8 flag.
        return info.filename


def safe_relative(name: str) -> Path:
    value = PurePosixPath(name)
    if value.is_absolute() or not value.parts or any(
        part in ("..", ".") or "\\" in part or ":" in part for part in value.parts
    ):
        raise ValueError(f"Unsafe fixture path: {name!r}")
    return Path(*value.parts)


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_source(source_directory: Path, manifest_path: Path) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["schema"] != SCHEMA:
        raise ValueError("Unknown fixture schema")
    expected = set()
    for record in manifest["files"]:
        relative = safe_relative(record["path"])
        if relative.parts[0] != "source":
            raise ValueError("Expected source/ file")
        path = source_directory / Path(*relative.parts[1:])
        if not path.resolve().is_relative_to(source_directory.resolve()):
            raise ValueError(f"Escaping fixture path: {relative}")
        if path.is_symlink() or path.stat().st_size != record["bytes"]:
            raise ValueError(f"Invalid size or symlink: {relative}")
        if file_digest(path) != record["sha256"]:
            raise ValueError(f"Hash mismatch: {relative}")
        if relative.as_posix() in expected:
            raise ValueError(f"Duplicate manifest path: {relative}")
        expected.add(relative.as_posix())
    if len(expected) != 10:
        raise ValueError("Expected exactly ten declared CAD files")
    return {"files_verified": len(expected), "bytes": sum(f["bytes"] for f in manifest["files"])}


def verify(destination: Path) -> dict:
    result = verify_source(destination / "source", destination / "manifest.json")
    manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    expected = {record["path"] for record in manifest["files"]}
    actual = {
        p.relative_to(destination).as_posix()
        for p in (destination / "source").rglob("*") if p.is_file()
    }
    if actual != expected or len(expected) != 10:
        raise ValueError("Fixture must contain exactly the ten declared CAD files")
    return result


def extract(archive: Path, destination: Path) -> dict:
    if (destination / "source").exists() or (destination / "manifest.json").exists():
        raise ValueError("Fixture already exists; use --verify. Existing files are never overwritten.")
    with zipfile.ZipFile(archive) as source:
        chosen = []
        omitted = []
        seen = set()
        for info in source.infolist():
            name = decoded_name(info)
            if info.is_dir() or not name.startswith(PREFIX) or "PaxHeader/" in name:
                continue
            relative = safe_relative(name[len(PREFIX):])
            if relative.suffix.lower() not in (".dwg", ".dxf"):
                omitted.append({"archive_path": name, "bytes": info.file_size})
                continue
            key = unicodedata.normalize("NFC", relative.as_posix()).casefold()
            if key in seen or stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError(f"Duplicate path or symlink: {name}")
            if info.file_size > 128 * 1024 * 1024:
                raise ValueError(f"Unexpectedly large member: {name}")
            seen.add(key)
            chosen.append((name, relative, info))
        if len(chosen) != 10:
            raise ValueError(f"Unexpected archive edition: expected 10 CAD files, found {len(chosen)}")
        destination.mkdir(parents=True, exist_ok=True)
        records = []
        with tempfile.TemporaryDirectory(prefix="fixture-stage-", dir=destination) as temporary:
            staging = Path(temporary) / "source"
            for name, relative, info in sorted(chosen, key=lambda row: row[0]):
                target = staging / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                written = 0
                with source.open(info) as reader, target.open("xb") as writer:
                    for block in iter(lambda: reader.read(1024 * 1024), b""):
                        written += len(block)
                        if written > info.file_size:
                            raise ValueError(f"Unexpected decompressed size: {name}")
                        digest.update(block)
                        writer.write(block)
                if written != info.file_size:
                    raise ValueError(f"Truncated member: {name}")
                records.append({"path": "source/" + relative.as_posix(),
                                "archive_path": name, "bytes": written,
                                "sha256": digest.hexdigest()})
            staging.rename(destination / "source")
    manifest = {
        "schema": SCHEMA,
        "street": "Кустанайская улица",
        "dataset_archive": archive.name,
        "archive_prefix": PREFIX,
        "scope": "All 10 direct DWG/DXF files under this street's Исходные данные; not the entire dataset or dependency closure.",
        "format_conversion_performed": False,
        "cad_dependency_closure_verified": False,
        "files": records,
        "omitted_non_cad_source_entries": sorted(omitted, key=lambda row: row["archive_path"]),
    }
    with (destination / "manifest.json").open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return verify(destination)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--archive", type=Path)
    mode.add_argument("--verify", type=Path, metavar="FIXTURE_DIRECTORY")
    mode.add_argument("--verify-source", type=Path, metavar="SOURCE_DATA_DIRECTORY")
    parser.add_argument("--manifest", type=Path, default=Path("fixtures/cad/kustanayskaya/manifest.json"))
    parser.add_argument("--destination", type=Path, default=Path("fixtures/cad/kustanayskaya"))
    args = parser.parse_args()
    if args.verify_source:
        result = verify_source(args.verify_source, args.manifest)
    else:
        result = verify(args.verify) if args.verify else extract(args.archive, args.destination)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
