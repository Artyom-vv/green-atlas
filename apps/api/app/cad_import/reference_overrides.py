"""Explicit, fingerprinted relocation decisions; never guess a same-named drawing."""

from pathlib import Path

from app.cad_import.cache import file_sha256
from app.cad_import.contracts import CadConversionError
from app.cad_import.package_contracts import PackageReference, ReferenceOverride
from app.cad_import.references import PackagePaths, path_key


class ReferenceOverrides:
    def __init__(self, items: list[ReferenceOverride]) -> None:
        self._items: dict[tuple[str, str], ReferenceOverride] = {}
        self._used: set[tuple[str, str]] = set()
        for item in items:
            key = (path_key(item.owner), item.block)
            if key in self._items:
                raise CadConversionError("Повторное назначение одной внешней ссылки")
            self._items[key] = item

    @property
    def unused_count(self) -> int:
        return len(self._items.keys() - self._used)

    def resolve(
        self, paths: PackagePaths, edge: PackageReference, original: Path | None
    ) -> Path | None:
        key = (path_key(edge.owner), edge.block)
        item = self._items.get(key)
        if item is None:
            return original
        target = (paths.root / item.target).resolve(strict=True)
        if not target.is_relative_to(paths.root):
            raise CadConversionError(
                "Переназначенная ссылка находится вне исходного комплекта"
            )
        if file_sha256(target) != item.expected_sha256:
            raise CadConversionError(
                f"Содержимое переназначенной ссылки изменилось: {item.target}"
            )
        self._used.add(key)
        edge.status = "resolved"
        edge.target = paths.relative(target)
        edge.resolution = "explicit_override"
        edge.expected_sha256 = item.expected_sha256
        edge.resolution_reason = item.reason
        return target
