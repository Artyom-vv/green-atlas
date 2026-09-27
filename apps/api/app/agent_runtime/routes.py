"""Experimental runtime API kept separate until parity with the old chat."""

import re

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app import planning_assistant as local
from app.agent_interpreter import project_context
from app.agent_runtime.contracts import (
    ControlCommandGeometry,
    ControlResult,
    ResolvedScope,
    SelectionContext,
    ToolError,
    ToolResult,
)
from app.agent_runtime.engine import AgentEngine
from app.agent_runtime.errors import WorkflowConflict
from app.agent_runtime.gateway import ToolGateway
from app.agent_runtime.history_budget import (
    MAX_ANSWER_MESSAGE_CHARS,
    MAX_HISTORY_CHARS,
    MAX_HISTORY_TURNS,
    MAX_INITIAL_MESSAGE_CHARS,
    HistoryBudgetExceeded,
    join_source_history,
)
from app.agent_runtime.intent import IntentCompiler
from app.agent_runtime.planner import LocalPlanner
from app.agent_runtime.selection import (
    bind_selection,
    refresh_selection,
    resolved_selection,
    selection_problem,
    selection_reference,
)
from app.agent_runtime.semantic_guardrails import (
    explicit_zone_numbers,
    preserving_quantity_amendment,
)
from app.agent_runtime.store import AgentRunRecord, AgentRunStore
from app.agent_runtime.verifier import extract_change_set, verify_preview_data
from app.agent_runtime.zone_workflow import (
    bind_zone_intent,
    verify_zone_evidence,
    zone_geometry_references,
)
from app.application import ProjectApplication
from app.composition import get_agent_run_store, get_application


class AgentRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    text: str = Field(min_length=5, max_length=MAX_INITIAL_MESSAGE_CHARS)
    conversation_id: str | None = Field(default=None, max_length=120)
    selection_context: SelectionContext | None = None


class AgentRunApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preview_ref: str | None = Field(default=None, max_length=120)


class AgentRunAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    text: str = Field(min_length=1, max_length=MAX_ANSWER_MESSAGE_CHARS)
    selection_context: SelectionContext | None = None


def _answer_scope(text, project):
    """Only current-answer references may replace a previous selection."""
    zones = project_context(project).get("zones", [])
    numbers = explicit_zone_numbers(text)
    if set(numbers) - {item.get("number") for item in zones}:
        raise ValueError("Указанный в ответе участок не найден. Уточните существующий участок.")
    zone_ids = [item["id"] for item in zones if (item.get("number") in numbers or
        (item.get("label") and re.search(rf'(?<!\w){re.escape(item["label"])}(?!\w)', text, re.IGNORECASE)))]
    object_ids = [item.id for item in (project.plan.objects if project.plan else [])
                  if re.search(rf'(?<![\w-]){re.escape(item.id)}(?![\w-])', text, re.IGNORECASE)
                  and (not item.id.isdigit() or re.search(rf'\b(?:id|ид|идентификатор)\s*[:=]?\s*{re.escape(item.id)}\b', text, re.IGNORECASE))]
    if zone_ids and object_ids:
        raise ValueError("Укажите одну область: участки или конкретные посадки")
    return zone_ids, object_ids


router = APIRouter(prefix="/api/projects/{project_id}/agent-runs", tags=["Autonomous agent"])


def _fail_execution_attempt(store, project_id, run_id, attempt_id, failure):
    """Infrastructure errors belong only to the worker's still-active attempt."""
    try:
        with store.lock:
            record = store.get(project_id, run_id)
            if record.state.status not in {"scheduled", "running"} or record.state.execution_attempt_id != attempt_id:
                return
            store.checkpoint(project_id, run_id, expected_revision=record.revision,
                state=record.state.model_copy(update={"status": "failed", "failure": failure}),
                kind="run_failed", payload={"execution_attempt_id": attempt_id, **failure.model_dump(mode="json")})
    except Exception:
        # If the store remains unavailable, startup recovery retains the
        # attempt's interruption instead of replaying it after a restart.
        return


def _run_in_background(project_id: str, run_id: str, model: str, attempt_id: str, application: ProjectApplication, store: AgentRunStore) -> None:
    """Execute the accepted attempt against the runtime captured by /run."""
    acquired = False
    try:
        with store.lock:
            record = store.get(project_id, run_id)
            if _reconcile_zone_commit(record, store, application) is not None:
                return
            record, started = store.start_execution(project_id, run_id, attempt_id)
            if not started:
                return
        acquired = local._busy.acquire(blocking=False)
        if not acquired:
            _fail_execution_attempt(store, project_id, run_id, attempt_id, ToolError(
                code="AGENT_BUSY", message="Другой запуск агента уже выполняется.", retryable=True,
                remedy="Повторите расчёт после завершения текущего запуска."))
            return
        gateway = ToolGateway(application)
        AgentEngine(store, gateway).run(run_id, project_id, LocalPlanner(model, gateway.registry),
            execution_attempt_id=attempt_id)
    except Exception as error:
        _fail_execution_attempt(store, project_id, run_id, attempt_id, ToolError(
            code="AGENT_RUNTIME_FAILED", message=f"Запуск агента остановлен: {str(error)[:600]}", retryable=True,
            remedy="Повторить запуск после проверки локального сервиса."))
    finally:
        if acquired:
            local._busy.release()


def _store() -> AgentRunStore:
    return get_agent_run_store()


@router.post("", response_model=AgentRunRecord, status_code=201)
def create_run(project_id: str, request: AgentRunCreate):
    application = get_application()
    model = local.configured_model()
    if not model:
        raise HTTPException(503, "Локальная модель не подключена")
    try:
        project = application.get(project_id)
        intent = IntentCompiler(model).compile(request.text, project_context=project_context(project), full_project=project)
        if intent.goal.operation != "zones" and intent.control is None:
            intent = bind_selection(intent, request.selection_context, project)
        if intent.control is None and intent.scope_mode == "explicit" and not (intent.explicit_zone_ids or intent.explicit_object_ids):
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
        application = get_application()
        store = _store()
        with store.lock:
            record = store.get(project_id, run_id)
            return _reconcile_zone_commit(record, store, application) or record
    except KeyError as error:
        raise HTTPException(404, "Запуск агента не найден") from error



@router.get("/{run_id}/control-command", response_model=ControlCommandGeometry)
def get_control_command(project_id: str, run_id: str, command_id: str | None = Query(default=None, min_length=1, max_length=120)):
    application = get_application()
    from app.agent_runtime.control_workflow import control_geometry

    store = _store()
    try:
        with store.lock:
            record = store.get(project_id, run_id)
            record = _reconcile_zone_commit(record, store, application) or record
            command = record.state.control_command
            if (record.state.status != "waiting_ui" or command is None
                    or (command_id is not None and command_id != command.id)):
                raise HTTPException(409, "Запуск больше не ожидает эту команду карты.")
            return control_geometry(record, application)
    except KeyError as error:
        raise HTTPException(404, "Запуск или участок не найден") from error
    except ValueError as error:
        raise HTTPException(409, str(error)) from error


@router.post("/{run_id}/control-result", response_model=AgentRunRecord)
def record_control_result(project_id: str, run_id: str, request: ControlResult):
    application = get_application()
    from app.agent_runtime.control_workflow import accept_control_result

    if request.project_id != project_id or request.run_id != run_id:
        raise HTTPException(409, "Результат относится к другому проекту или запуску.")
    store = _store()
    try:
        with store.lock:
            record = store.get(project_id, run_id)
            return accept_control_result(record, request, application, store)
    except WorkflowConflict as error:
        raise HTTPException(409, str(error)) from error
    except KeyError as error:
        raise HTTPException(404, "Запуск агента не найден") from error


@router.get("", response_model=list[AgentRunRecord])
def list_runs(project_id: str, limit: int = Query(default=20, ge=1, le=50)):
    application = get_application()

    try:
        application.get(project_id, lightweight=True)
    except KeyError as error:
        raise HTTPException(404, "Проект не найден") from error
    return _store().list(project_id, limit=limit)


@router.post("/{run_id}/cancel", response_model=AgentRunRecord)
def cancel_run(project_id: str, run_id: str):
    """Cancel a checkpointed run; no project write is implied by cancellation."""
    store = _store()
    with store.lock:
        try:
            record = store.get(project_id, run_id)
        except KeyError as error:
            raise HTTPException(404, "Запуск агента не найден") from error
        application = get_application()
        recovered = _reconcile_zone_commit(record, store, application)
        if recovered is not None:
            return recovered
        if record.state.status in {"finished", "cancelled"}:
            return record
        cancelled = record.state.model_copy(update={
            "status": "cancelled", "pending_approval": None, "pending_question": None,
            "placement_retry": None, "failure": None,
        })
        return store.checkpoint(project_id, run_id, expected_revision=record.revision,
                                state=cancelled, kind="run_cancelled", payload={"reason": "user_cancelled"})


@router.post("/{run_id}/resume", response_model=AgentRunRecord)
def resume_run(project_id: str, run_id: str):
    """Restart a failed/cancelled run from fresh facts and retain its audit log."""
    application = get_application()

    store = _store()
    with store.lock:
        try:
            record = store.get(project_id, run_id)
            project = application.get(project_id, lightweight=True)
        except KeyError as error:
            raise HTTPException(404, "Проект или запуск агента не найден") from error
        recovered = _reconcile_zone_commit(record, store, application)
        if recovered is not None:
            return recovered
        if record.state.status not in {"failed", "cancelled"}:
            raise HTTPException(409, "Возобновить можно только отменённый или остановленный запуск")
        intent = record.state.intent
        if intent.zone is not None:
            try:
                project = application.get(project_id)
                intent = intent.model_copy(update={"zone": _refresh_zone_bound(intent.zone, project)})
            except ValueError as error:
                raise HTTPException(409, str(error)) from error
        if intent.scope_mode == "selection":
            intent = refresh_selection(intent, project)
        scope = None
        if intent.scope_mode == "explicit" and (intent.explicit_zone_ids or intent.explicit_object_ids):
            known_zones = {zone.id for zone in project.planting_zones}
            known_objects = {obj.id for obj in project.plan.objects} if project.plan else set()
            if set(intent.explicit_zone_ids) - known_zones or set(intent.explicit_object_ids) - known_objects:
                raise HTTPException(409, "Выбранный участок или объект больше не существует. Создайте новое задание.")
            scope = ResolvedScope(
                project_id=project_id, zone_ids=intent.explicit_zone_ids, object_ids=intent.explicit_object_ids,
                basis="user", criteria=["explicit_user_scope"], source_revision=project.state_version,
            )
        elif intent.scope_mode == "selection":
            scope = resolved_selection(intent, project_id)
        restarted = record.state.model_copy(update={
            "intent": intent,
            "status": "queued", "execution_attempt_id": None, "snapshot_version": project.state_version,
            "plan_version": project.plan.version if project.plan else None, "resolved_scope": scope,
            "candidate_zone_ids": [], "step": 0, "tool_fingerprints": [], "last_result": None, "read_outcome": None,
            "placement_retry": None, "pending_approval": None, "pending_question": None,
            "failure": None, "outcome_ref": None,
            "control_command": None, "control_result": None,
        })
        return store.checkpoint(project_id, run_id, expected_revision=record.revision,
                                state=restarted, kind="run_restarted", payload={"reason": "user_resumed"})


def _refresh_zone_bound(bound, project):
    """Explicit restart changes revisions, preserving the originally named contour."""
    proposed = bound.intent
    reference = proposed.geometry_reference
    if reference is not None:
        candidate = next((item["reference"] for item in zone_geometry_references(project)
                          if item["reference"]["feature_id"] == reference.feature_id), None)
        if candidate is None or candidate["geometry_digest"] != reference.geometry_digest:
            raise ValueError("Исходный контур изменился. Создайте новое поручение и проверьте его геометрию.")
        proposed = proposed.model_copy(update={"geometry_reference": type(reference).model_validate(candidate)})
    refreshed = bind_zone_intent(bound.source_text, project, proposed, source_turns=bound.source_turns)
    if refreshed.draft.zone_id != bound.draft.zone_id or refreshed.intent.operation != bound.intent.operation:
        raise ValueError("Исходный участок изменился. Укажите новое полное поручение.")
    return refreshed


def _zone_preview_from_run(record: AgentRunRecord, preview_ref: str, *, authoritative_preview=None) -> dict:
    """The epoch identifies the reference; only the saved full preview proves effects."""
    intent = record.state.intent
    if (intent.goal.operation != "zones" or intent.zone is None
            or record.state.pending_approval is None or record.state.pending_approval.get("kind") != "planting_zones"):
        raise HTTPException(409, "Запуск не содержит предложения участка")
    from app.agent_runtime.policy import assess_requirements
    if assess_requirements(intent).status != "supported":
        raise HTTPException(409, "Условия изменения участка не подтверждены")
    target_id = intent.zone.draft.zone_id
    if (intent.raw_text != intent.zone.source_text or intent.zone.project_id != record.state.project_id
            or intent.scope_mode != ("explicit" if target_id else "project")
            or intent.explicit_zone_ids != ([target_id] if target_id else []) or intent.explicit_object_ids):
        raise HTTPException(409, "Исходное поручение не соответствует области изменения участка")
    for event in reversed(record.events):
        if event.kind in {"question_answered", "run_restarted"}:
            break
        if event.kind != "tool_result" or event.payload.get("call_id") != preview_ref:
            continue
        result = ToolResult.model_validate(event.payload)
        if result.name != "prepare_zone_change" or result.status != "succeeded" or not result.verification or result.verification.status != "verified":
            break
        data = result.data
        if not isinstance(data, dict) or not all(data.get(key) is not None for key in ("id", "digest", "base_state_version")):
            break
        if f"zone-preview:{record.state.project_id}:{record.state.run_id}:{preview_ref}:{data['id']}" not in result.evidence_refs:
            break
        if (data.get("project_id") != record.state.project_id or data["base_state_version"] != record.state.snapshot_version
                or data["base_state_version"] != intent.zone.draft.base_state_version):
            break
        if authoritative_preview is not None:
            try:
                application = get_application()
                if bind_zone_intent(intent.zone.source_text, application.get(record.state.project_id), intent.zone.intent, source_turns=intent.zone.source_turns) != intent.zone:
                    break
                verify_zone_evidence(intent.zone, data, authoritative_preview)
            except ValueError:
                break
        return data
    raise HTTPException(409, "Предложение участка недоступно. Рассчитайте его заново.")


def _preview_from_run(record: AgentRunRecord, preview_ref: str, *, authoritative_change_set: dict | None = None) -> dict:
    if record.state.pending_approval and record.state.pending_approval.get("kind", "plantings") != "plantings":
        raise HTTPException(409, "Предложение относится к участкам, а не посадкам")
    for event in reversed(record.events):
        if event.kind in {"question_answered", "run_restarted"}:
            break
        payload = event.payload
        if event.kind != "tool_result" or payload.get("call_id") != preview_ref:
            continue
        if payload.get("status") not in {"succeeded", "partial"}:
            break
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        if authoritative_change_set is not None:
            data = {**data, "change_set": authoritative_change_set}
            if isinstance(data.get("proposal"), dict):
                data["proposal"] = {**data["proposal"], "change_set": authoritative_change_set}
        # Recheck the saved facts against the current brief, including old
        # checkpoints that predate the capacity contract.
        verification = verify_preview_data(
            data, tool_name=payload.get("name"), intent=record.state.intent,
            zone_ids=record.state.resolved_scope.zone_ids if record.state.resolved_scope else None,
        ).model_dump(mode="json")
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
    store = _store()
    # Approval and cancellation share the checkpoint lock through commit, so
    # a concurrent cancel cannot claim success while an approved write runs.
    try:
        with store.lock:
            return _approve_run_locked(project_id, run_id, request, store)
    except WorkflowConflict as error:
        raise HTTPException(409, str(error)) from error


def _commit_coordinator(store, application):
    from app.agent_runtime.commit_workflow import CommitCoordinator
    return CommitCoordinator(application, store, planting_preview=_preview_from_run, zone_preview=_zone_preview_from_run)


def _approve_run_locked(project_id: str, run_id: str, request: AgentRunApproval, store: AgentRunStore):
    application = get_application()
    try:
        record = store.get(project_id, run_id)
    except KeyError as error:
        raise HTTPException(404, "Запуск агента не найден") from error
    return _commit_coordinator(store, application).approve(record, request.preview_ref)


def _zone_commit_attempt(record):
    # Compatibility name while all lifecycle entry points share one barrier.
    from app.agent_runtime.commit_workflow import pending_commit_attempt
    return pending_commit_attempt(record)


def _reconcile_zone_commit(record, store, application):
    # This handles both planting and zone mutations, including legacy callers.
    from app.agent_runtime.control_workflow import reconcile_control
    return _commit_coordinator(store, application).reconcile(record) or reconcile_control(record, store, application)


@router.post("/{run_id}/answer", response_model=AgentRunRecord)
def answer_run(project_id: str, run_id: str, request: AgentRunAnswer):
    """Add one user answer and resume the same durable autonomous run."""
    application = get_application()

    store = _store()
    try:
        with store.lock:
            record = store.get(project_id, run_id)
            recovered = _reconcile_zone_commit(record, store, application)
            if recovered is not None:
                return recovered
        project = application.get(project_id)
    except KeyError as error:
        raise HTTPException(404, "Запуск агента не найден") from error
    if record.state.status != "waiting_question" or not record.state.pending_question:
        raise HTTPException(409, "Запуск не ожидает ответа")
    source_turns = [*(record.state.intent.source_turns or [record.state.intent.raw_text]), request.text]
    try:
        text = join_source_history(source_turns)
    except HistoryBudgetExceeded as error:
        raise HTTPException(413, detail={"code": "AGENT_HISTORY_LIMIT", "message": str(error),
            "details": {"max_history_chars": MAX_HISTORY_CHARS, "max_history_turns": MAX_HISTORY_TURNS}}) from error
    model = local.configured_model()
    if not model:
        raise HTTPException(503, "Локальная модель не подключена")
    try:
        amended_count = (preserving_quantity_amendment(request.text)
                         if record.state.pending_question.get("slot") == "capacity" else None)
        if amended_count is not None:
            previous = record.state.last_result
            current_events = AgentEngine._current_epoch_events(record)
            if (previous is None or previous.name != "prepare_placement"
                    or not isinstance(previous.data, dict)
                    or not any(event.kind == "tool_result" and event.payload.get("call_id") == previous.call_id for event in current_events)):
                raise ValueError("Не найдены сохранённые факты последнего расчёта. Подготовьте новое предложение.")
            data = previous.data
            zones = data.get("resolved_zone_ids") or []
            species = data.get("species_revision_ids") or []
            if not zones or not species or set(zones) - {zone.id for zone in project.planting_zones}:
                raise ValueError("Не удалось сохранить прежний участок и породы. Подготовьте новое предложение.")
            original = record.state.intent
            intent = original.model_copy(update={
                "raw_text": text, "source_turns": source_turns, "goal": original.goal.model_copy(update={"target_count": amended_count}),
                "scope_mode": "explicit", "explicit_zone_ids": zones, "explicit_object_ids": [],
                "species_ids": species, "arrangement": data.get("arrangement") or original.arrangement,
                "plant_kind": data.get("plant_kind") or original.plant_kind,
                "delegations": [item for item in original.delegations if item.slot not in {"scope", "species", "quantity"}],
                "evidence": original.evidence.model_copy(update={"corrections":
                    (original.evidence.corrections + ["quantity:capacity_answer", "scope_and_species:kept_from_verified_search"])[-20:]}),
            })
            # Validate copied values at the same boundary as a compiled intent.
            intent = type(original).model_validate(intent.model_dump(mode="json"))
        else:
            intent = IntentCompiler(model).compile(text, project_context=project_context(project), full_project=project, source_turns=source_turns)
        original = record.state.intent
        new_reference = None
        answer_zones, answer_objects = [], []
        if intent.goal.operation != "zones" and intent.control is None:
            new_reference = selection_reference(request.text)
            answer_zones, answer_objects = (_answer_scope(request.text, project)
                if new_reference is not None or original.scope_mode == "selection" else ([], []))
        if intent.goal.operation == "zones" or intent.control is not None:
            # Zone mutation selection has no source-bound adapter yet. Its
            # explicit name/ID grammar returns a zone question, never UI IDs.
            pass
        elif new_reference is not None:
            # An explicit answer authorizes a new UI snapshot and a new preview.
            # The full source preserves a previously specified noun/type.
            intent = intent.model_copy(update={"explicit_zone_ids": answer_zones, "explicit_object_ids": answer_objects})
            intent = bind_selection(intent, request.selection_context, project,
                                    reference=selection_reference(text, source_turns=source_turns))
        elif original.scope_mode == "selection" and (answer_zones or answer_objects):
            intent = intent.model_copy(update={"scope_mode": "explicit", "explicit_zone_ids": answer_zones,
                "explicit_object_ids": answer_objects, "selection_context": None, "selection_binding": None,
                "selection_reference": None, "selection_issue": None})
        elif original.scope_mode == "selection":
            # New UI state attached to an unrelated answer never changes the
            # accepted snapshot or its exact source-selected subset.
            intent = intent.model_copy(update={"scope_mode": "selection", "explicit_zone_ids": [], "explicit_object_ids": [],
                "selection_context": original.selection_context, "selection_binding": original.selection_binding,
                "selection_reference": original.selection_reference, "selection_issue": original.selection_issue})
            problem = selection_problem(intent, project)
            if problem:
                intent = intent.model_copy(update={"selection_issue": problem})
        else:
            intent = bind_selection(intent, None, project)
    except (OSError, TypeError, ValueError) as error:
        raise HTTPException(422, f"Не удалось разобрать ответ: {str(error)[:600]}") from error
    resumed = record.state.model_copy(update={
        "status": "queued",
        "execution_attempt_id": None,
        "intent": intent,
        "resolved_scope": None,
        "placement_retry": None,
        "candidate_zone_ids": [],
        "snapshot_version": project.state_version,
        "plan_version": project.plan.version if project.plan else None,
        "pending_question": None,
        "pending_approval": None,
        # The answer starts a new planning epoch. Preserve the event log and
        # evidence, but allow a reread for the newly compiled intent.
        "tool_fingerprints": [],
        "failure": None,
        "step": 0,
        "last_result": None,
        "read_outcome": None,
        "control_command": None, "control_result": None,
    })
    if intent.scope_mode == "explicit" and (intent.explicit_zone_ids or intent.explicit_object_ids):
        resumed.resolved_scope = ResolvedScope(
            project_id=project_id, zone_ids=intent.explicit_zone_ids, object_ids=intent.explicit_object_ids,
            basis="user", criteria=["explicit_user_scope"], source_revision=project.state_version,
        )
    elif intent.scope_mode == "selection":
        resumed.resolved_scope = resolved_selection(intent, project_id)
    latest = store.get(project_id, run_id)
    if latest.revision != record.revision:
        return latest
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
    application = get_application()

    store = _store()
    try:
        with store.lock:
            record = store.get(project_id, run_id)
            recovered = _reconcile_zone_commit(record, store, application)
            if recovered is not None:
                return recovered
            if record.state.status != "queued":
                return record
            model = local.configured_model()
            if not model:
                raise HTTPException(503, "Локальная модель не подключена")
            record, scheduled = store.claim_execution(project_id, run_id)
            if scheduled:
                background_tasks.add_task(_run_in_background, project_id, run_id, model, record.state.execution_attempt_id, application, store)
            return record
    except KeyError as error:
        raise HTTPException(404, "Запуск агента не найден") from error
