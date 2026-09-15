"""Resolve only a completed operation's entry, under current server roots."""

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from app.cad_import.contracts import CadConversion, DrawingInspection
from app.cad_import.package_contracts import SourcePackage
from app.cad_intake.asset_contracts import CadAssetUnavailable
from app.cad_intake.config import CadIntakeConfig
from app.cad_intake.contracts import CadDrawingPassport, CadPackagePassport
from app.cad_intake.passport import public_reference
from app.cad_intake.paths import CadDiscovery
from app.operations.contracts import OperationKind, OperationStatus, ProjectOperation

MAX_ASSET_MANIFEST_BYTES = 16 * 1024 * 1024


def _matches_public_inspection(
    public: DrawingInspection | None, private: DrawingInspection | None
) -> bool:
    if public is None or private is None:
        return False
    return public == private.model_copy(
        update={
            "xrefs": {
                block: public_reference(path) for block, path in private.xrefs.items()
            }
        }
    )


def _inside(path: Path, parent: Path) -> Path:
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(parent.resolve()) or not resolved.is_file():
        raise CadAssetUnavailable()
    return resolved


def _read_manifest(path: Path) -> bytes:
    with path.open("rb") as stream:
        content = stream.read(MAX_ASSET_MANIFEST_BYTES + 1)
    if len(content) > MAX_ASSET_MANIFEST_BYTES:
        raise CadAssetUnavailable()
    return content


@dataclass(frozen=True)
class CadAssetSource:
    passport: CadPackagePassport
    drawing: CadDrawingPassport
    original: Path
    asset: Path
    asset_bytes: int
    conversion: CadConversion | None


def resolve_asset_source(
    config: CadIntakeConfig, operation: ProjectOperation, project_id: str
) -> CadAssetSource:
    if operation.project_id != project_id:
        raise KeyError("CAD operation not found")
    if (
        operation.kind != OperationKind.INSPECT_CAD_PACKAGE
        or operation.status != OperationStatus.COMPLETED
        or operation.cad_intake is None
        or operation.cad_intake.passport is None
    ):
        raise CadAssetUnavailable()
    record = operation.cad_intake
    request, passport = record.request, record.passport
    assert passport is not None
    root = config.root(request.root_id).path.resolve(strict=True)
    original = CadDiscovery(config).resolve(
        request.root_id, request.entry, drawing=True
    )
    manifest = _inside(
        config.storage / "operations" / operation.id / "source-package.json",
        config.storage / "operations",
    )
    content = _read_manifest(manifest)
    if sha256(content).hexdigest() != passport.manifest_sha256:
        raise CadAssetUnavailable()
    package = SourcePackage.model_validate_json(content)
    if (
        Path(package.root).resolve() != root
        or package.entry != request.entry
        or passport.root_id != request.root_id
        or passport.entry != request.entry
    ):
        raise CadAssetUnavailable()
    drawing = next(
        (item for item in passport.drawings if item.path == request.entry), None
    )
    private = next(
        (item for item in package.drawings if item.path == request.entry), None
    )
    if (
        drawing is None
        or private is None
        or drawing.status != "readable"
        or private.status != "readable"
        or drawing.source_sha256 != request.entry_sha256
        or private.source_sha256 != request.entry_sha256
        or drawing.source_bytes != private.source_bytes
        or not _matches_public_inspection(drawing.inspection, private.inspection)
        or drawing.normalized_sha256 is None
        or private.normalized_path is None
    ):
        raise CadAssetUnavailable()
    asset = Path(private.normalized_path).resolve(strict=True)
    conversion = None
    if original.suffix.lower() == ".dwg":
        asset = _inside(asset, config.storage / "cache")
        if asset.suffix.lower() != ".dxf" or private.evidence_path is None:
            raise CadAssetUnavailable()
        evidence = _inside(Path(private.evidence_path), config.storage / "cache")
        conversion = CadConversion.model_validate_json(_read_manifest(evidence))
        if (
            conversion.source_sha256 != drawing.source_sha256
            or conversion.source_bytes != drawing.source_bytes
            or conversion.output_sha256 != drawing.normalized_sha256
        ):
            raise CadAssetUnavailable()
        # PackageInspector can refresh derived inspection (e.g. boundary catalog)
        # without rewriting immutable conversion evidence. Byte identities bind
        # the asset; current units/details come from the checked package passport.
    elif asset != original or drawing.normalized_sha256 != request.entry_sha256:
        raise CadAssetUnavailable()
    size = asset.stat().st_size
    if (
        not 0 < size <= config.policy.max_output_bytes
        or original.stat().st_size != drawing.source_bytes
        or (conversion is not None and conversion.output_bytes != size)
    ):
        raise CadAssetUnavailable()
    return CadAssetSource(passport, drawing, original, asset, size, conversion)
