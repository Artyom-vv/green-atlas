from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from app.cad_import.cache import file_sha256
from app.cad_import.contracts import CadConversion, ConverterIdentity, DrawingInspection
from app.cad_import.package_contracts import PackageDrawing, SourcePackage
from app.cad_intake.asset_application import CadAssetApplication
from app.cad_intake.config import AllowedCadRoot, CadIntakeConfig
from app.cad_intake.contracts import CadIntakeRecord, CadIntakeRequest
from app.cad_intake.passport import make_passport
from app.composition import Runtime, create_runtime
from app.operations.contracts import OperationKind, OperationStatus, ProjectOperation
from app.projects.contracts import Project


@dataclass
class AssetFixture:
    runtime: Runtime
    application: CadAssetApplication
    operation: ProjectOperation
    package: SourcePackage
    original: Path
    asset: Path
    manifest: Path
    evidence: Path | None

    @property
    def url(self) -> str:
        return f"/api/projects/{self.operation.project_id}/operations/{self.operation.id}/cad-asset"

    def save_package(self) -> None:
        self.manifest.write_text(self.package.model_dump_json(), encoding="utf-8")
        assert self.operation.cad_intake is not None
        assert self.operation.cad_intake.passport is not None
        self.operation.cad_intake.passport.manifest_sha256 = file_sha256(self.manifest)
        self.runtime.operation_repository.save(self.operation)


def fixture_asset(
    tmp_path: Path, *, units: int = 6, native: bool = False
) -> AssetFixture:
    root, storage = tmp_path / "originals", tmp_path / "data"
    root.mkdir()
    original = root / ("main.dxf" if native else "main.dwg")
    asset = original if native else storage / "cache" / "fixture" / "drawing.dxf"
    asset.parent.mkdir(parents=True, exist_ok=True)
    # A sizeable fixture verifies chunking; no native reader or conversion is run.
    content = (
        b"0\nSECTION\n2\nENTITIES\n"
        + b"999\nrender fixture\n" * 10_000
        + b"0\nENDSEC\n0\nEOF\n"
    )
    if not native:
        original.write_bytes(b"AC1032fixture original")
    asset.write_bytes(content)
    source_hash, asset_hash = file_sha256(original), file_sha256(asset)
    inspection = DrawingInspection(
        dxf_version="AC1032",
        units=units,
        modelspace_entities={},
        layer_names=["0"],
        xrefs={"external": "../legacy/reference.dwg"},
    )
    evidence_path = None
    if not native:
        evidence_path = asset.with_suffix(".conversion.json")
        evidence_path.write_text(
            CadConversion(
                source_name=original.name,
                source_sha256=source_hash,
                source_bytes=original.stat().st_size,
                source_version="AC1032",
                converter=ConverterIdentity(
                    name="libredwg", version="fixture", executable_sha256="a" * 64
                ),
                output_sha256=asset_hash,
                output_bytes=len(content),
                elapsed_seconds=0,
                exit_code=0,
                inspection=inspection,
            ).model_dump_json(),
            encoding="utf-8",
        )
    package = SourcePackage(
        root=str(root),
        entry=original.name,
        drawings=[
            PackageDrawing(
                path=original.name,
                source_sha256=source_hash,
                source_bytes=original.stat().st_size,
                normalized_path=str(asset),
                evidence_path=str(evidence_path) if evidence_path else None,
                inspection=inspection,
                status="readable",
            )
        ],
        status="blocked",
        blockers=["External reference requires review"],
    )
    runtime = create_runtime(tmp_path / "service.sqlite3")
    project = runtime.project_repository.create(Project(name="CAD asset"))
    operation_id = str(uuid4())
    manifest = storage / "operations" / operation_id / "source-package.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(package.model_dump_json(), encoding="utf-8")
    passport = make_passport(
        package, "official", file_sha256(manifest), {original.name: asset_hash}
    )
    operation = runtime.operation_repository.create(
        ProjectOperation(
            id=operation_id,
            project_id=project.id,
            kind=OperationKind.INSPECT_CAD_PACKAGE,
            status=OperationStatus.COMPLETED,
            cad_intake=CadIntakeRecord(
                request=CadIntakeRequest(
                    root_id="official",
                    entry=original.name,
                    entry_sha256=source_hash,
                ),
                passport=passport,
            ),
        )
    )
    config = CadIntakeConfig(
        (AllowedCadRoot("official", "Fixture", root),), storage, None
    )
    return AssetFixture(
        runtime,
        CadAssetApplication(config, runtime.operation_repository),
        operation,
        package,
        original,
        asset,
        manifest,
        evidence_path,
    )
