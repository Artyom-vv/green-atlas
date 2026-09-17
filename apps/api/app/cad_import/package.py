"""Prepare one CAD entry and its dependency graph, without modifying originals."""

from collections.abc import Callable
from pathlib import Path

from app.cad_import.cache import file_sha256
from app.cad_import.contracts import CadConversionError
from app.cad_import.conversion import LibreDwgConverter
from app.cad_import.dxf_inspection import DxfInspector
from app.cad_import.package_contracts import (
    PackageDrawing,
    ReferenceOverride,
    SourcePackage,
)
from app.cad_import.reference_overrides import ReferenceOverrides
from app.cad_import.references import PackagePaths

MAX_PACKAGE_DRAWINGS = 64
MAX_PACKAGE_SOURCE_BYTES = 512 * 1024 * 1024


class PackageInspector:
    def __init__(
        self,
        converter: DxfInspector,
        *,
        converter_factory: Callable[[], LibreDwgConverter] | None = None,
    ) -> None:
        self.converter = converter
        self._dwg_converter = converter if isinstance(converter, LibreDwgConverter) else None
        self._converter_factory = converter_factory

    def inspect(
        self, root: Path, entry: Path, overrides: list[ReferenceOverride] | None = None
    ) -> SourcePackage:
        paths = PackagePaths(root)
        if self.converter.cache.is_relative_to(paths.root):
            raise CadConversionError(
                "Кэш CAD должен находиться вне исходного комплекта"
            )
        source = (paths.root / entry).resolve(strict=True)
        manifest = SourcePackage(root=str(paths.root), entry=paths.relative(source))
        resolver = ReferenceOverrides(overrides or [])
        self._visit(source, paths, manifest, set(), resolver)
        if resolver.unused_count:
            manifest.blockers.append(
                f"Не применено назначений ссылок: {resolver.unused_count}"
            )
        if manifest.blockers or any(
            edge.status != "resolved" for edge in manifest.references
        ):
            manifest.status = "blocked"
        return manifest

    def _visit(
        self,
        source: Path,
        paths: PackagePaths,
        manifest: SourcePackage,
        ancestry: set[str],
        overrides: ReferenceOverrides,
    ) -> None:
        relative = paths.relative(source)
        if any(drawing.path == relative for drawing in manifest.drawings):
            return
        if len(manifest.drawings) >= MAX_PACKAGE_DRAWINGS:
            manifest.blockers.append(
                f"Достигнут бюджет {MAX_PACKAGE_DRAWINGS} чертежей в комплекте"
            )
            return
        size = source.stat().st_size
        if (
            size + sum(drawing.source_bytes for drawing in manifest.drawings)
            > MAX_PACKAGE_SOURCE_BYTES
        ):
            manifest.blockers.append("Достигнут бюджет исходных байтов комплекта")
            return
        drawing = PackageDrawing(path=relative, source_bytes=size, status="rejected")
        manifest.drawings.append(drawing)
        try:
            if source.suffix.lower() == ".dwg":
                # Native DXF must not initialize an optional executable or its
                # DLLs, even if an old DWG converter path remains configured.
                if self._dwg_converter is None and self._converter_factory is not None:
                    self._dwg_converter = self._converter_factory()
                if self._dwg_converter is None:
                    raise CadConversionError(
                        "Комплект содержит DWG. Подготовьте этот чертёж в DXF "
                        "и назначьте соответствующую внешнюю ссылку."
                    )
                result = self._dwg_converter.convert(source)
                drawing.source_sha256 = result.evidence.source_sha256
                drawing.normalized_path = str(result.path)
                drawing.evidence_path = str(result.evidence_path)
                drawing.inspection = result.evidence.inspection
                if drawing.inspection.boundary_catalog is None:
                    # Extend old inspection evidence without reconverting or
                    # rewriting the immutable native conversion manifest.
                    drawing.inspection = self.converter.inspect_dxf(result.path)
            else:
                # Inspect a native DXF with the same isolated parser and budgets.
                drawing.source_sha256 = file_sha256(source)
                drawing.inspection = self.converter.inspect_dxf(source)
                if file_sha256(source) != drawing.source_sha256:
                    raise CadConversionError("Исходный DXF изменился во время проверки")
                drawing.normalized_path = str(source)
            drawing.status = "readable"
        except (OSError, ValueError) as error:
            drawing.message = str(error)
            manifest.blockers.append(f"Не прочитан {relative}")
            return
        assert drawing.inspection is not None
        for block, reference in drawing.inspection.xrefs.items():
            edge, target = paths.resolve(source, block, reference)
            target = overrides.resolve(paths, edge, target)
            manifest.references.append(edge)
            if target is None:
                continue
            if edge.target in ancestry | {relative}:
                edge.status = "cycle"
                continue
            self._visit(target, paths, manifest, ancestry | {relative}, overrides)


def write_package(manifest: SourcePackage, output: Path) -> None:
    output = output.resolve()
    if output.is_relative_to(Path(manifest.root)):
        raise CadConversionError(
            "Отчёт комплекта должен находиться вне исходного набора"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
