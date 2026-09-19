from app.cad_intake.contracts import CadPackagePassport
from app.cad_intake.preview_contracts import CadPreviewRequest
from app.operations.contracts import OperationKind, OperationStatus, ProjectOperation
from app.projects.contracts import Project


def require_empty_preview_project(project: Project) -> None:
    if project.source_file is not None or project.plan is not None:
        raise ValueError(
            "Для рабочей территории создайте новый проект без исходника"
        )


def validate_preview_intake(
    intake: ProjectOperation, project_id: str, request: CadPreviewRequest
) -> CadPackagePassport:
    if (
        intake.project_id != project_id
        or intake.kind != OperationKind.INSPECT_CAD_PACKAGE
        or intake.status != OperationStatus.COMPLETED
        or intake.cad_intake is None
        or intake.cad_intake.passport is None
    ):
        raise ValueError("Сначала завершите проверку этого CAD-комплекта")
    passport = intake.cad_intake.passport
    if passport.manifest_sha256 != request.manifest_sha256:
        raise ValueError("Паспорт CAD изменился; обновите результаты проверки")
    selected = []
    for selection in (request.source, request.boundary):
        drawing = next(
            (item for item in passport.drawings if item.path == selection.path), None
        )
        if (
            drawing is None
            or drawing.status != "readable"
            or drawing.source_sha256 != selection.source_sha256
            or drawing.normalized_sha256 != selection.normalized_sha256
        ):
            raise ValueError(
                "Выбранный чертёж не соответствует проверенному паспорту CAD"
            )
        selected.append(drawing)
    boundary = selected[1]
    catalog = boundary.inspection.boundary_catalog if boundary.inspection else None
    if catalog is None:
        raise ValueError(
            "Повторите проверку CAD для получения списка авторских контуров"
        )
    candidate = next(
        (
            item
            for item in catalog.candidates
            if item.handle.upper() == request.boundary.handle.upper()
        ),
        None,
    )
    if candidate is None or not candidate.available_for_preview:
        raise ValueError(
            "Выбранный авторский контур недоступен для подготовки территории"
        )
    return passport
