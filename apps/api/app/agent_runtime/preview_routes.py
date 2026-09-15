"""Map review of the exact preview referenced by an autonomous run."""

from fastapi import APIRouter, HTTPException, Query

from app.agent_runtime.routes import _preview_from_run, _store, _zone_preview_from_run
from app.agent_runtime.selection import selection_problem
from app.composition import get_application
from app.contracts import ChangeSetPreview
from app.planting_zone_changes import get_zone_change_service
from app.planting_zones.change_contracts import ZoneChangePreview
from app.projects.concurrency import ProjectVersionConflict

router = APIRouter(prefix="/api/projects/{project_id}/agent-runs", tags=["Autonomous agent"])


@router.get("/{run_id}/preview", response_model=ChangeSetPreview)
def get_run_preview(project_id: str, run_id: str, preview_ref: str = Query(min_length=1, max_length=120)):
    application = get_application()

    store = _store()
    with store.lock:
        try:
            record = store.get(project_id, run_id)
        except KeyError as error:
            raise HTTPException(404, "Запуск агента не найден") from error
        if record.state.status != "waiting_approval" or not record.state.pending_approval:
            raise HTTPException(409, "Запуск не ожидает проверки предложения")
        if preview_ref != record.state.pending_approval.get("preview_ref"):
            raise HTTPException(409, "Предложение заменено. Обновите запуск перед просмотром.")
        saved = _preview_from_run(record, preview_ref)
        try:
            issue = selection_problem(record.state.intent, application.get(project_id, lightweight=True))
            if issue:
                raise ValueError(issue.message)
            preview = application.get_change_set_preview(project_id, saved["id"], saved["digest"])
            _preview_from_run(record, preview_ref, authoritative_change_set=preview.model_dump(mode="json"))
            return preview
        except (KeyError, ValueError) as error:
            raise HTTPException(409, "Предложение недоступно или устарело. Рассчитайте его заново.") from error


@router.get("/{run_id}/zone-preview", response_model=ZoneChangePreview)
def get_run_zone_preview(project_id: str, run_id: str, preview_ref: str = Query(min_length=1, max_length=120)):
    application = get_application()

    store = _store()
    with store.lock:
        try:
            record = store.get(project_id, run_id)
        except KeyError as error:
            raise HTTPException(404, "Запуск агента не найден") from error
        if record.state.status != "waiting_approval" or not record.state.pending_approval:
            raise HTTPException(409, "Запуск не ожидает проверки предложения")
        if preview_ref != record.state.pending_approval.get("preview_ref"):
            raise HTTPException(409, "Предложение заменено. Обновите запуск перед просмотром.")
        saved = _zone_preview_from_run(record, preview_ref)
        try:
            preview = get_zone_change_service(application).get_preview(project_id, saved["id"], saved["digest"])
            _zone_preview_from_run(record, preview_ref, authoritative_preview=preview)
            return preview
        except (KeyError, ValueError, ProjectVersionConflict) as error:
            raise HTTPException(409, "Предложение участка недоступно или устарело. Рассчитайте его заново.") from error
