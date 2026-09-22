from app.cad_intake.contracts import CadPackagePassport
from app.cad_intake.prepare_contracts import CadPrepareRequest
from app.operations.contracts import OperationKind, OperationStatus, ProjectOperation
from app.projects.contracts import Project


def require_empty_source_project(project: Project) -> None:
    if project.source_file is not None or project.plan is not None:
        raise ValueError("Для полного исходника создайте новый проект")


def validate_prepared_intake(
    intake: ProjectOperation, project_id: str, request: CadPrepareRequest
) -> CadPackagePassport:
    if (
        intake.project_id != project_id
        or intake.kind != OperationKind.INSPECT_CAD_PACKAGE
        or intake.status != OperationStatus.COMPLETED
        or intake.cad_intake is None
        or intake.cad_intake.passport is None
    ):
        raise ValueError("Сначала завершите проверку полного DXF-комплекта")
    passport = intake.cad_intake.passport
    if passport.manifest_sha256 != request.manifest_sha256:
        raise ValueError("Паспорт изменился; повторите проверку исходника")
    entry = next(
        (item for item in passport.drawings if item.path == passport.entry), None
    )
    if entry is None or entry.status != "readable" or entry.inspection is None:
        raise ValueError("Основной чертёж не прочитан")
    if not passport.entry.lower().endswith(".dxf"):
        raise ValueError(
            "Подготовьте полный DXF вне сервиса; исходный DWG остаётся неизменным"
        )
    entries = passport.entries or [passport.entry]
    if len(entries) != len(set(entries)) or passport.entry not in entries:
        raise ValueError("Паспорт содержит противоречивый список самостоятельных DXF")
    drawings = {item.path: item for item in passport.drawings}
    omitted = set(request.opening_review.skipped_drawings)
    if passport.entry in omitted or not omitted.issubset(entries):
        raise ValueError("Нельзя пропустить основной или посторонний чертёж")
    entries = [path for path in entries if path not in omitted]
    for path in entries:
        drawing = drawings.get(path)
        if (
            drawing is None
            or drawing.status != "readable"
            or drawing.inspection is None
            or not path.lower().endswith(".dxf")
        ):
            raise ValueError("Каждый самостоятельный источник должен быть прочитанным DXF")
    additional_snapshots = {
        item.drawing_path: item.snapshot for item in request.additional_snapshots
    }
    if any(path == passport.entry or path not in entries for path in additional_snapshots):
        raise ValueError("CAD snapshot выбран для постороннего чертежа")
    declared_xrefs = {
        (drawing.path, block)
        for drawing in passport.drawings
        if drawing.inspection is not None
        for block in (drawing.inspection.xrefs or {})
    }
    recorded_xrefs = {(item.owner, item.block) for item in passport.references}
    accepted = {(item.owner, item.block) for item in request.opening_review.skipped_references}
    unresolved = {(item.owner, item.block) for item in passport.references if item.status != "resolved"}
    if accepted != unresolved:
        raise ValueError("Выберите решение для каждой неразрешённой внешней ссылки")
    if declared_xrefs or passport.references:
        if declared_xrefs != recorded_xrefs:
            raise ValueError(
                "DXF-комплект содержит неразрешённые внешние ссылки"
            )
        snapshots = {
            **additional_snapshots,
            **({passport.entry: request.cad_snapshot} if request.cad_snapshot else {}),
        }
        if all(path in snapshots for path in entries):
            return passport
        raise ValueError(
            "DXF содержит внешние ссылки. Приложите нативный CAD snapshot, "
            "который привязывает расчётную геометрию ко всему DXF-комплекту."
        )
    return passport
