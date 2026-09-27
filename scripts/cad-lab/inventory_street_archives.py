"""Inspect CAD members separately in each supplied archive; keep original files intact.

ZIP uses its UTF8 flag or the package's DOS Cyrillic legacy encoding. Other
archives use Windows' existing libarchive, without extracting paths to disk.
Only content-addressed CAD bytes and nested archives are written on D.
"""

import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys
import zipfile

CAD = {".dwg", ".dxf"}
ARCHIVES = {".zip", ".7z", ".rar"}
MAX_MEMBER = 512 * 1024 * 1024
MAX_DEPTH = 5


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def usable(name: str) -> bool:
    path = PurePosixPath(name.replace("\\", "/"))
    return not (path.is_absolute() or ".." in path.parts or any(
        p.casefold().startswith(("paxheader", "__macosx", "._")) for p in path.parts))


def main(manifest_path: Path):
    directory = manifest_path.parent.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf8"))
    source_root = Path(manifest["root"])
    known = {r["sha256"]: str(source_root / r["path"]) for r in manifest["files"]}
    content = directory / "archive-content"
    content.mkdir(exist_ok=True)
    archives = []

    def process(source: Path, street: str, logical: str, depth: int):
        record = {"street": street, "path": logical, "sha256": digest(source.read_bytes()), "members": []}
        archives.append(record)
        if depth > MAX_DEPTH:
            record["error"] = "Archive nesting exceeds diagnostic policy"
            return
        package = f"{logical}.contents"

        def accept(name: str, data: bytes):
            if len(data) > MAX_MEMBER:
                raise ValueError("Archive member exceeds diagnostic byte policy")
            path = PurePosixPath(name.replace("\\", "/"))
            sha = digest(data)
            member_path = f"{package}/{path}"
            if path.suffix.casefold() in CAD:
                target = Path(known[sha]) if sha in known else content / f"{sha}{path.suffix.casefold()}"
                if sha not in known:
                    target.write_bytes(data)
                    known[sha] = str(target)
                row = {"street": street, "path": member_path, "source_path": str(target),
                       "bytes": len(data), "sha256": sha, "signature": data[:6].decode("ascii", errors="replace"),
                       "role_hint": "archive_revision", "archive": logical, "archive_sha256": record["sha256"],
                       "member": str(path)}
                manifest["files"].append(row)
                record["members"].append({"member": str(path), "bytes": len(data), "sha256": sha})
            else:
                nested = content / f"{sha}{path.suffix.casefold()}"
                if not nested.exists():
                    nested.write_bytes(data)
                process(nested, street, member_path, depth + 1)

        try:
            if zipfile.is_zipfile(source):
                with zipfile.ZipFile(source, metadata_encoding="cp866") as archive:
                    for member in archive.infolist():
                        if not usable(member.filename) or PurePosixPath(member.filename).suffix.casefold() not in CAD | ARCHIVES:
                            continue
                        if member.file_size > MAX_MEMBER:
                            raise ValueError("Archive member exceeds diagnostic byte policy")
                        accept(member.filename, archive.read(member))
            else:
                listing = subprocess.run(["tar", "-tf", str(source)], capture_output=True, timeout=120, check=True)
                try:
                    names = listing.stdout.decode("utf8")
                except UnicodeDecodeError:
                    names = listing.stdout.decode("mbcs")
                for name in names.splitlines():
                    if usable(name) and PurePosixPath(name).suffix.casefold() in CAD | ARCHIVES:
                        # bsdtar treats member arguments as globs, including the
                        # square brackets in official output[1-11].dwg names.
                        pattern = "".join(f"[{c}]" if c in "[*?" else c for c in name)
                        run = subprocess.run(["tar", "-xOf", str(source), "--", pattern], capture_output=True, timeout=120, check=True)
                        accept(name, run.stdout)
        except Exception as error:
            record["error"] = str(error)
        print(json.dumps({"archive": logical, "cad": len(record["members"]), "error": record.get("error")}, ensure_ascii=False), flush=True)

    for street in manifest["streets"]:
        for relative in street["archives"]:
            process(source_root / relative, street["street"], relative, 0)
    (directory / "archive-inventory.json").write_text(json.dumps(archives, ensure_ascii=False, indent=2), encoding="utf8")
    (directory / "manifest-with-archives.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf8")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
