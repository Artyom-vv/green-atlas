from dataclasses import dataclass
from pathlib import Path

import ezdxf

from app.cad_import.boundary_catalog import boundary_catalog
from app.cad_import.cache import file_sha256
from app.cad_import.contracts import DrawingInspection
from app.cad_import.package_contracts import PackageDrawing, SourcePackage
from app.cad_intake.config import AllowedCadRoot, CadIntakeConfig
from app.cad_intake.contracts import CadIntakeRecord, CadIntakeRequest
from app.cad_intake.passport import make_passport
from app.cad_intake.preview_application import CadPreviewApplication
from app.cad_intake.preview_contracts import (
    CadBoundarySelection,
    CadDrawingSelection,
    CadPreviewRequest,
)
from app.cad_intake.preview_work import PreviewWork
from app.composition import Runtime, create_runtime
from app.operations.contracts import OperationKind, OperationStatus
from app.projects.contracts import Project


@dataclass
class PreviewFixture:
    runtime: Runtime
    config: CadIntakeConfig
    project: Project
    request: CadPreviewRequest
    source: Path

    @property
    def lifecycle(self):
        return self.runtime.application.operations.lifecycle

    def application(self, preparation):
        return CadPreviewApplication(
            self.config, self.lifecycle, preparation, lambda _: None
        )

    def start_work(self):
        application = self.application(None)
        operation = application.start(self.project.id, self.request)
        self.lifecycle.claim(operation.id, "fixture")
        output = self.config.storage / "operations" / operation.id
        output.mkdir(parents=True, exist_ok=True)
        return PreviewWork(
            operation_id=operation.id,
            database=Path(self.runtime.database_path),
            root=self.source.parent,
            storage=self.config.storage,
            receipt=output / "publication.json",
        )


def fixture_preview(tmp_path: Path) -> PreviewFixture:
    root = tmp_path / "originals"
    root.mkdir()
    source = root / "main.dxf"
    document = ezdxf.new("R2018")
    document.units = 6
    contour = document.modelspace().add_lwpolyline(
        [(0, 0), (10, 0), (10, 10), (0, 10)], close=True
    )
    document.modelspace().add_line((-10, 5), (20, 5))
    document.modelspace().add_line((100, 100), (200, 200))
    document.saveas(source)
    digest = file_sha256(source)
    config = CadIntakeConfig(
        (AllowedCadRoot("test", "Test", root),), tmp_path / "data", Path("converter")
    )
    runtime = create_runtime(tmp_path / "service.sqlite3")
    project = runtime.project_repository.create(Project(name="Preview fixture"))
    lifecycle = runtime.application.operations.lifecycle
    intake_request = CadIntakeRequest(
        root_id="test", entry="main.dxf", entry_sha256=digest
    )
    operation = lifecycle.start(
        project.id,
        OperationKind.INSPECT_CAD_PACKAGE,
        cad_intake=CadIntakeRecord(request=intake_request),
    )
    inspection = DrawingInspection(
        dxf_version=document.dxfversion,
        units=6,
        modelspace_entities={"LINE": 2, "LWPOLYLINE": 1},
        layer_names=["0"],
        xrefs={},
        boundary_catalog=boundary_catalog(document),
    )
    package = SourcePackage(
        root=str(root),
        entry="main.dxf",
        drawings=[
            PackageDrawing(
                path="main.dxf",
                source_sha256=digest,
                source_bytes=source.stat().st_size,
                normalized_path=str(source),
                status="readable",
                inspection=inspection,
            )
        ],
    )
    manifest = config.storage / "operations" / operation.id / "source-package.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(package.model_dump_json(), encoding="utf-8")
    passport = make_passport(
        package, "test", file_sha256(manifest), {"main.dxf": digest}
    )
    lifecycle.update(
        operation.id,
        status=OperationStatus.COMPLETED,
        stage="checked",
        progress=100,
        cad_intake=CadIntakeRecord(request=intake_request, passport=passport),
    )
    selected = CadDrawingSelection(
        path="main.dxf", source_sha256=digest, normalized_sha256=digest
    )
    return PreviewFixture(
        runtime,
        config,
        project,
        CadPreviewRequest(
            intake_operation_id=operation.id,
            manifest_sha256=passport.manifest_sha256,
            source=selected,
            boundary=CadBoundarySelection(
                **selected.model_dump(), handle=contour.dxf.handle
            ),
        ),
        source,
    )
