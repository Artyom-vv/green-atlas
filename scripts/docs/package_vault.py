"""Package Markdown and diagrams, then validate a standalone extracted copy."""

from __future__ import annotations

import argparse
import hashlib
import tempfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from check_vault import check


def package(vault: Path, destination: Path) -> dict[str, int]:
    vault = vault.resolve()
    counts = check(vault)
    files = sorted(p for p in vault.rglob("*") if p.suffix in {".md", ".svg"})
    manifest = "".join(
        f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(vault).as_posix()}\n"
        for p in files
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(destination, "w", compression=ZIP_DEFLATED) as archive:
        for file in files:
            archive.write(file, Path("obsidian") / file.relative_to(vault))
        archive.writestr("obsidian/MANIFEST.sha256", manifest)
    with ZipFile(destination) as archive:
        if archive.testzip() is not None:
            raise ValueError("Archive integrity check failed")
        expected = {f"obsidian/{p.relative_to(vault).as_posix()}" for p in files}
        expected.add("obsidian/MANIFEST.sha256")
        if set(archive.namelist()) != expected:
            raise ValueError("Unexpected archive members")
        with tempfile.TemporaryDirectory(prefix="ga-docs-verify-") as temporary:
            archive.extractall(temporary)
            extracted = Path(temporary) / "obsidian"
            if check(extracted) != counts:
                raise ValueError("Standalone validation differs")
            for line in manifest.splitlines():
                digest, relative = line.split("  ", 1)
                if hashlib.sha256((extracted / relative).read_bytes()).hexdigest() != digest:
                    raise ValueError(f"Content mismatch: {relative}")
    return counts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", type=Path, default=Path("docs/obsidian"))
    parser.add_argument("--output", type=Path, default=Path("output/documentation/Green-Atlas-documentation.zip"))
    args = parser.parse_args()
    print(package(args.vault, args.output))
    print(args.output)
