"""Experimental runtime API kept separate until parity with the old chat."""

from functools import lru_cache

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app import planning_assistant as local
from app.agent_interpreter import project_context
from app.agent_runtime.contracts import ToolCall, ToolError
from app.agent_runtime.engine import AgentEngine
from app.agent_runtime.gateway import GatewayContext, GatewayPolicy, ToolGateway
from app.agent_runtime.intent import IntentCompiler
from app.agent_runtime.planner import LocalPlanner
from app.agent_runtime.store import AgentRunRecord, AgentRunStore
from app.agent_runtime.verifier import extract_change_set, verify_preview_data


class AgentRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    text: str = Field(min_length=5, max_length=2000)
    conversation_id: str | None = Field(default=None, max_length=120)


class AgentRunApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preview_ref: str | None = Field(default=None, max_length=120)


class AgentRunAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    text: str = Field(min_length=1, max_length=800)


router = APIRouter(prefix="/api/projects/{project_id}/agent-runs", tags=["Autonomous agent"])


def _run_in_background(project_id: str, run_id: str, model: str) -> None:
    """Execute after the HTTP response; polling observes durable checkpoints."""
    from app import api

    store = _store()
    if not local._busy.acquire(blocking=False):
        try:
            record = store.get(project_id, run_id)
            if record.state.status in {"queued", "running"}:
                failure = ToolError(
                    code="AGENT_BUSY",
                    message="Другой запуск агента уже выполняется.",
                    retryable=True,
                    remedy="Повторите расчёт после завершения текущего запуска.",
                )
                store.checkpoint(
                    project_id,
                    run_id,
                    expected_revision=record.revision,
                    state=record.state.model_copy(update={"status": "failed", "failure": failure}),
                    kind="run_failed",
                    payload=failure.model_dump(mode="json"),
                )
        except Exception:
            # The worker boundary must not turn a queued run into an
            # unobservable process-level exception.
            return
        return
    try:
        gateway = ToolGateway(api.application)
        AgentEngine(store, gateway).run(run_id, project_id, LocalPlanner(model, gateway.registry))
    except Exception as error:
        # AgentEngine handles planner/tool failures itself. This catches only
        # infrastructure failures (for example a lost checkpoint connection)
        # so the client still receives a terminal, retryable state.
        try:
            record = store.get(project_id, run_id)
            if record.state.status in {"queued", "running"}:
                failure = ToolError(
                    code="AGENT_RUNTIME_FAILED",
                    message=f"Запуск агента остановлен: {str(error)[:600]}",
                    retryable=True,
                    remedy="Повторить запуск после проверки локального сервиса.",
                )
                store.checkpoint(
                    project_id,
                    run_id,
                    expected_revision=record.revision,
                    state=record.state.model_copy(update={"status": "failed", "failure": failure}),
                    kind="run_failed",
                    payload=failure.model_dump(mode="json"),
                )
        except Exception:
            return
    finally:
        local._busy.release()


@lru_cache(maxsize=4)
def store_for_path(path: str) -> AgentRunStore:
    return AgentRunStore(path)


def _store() -> AgentRunStore:
    from app.api import database_path
    return store_for_path(database_path)


@router.post("", response_model=AgentRunRecord, status_code=201)
def create_run(project_id: str, request: AgentRunCreate):
    from app.api import application
    model = local.configured_model()
    if not model:
        raise HTTPException(503, "Локальная модель не подключена")
    try:
        project = application.get(project_id, lightweight=True)
        intent = IntentCompiler(model).compile(request.text, project_context=project_context(project))
        if intent.scope_mode == "explicit" and not (intent.explicit_zone_ids or intent.explicit_object_ids):
            raise ValueError("В намерении не разрешён участок или объект")
        return _store().create(project_id, intent, conversation_id=request.conversation_id,
                               snapshot_version=project.state_version,
                               plan_version=project.plan.version if project.plan else None)
    except KeyError as error:
        raise HTTPException(404, "Проект не найден") from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    except (OSError, TypeError) as error:
        raise HTTPException(503, "Не удалось создать запуск агента") from error


@router.get("/{run_id}", response_model=AgentRunRecord)
def get_run(project_id: str, run_id: str):
    try:
        return _store().get(project_id, run_id)
    except KeyError as error:
        raise HTTPException(404, "Запуск агента не найден") from error


def _preview_from_run(record: AgentRunRecord, preview_ref: str) -> dict:
    for event in reversed(record.events):
        payload = event.payload
        if event.kind != "tool_result" or payload.get("call_id") != preview_ref:
            continue
        if payload.get("status") not in {"succeeded", "partial"}:
            break
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        verification = payload.get("verification")
        if not isinstance(verification, dict) or verification.get("status") != "verified":
            verification = verify_preview_data(data, tool_name=payload.get("name")).model_dump(mode="json")
        if verification.get("status") != "verified":
            break
        change_set = extract_change_set(data, tool_name=payload.get("name"))
        if isinstance(change_set, dict):
            required = ("id", "digest", "base_plan_version")
            if all(change_set.get(key) is not None for key in required):
                return change_set
        break
    raise HTTPException(409, "Предложение для подтверждения больше недоступно. Рассчитайте его заново.")


@router.post("/{run_id}/approve", response_model=AgentRunRecord)
def approve_run(project_id: str, run_id: str, request: AgentRunApproval):
    from app.api import application

    store = _store()
    try:
        record = store.get(project_id, run_id)
    except KeyError as error:
        raise HTTPException(404, "Запуск агента не найден") from error
    state = record.state
    if state.status == "finished":
        return record
    if state.status != "waiting_approval" or not state.pending_approval:
        raise HTTPException(409, "Запуск не ожидает подтверждения")
    preview_ref = request.preview_ref or state.pending_approval.get("preview_ref")
    if preview_ref != state.pending_approval.get("preview_ref"):
        raise HTTPException(409, "Подтверждение относится к другому preview")
    change_set = _preview_from_run(record, preview_ref)
    try:
        result = ToolGateway(application).call(
            GatewayContext(
                project_id=project_id,
                expected_state_version=state.snapshot_version,
                run_id=run_id,
                approved_change_set_id=change_set["id"],
                approved_digest=change_set["digest"],
                policy=GatewayPolicy(
                    allowed_effects=frozenset({"write"}),
                    project_id=project_id,
                ),
            ),
            ToolCall(name="commit_change_set", arguments={
                "preview_id": change_set["id"],
                "digest": change_set["digest"],
                "base_plan_version": int(change_set["base_plan_version"]),
            }),
        )
        if result.status != "succeeded":
            raise ValueError(result.error.message if result.error else "Не удалось применить preview")
        mutation = result.data if isinstance(result.data, dict) else {}
    except ValueError as error:
        failure = ToolError(
            code="APPROVAL_FAILED",
            message=str(error)[:800],
            retryable=True,
            remedy="Обновите снимок проекта и рассчитайте предложение заново.",
        )
        failed = state.model_copy(update={"status": "failed", "failure": failure})
        return store.checkpoint(
            project_id, run_id, expected_revision=record.revision,
            state=failed, kind="commit_failed", payload=failure.model_dump(mode="json"),
        )
    finished = state.model_copy(update={
        "status": "finished",
        "pending_approval": None,
        "outcome_ref": f"change-set:{mutation.get('change_set_id', change_set['id'])}",
        "snapshot_version": int(result.resource_versions.get("project", state.snapshot_version)),
        "plan_version": (int(result.resource_versions["plan"])
                          if isinstance(result.resource_versions.get("plan"), int)
                          else state.plan_version),
        "failure": None,
    })
    return store.checkpoint(
        project_id, run_id, expected_revision=record.revision,
        state=finished,
        kind="commit_applied",
        payload={
            "change_set_id": mutation.get("change_set_id", change_set["id"]),
            "plan_version": mutation.get("plan_version"),
            "state_version": mutation.get("state_version"),
            "added": len(mutation.get("added_ids", [])),
            "updated": len(mutation.get("updated_ids", [])),
            "deleted": len(mutation.get("deleted_ids", [])),
        },
    )


@router.post("/{run_id}/answer", response_model=AgentRunRecord)
def answer_run(project_id: str, run_id: str, request: AgentRunAnswer):
    """Add one user answer and resume the same durable autonomous run."""
    from app import api

    model = local.configured_model()
    if not model:
        raise HTTPException(503, "Локальная модель не подключена")
    store = _store()
    try:
        record = store.get(project_id, run_id)
        project = api.application.get(project_id, lightweight=True)
    except KeyError as error:
        raise HTTPException(404, "Запуск агента не найден") from error
    if record.state.status != "waiting_question" or not record.state.pending_question:
        raise HTTPException(409, "Запуск не ожидает ответа")
    try:
        intent = IntentCompiler(model).compile(
            f"{record.state.intent.raw_text}\n\nДополнение пользователя: {request.text}",
            project_context=project_context(project),
        )
    except (OSError, TypeError, ValueError) as error:
        raise HTTPException(422, f"Не удалось разобрать ответ: {str(error)[:600]}") from error
    resumed = record.state.model_copy(update={
        "status": "queued",
        "intent": intent,
        "resolved_scope": None,
        "candidate_zone_ids": [],
        "snapshot_version": project.state_version,
        "plan_version": project.plan.version if project.plan else None,
        "pending_question": None,
        "pending_approval": None,
        # The answer starts a new planning epoch. Preserve the event log and
        # evidence, but allow a reread for the newly compiled intent.
        "tool_fingerprints": [],
        "failure": None,
    })
    return store.checkpoint(
        project_id,
        run_id,
        expected_revision=record.revision,
        state=resumed,
        kind="question_answered",
        payload={"text": request.text, "missing_slot": record.state.pending_question.get("slot", "unknown")},
    )


@router.post("/{run_id}/run", response_model=AgentRunRecord)
def run_agent(project_id: str, run_id: str, background_tasks: BackgroundTasks):
    model = local.configured_model()
    if not model:
        raise HTTPException(503, "Локальная модель не подключена")
    try:
        record = _store().get(project_id, run_id)
    except KeyError as error:
        raise HTTPException(404, "Запуск агента не найден") from error
    if record.state.status in {"finished", "cancelled", "waiting_question", "waiting_approval", "waiting_job"}:
        return record
    background_tasks.add_task(_run_in_background, project_id, run_id, model)
    return record
