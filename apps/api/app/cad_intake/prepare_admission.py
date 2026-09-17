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
    if entry.inspection.xrefs or passport.references:
        raise ValueError(
            "DXF содержит внешние ссылки. Для полного импорта нужен подготовленный "
            "чертёж со всеми подключёнными источниками; один корневой файл не заменяет комплект."
        )
    return passport
