"""Resolve XREFs within an explicit package, preserving the owner's path context."""

import posixpath
import unicodedata
from pathlib import Path, PureWindowsPath

from app.cad_import.package_contracts import PackageReference


def path_key(value: str) -> str:
    parts = []
    for part in value.replace("\\", "/").split("/"):
        normalized = unicodedata.normalize("NFC", part).rstrip(" ")
        parts.append(
            normalized
            if normalized in {".", ".."}
            else normalized.rstrip(".").casefold()
        )
    return posixpath.normpath("/".join(parts))


class PackagePaths:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve(strict=True)
        self._index: dict[str, list[Path]] = {}
        for path in sorted(self.root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in {".dwg", ".dxf"}:
                continue
            if any(part.casefold().startswith("paxheader") for part in path.parts):
                continue
            if not path.resolve().is_relative_to(self.root):
                continue
            key = path_key(path.relative_to(self.root).as_posix())
            self._index.setdefault(key, []).append(path)

    def relative(self, path: Path) -> str:
        return path.resolve(strict=True).relative_to(self.root).as_posix()

    def resolve(
        self, owner: Path, block: str, reference: str
    ) -> tuple[PackageReference, Path | None]:
        origin = self.relative(owner)
        edge = PackageReference(
            owner=origin, block=block, requested_path=reference, status="missing"
        )
        windows_path = PureWindowsPath(reference)
        if windows_path.drive or reference.startswith(("/", "\\")):
            edge.status = "outside_package"
            return edge, None
        key = path_key(
            posixpath.join(posixpath.dirname(origin), reference.replace("\\", "/"))
        )
        if key == ".." or key.startswith("../"):
            edge.status = "outside_package"
            return edge, None
        matches = self._index.get(key, [])
        if len(matches) > 1:
            edge.status = "ambiguous"
        elif matches:
            edge.status = "resolved"
            edge.target = self.relative(matches[0])
            return edge, matches[0]
        # A matching basename elsewhere is not sufficient: street copies can
        # contain different geometry and relative dependencies.
        return edge, None
