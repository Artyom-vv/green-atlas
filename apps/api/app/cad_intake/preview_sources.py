"""Verify compact selections against the private immutable intake manifest."""

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from app.cad_import.aoi_contracts import AoiRequest, AoiSource
from app.cad_import.cache import file_sha256
from app.cad_import.package_contracts import PackageDrawing, SourcePackage
from app.cad_intake.contracts import relative_path
from app.cad_intake.preview_admission import validate_preview_intake
from app.cad_intake.preview_contracts import CadDrawingSelection, CadPreviewRequest
from app.cad_intake.preview_work import PreviewWork
from app.operations.contracts import ProjectOperation

MAX_INTAKE_MANIFEST_BYTES = 16 * 1024 * 1024


def _inside(path: Path, parent: Path) -> Path:
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(parent.resolve()) or not resolved.is_file():
        raise ValueError("Исходник находится вне разрешённого хранилища")
    return resolved


def _source(
    work: PreviewWork, package: SourcePackage, selected: CadDrawingSelection
) -> AoiSource:
    drawing: PackageDrawing | None = next(
        (item for item in package.drawings if item.path == selected.path), None
    )
    if (
        drawing is None
        or drawing.status != "readable"
        or drawing.source_sha256 != selected.source_sha256
        or drawing.normalized_path is None
    ):
        raise ValueError("Выбранный чертёж не совпадает с паспортом комплекта")
    original = _inside(work.root / relative_path(drawing.path), work.root)
    normalized = Path(drawing.normalized_path).resolve(strict=True)
    # Native DXF can be its own normalized source; converted DWG is cache-only.
    if normalized != original or original.suffix.lower() != ".dxf":
        normalized = _inside(normalized, work.storage / "cache")
    evidence = (
        _inside(Path(drawing.evidence_path), work.storage / "cache")
        if drawing.evidence_path
        else None
    )
    return AoiSource(
        original_path=original,
        original_sha256=selected.source_sha256,
        converted_path=normalized,
        converted_sha256=selected.normalized_sha256,
        evidence_path=evidence,
    )


@dataclass(frozen=True)
class VerifiedPreviewSources:
    request: AoiRequest
    manifest: Path
    manifest_sha256: str
    package_warnings: tuple[str, ...]

    def verify(self) -> None:
        if file_sha256(self.manifest) != self.manifest_sha256:
            raise ValueError("Паспорт комплекта изменился во время подготовки")
        checked: set[tuple[Path, str]] = set()
        for source in (self.request.source, self.request.boundary_source):
            for path, expected in (
                (source.original_path, source.original_sha256),
                (source.converted_path, source.converted_sha256),
            ):
                if (path, expected) not in checked:
                    if file_sha256(path) != expected:
                        raise ValueError("Исходный чертёж изменился после проверки")
                    checked.add((path, expected))


def resolve_preview_sources(
    work: PreviewWork,
    operation: ProjectOperation,
    intake: ProjectOperation,
    request: CadPreviewRequest,
) -> VerifiedPreviewSources:
    passport = validate_preview_intake(intake, operation.project_id, request)
    if intake.cad_intake is None:
        raise ValueError("Операция не содержит исходного комплекта")
    manifest = _inside(
        work.storage / "operations" / intake.id / "source-package.json",
        work.storage,
    )
    if manifest.stat().st_size > MAX_INTAKE_MANIFEST_BYTES:
        raise ValueError("Паспорт комплекта превышает бюджет чтения")
    content = manifest.read_bytes()
    if sha256(content).hexdigest() != request.manifest_sha256:
        raise ValueError("Сохранённый паспорт не совпадает с выбранным")
    package = SourcePackage.model_validate_json(content)
    if (
        Path(package.root).resolve() != work.root.resolve()
        or package.entry != intake.cad_intake.request.entry
        or passport.root_id != intake.cad_intake.request.root_id
        or passport.entry != package.entry
    ):
        raise ValueError("Корневой каталог или входной чертёж комплекта изменился")
    result = VerifiedPreviewSources(
        request=AoiRequest(
            source=_source(work, package, request.source),
            boundary_source=_source(work, package, request.boundary),
            boundary_handle=request.boundary.handle,
        ),
        manifest=manifest,
        manifest_sha256=request.manifest_sha256,
        package_warnings=tuple(passport.blockers),
    )
    result.verify()
    return result
