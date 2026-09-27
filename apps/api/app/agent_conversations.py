"""Conversation transport, separate from project mutation endpoints."""
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, computed_field

from app import planning_assistant as local
from app.agent_interpreter import interpret_task, project_context
from app.agent_loop import run_placement_agent, run_read_agent
from app.agent_memory import (
    ConversationConflict,
    ConversationStore,
    TaskPatch,
    TaskState,
)
from app.agent_perception import MapContext, StaleMapContext, bind_selection, perceive
from app.agent_planning import prepare_task as prepare_agent_task
from app.agent_readiness import next_question
from app.composition import get_application, get_conversation_store
from app.contracts import PlanChangeSetApplyRequest, PlanMutationResult
from app.http_errors import handle


class ConversationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(default="Новый диалог", min_length=1, max_length=160)
    request_id: str | None = Field(default=None, min_length=1, max_length=200)


class ConversationSummary(BaseModel):
    id: str
    title: str
    revision: int
    created_at: str
    updated_at: str


class ConversationRecord(BaseModel):
    record_id: str
    sequence: int
    kind: Literal["message", "tool_event", "annotation"]
    payload: dict[str, Any]
    created_at: str


class Conversation(ConversationSummary):
    project_id: str
    task: TaskState
    records: list[ConversationRecord]

    @computed_field
    @property
    def can_prepare(self) -> bool:
        return self.task.values.operation in {"place", "edit", "delete"} and next_question(self.task) is None


class LegacyMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=200)
    role: Literal["user", "assistant"]
    text: str = Field(min_length=1, max_length=2000)
    result: str | None = Field(default=None, max_length=2000)


class LegacyImport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str = Field(min_length=1, max_length=200)
    messages: list[LegacyMessage] = Field(max_length=1000)


class UserMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    record_id: str = Field(min_length=1, max_length=200)
    expected_revision: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=2000)
    map_context: MapContext | None = None

    def content(self) -> dict:
        payload = {"role": "user", "text": self.text}
        if self.map_context is not None:
            payload["map_context"] = self.map_context.model_dump(mode="json")
        return payload


class PrepareTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    record_id: str = Field(min_length=1, max_length=200)
    expected_revision: int = Field(ge=1)


class ProposalStatus(BaseModel):
    record_id: str
    status: Literal["ready", "blocked", "expired", "stale", "applied", "undone", "declined", "unavailable", "superseded"]
    can_apply: bool


class ConfirmProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)


def project_store(project_id: str) -> ConversationStore:
    # Same project lookup as the existing local API. Do not create orphan chats.
    application = get_application()
    try:
        application.get(project_id, lightweight=True)
    except KeyError as error:
        raise HTTPException(404, "Проект не найден") from error
    return get_conversation_store()


router = APIRouter(prefix="/api/projects/{project_id}/conversations", tags=["Project conversations"])


@router.get("", response_model=list[ConversationSummary])
def conversations(project_id: str, store: ConversationStore = Depends(project_store)):
    return store.list(project_id)


@router.post("", response_model=Conversation, status_code=201)
def create(project_id: str, request: ConversationCreate, store: ConversationStore = Depends(project_store)):
    try:
        return store.create(project_id, request.title, request_id=request.request_id)
    except ConversationConflict as error:
        raise HTTPException(409, "Запрос создания уже использован для другого диалога.") from error


@router.post("/import", response_model=Conversation)
def import_legacy(project_id: str, request: LegacyImport, store: ConversationStore = Depends(project_store)):
    try:
        return store.import_legacy(project_id, request.source_id, [message.model_dump() for message in request.messages])
    except ConversationConflict as error:
        raise HTTPException(409, "Исходная переписка изменилась. Сохраните её как отдельный диалог.") from error


@router.get("/{conversation_id}", response_model=Conversation)
def conversation(project_id: str, conversation_id: str, store: ConversationStore = Depends(project_store)):
    try:
        return store.get(project_id, conversation_id)
    except KeyError as error:
        raise HTTPException(404, "Диалог не найден") from error


@router.post("/{conversation_id}/messages", response_model=Conversation)
def append_message(project_id: str, conversation_id: str, request: UserMessage, store: ConversationStore = Depends(project_store)):
    # The browser cannot forge assistant answers, tool results or task patches.
    try:
        chat = store.get(project_id, conversation_id)
        previous = next((record for record in chat["records"] if record["record_id"] == request.record_id), None)
        if previous:
            if previous["kind"] != "message" or previous["payload"]["content"] != request.content() or previous["payload"].get("assistant_text") is not None:
                raise ConversationConflict("Record id is already used")
            return chat
        if request.map_context is not None:
            application = get_application()
            perceive(application.get(project_id, lightweight=True), request.map_context)
        return store.append(project_id, conversation_id, expected_revision=request.expected_revision,
                            record_id=request.record_id, kind="message", payload=request.content())
    except KeyError as error:
        raise HTTPException(404, "Диалог не найден") from error
    except ConversationConflict as error:
        raise HTTPException(409, "Переписка изменилась. Обновите диалог.") from error
    except StaleMapContext as error:
        raise HTTPException(409, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.post("/{conversation_id}/interpret", response_model=Conversation)
def interpret_message(project_id: str, conversation_id: str, request: UserMessage, store: ConversationStore = Depends(project_store)):
    application = get_application()
    try:
        chat = store.get(project_id, conversation_id)
    except KeyError as error:
        raise HTTPException(404, "Диалог не найден") from error
    for record in chat["records"]:
        if record["record_id"] == request.record_id:
            if record["payload"]["content"] != request.content() or "assistant_text" not in record["payload"]:
                raise HTTPException(409, "Сообщение с этим идентификатором уже существует")
            return chat
    if chat["revision"] != request.expected_revision:
        raise HTTPException(409, "Переписка изменилась. Обновите диалог.")
    model = local.configured_model()
    if not model:
        raise HTTPException(503, "Локальная модель не подключена")
    if not local._busy.acquire(blocking=False):
        raise HTTPException(429, "Помощник обрабатывает предыдущий запрос")
    try:
        project = application.get(project_id, lightweight=True)
        perception = perceive(project, request.map_context) if request.map_context else None
        context = project_context(project)
        if perception is not None:
            context["map_context"] = perception
        patch, interpretation = interpret_task(request.text, TaskState.model_validate(chat["task"]), chat["records"], context, model)
        patch = bind_selection(patch, perception)
        if interpretation.operation == "inspect" or patch.operation == "inspect":
            history = [record["payload"]["content"] for record in chat["records"] if record["kind"] == "message"][-12:]
            result = run_read_agent(application, project_id, request.text, model,
                                    context={**context, "task": chat["task"], "history": history})
            if result.status == "stale":
                raise StaleMapContext(result.text)
            if application.get(project_id, lightweight=True).state_version != project.state_version:
                raise StaleMapContext("Проект изменился. Повторите запрос.")
            # Inspection is a side question, not a replacement of the retained
            # placement/edit task. Save source, evidence and answer atomically.
            return store.append(project_id, conversation_id, expected_revision=request.expected_revision,
                                record_id=request.record_id, kind="message", payload=request.content(),
                                patch=TaskPatch(), assistant_text=result.text,
                                tool_result={"event": "project_inspected", "state_version": project.state_version,
                                             "inspection": result.model_dump(mode="json")})
        if request.map_context is not None:
            # The model may take seconds. Do not accept references from an old map.
            perceive(application.get(project_id, lightweight=True), request.map_context)
        # Until the executor is connected, never pretend this is a plan preview.
        retained = (TaskState() if interpretation.intent == "new_task" else TaskState.model_validate(chat["task"])).amended(patch, request.record_id)
        if interpretation.intent != "discuss" and retained.values.operation in {"place", "edit", "delete"}:
            answer = next_question(retained) or "Условия задания сохранены. План пока не изменён."
        else:
            answer = interpretation.question or "Уточните, какие сведения о проекте необходимо проверить."
        if patch.scope == "selection":
            answer = "Какие объекты использовать: выбранные участки или отдельные посадки?" if perception and perception["selected_zone_ids"] and perception["selected_objects"] else "Выберите участок или посадки на карте для этого задания."
        return store.append(project_id, conversation_id, expected_revision=request.expected_revision, record_id=request.record_id,
                            kind="message", payload=request.content(), patch=patch,
                            assistant_text=answer, reset_task=interpretation.intent == "new_task")
    except ConversationConflict as error:
        raise HTTPException(409, "Переписка изменилась. Обновите диалог.") from error
    except StaleMapContext as error:
        raise HTTPException(409, str(error)) from error
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise HTTPException(503, "Не удалось уточнить задание. Переписка и план не изменены.") from error
    finally:
        local._busy.release()


@router.post("/{conversation_id}/prepare", response_model=Conversation)
def prepare_task(project_id: str, conversation_id: str, request: PrepareTask, store: ConversationStore = Depends(project_store)):
    application = get_application()
    try:
        chat = store.get(project_id, conversation_id)
        previous = next((record for record in chat["records"] if record["record_id"] == request.record_id), None)
        if previous:
            if previous["kind"] != "tool_event" or previous["payload"]["content"].get("event") not in {"placement_prepared", "task_prepared"}:
                raise ConversationConflict("Record id is already used")
            return chat
        if chat["revision"] != request.expected_revision:
            raise ConversationConflict("Conversation changed")
        task = TaskState.model_validate(chat["task"])
        if (question := next_question(task)) is not None:
            raise ValueError(question)
        model = local.configured_model() if task.values.operation == "place" else ""
        if model:
            if not local._busy.acquire(blocking=False):
                raise ValueError("Помощник уже готовит предложение")
            try:
                prepared = run_placement_agent(application, project_id, task, model)
            finally:
                local._busy.release()
        else:
            prepared = prepare_agent_task(application, project_id, task)
        return store.append(project_id, conversation_id, expected_revision=request.expected_revision, record_id=request.record_id,
                            kind="tool_event", payload={"event": "task_prepared", "preparation": prepared})
    except ConversationConflict as error:
        raise HTTPException(409, "Задание изменилось. Обновите диалог.") from error
    except KeyError as error:
        raise HTTPException(404, "Проект или диалог не найден") from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.get("/{conversation_id}/proposals/{record_id}/status", response_model=ProposalStatus)
def proposal_status(project_id: str, conversation_id: str, record_id: str, store: ConversationStore = Depends(project_store)):
    application = get_application()
    try:
        chat = store.get(project_id, conversation_id)
        record = next((item for item in chat["records"] if item["record_id"] == record_id), None)
        if record is None or record["kind"] != "tool_event":
            raise KeyError(record_id)
        content = record["payload"]["content"]
        if content.get("event") not in {"task_prepared", "placement_prepared"}:
            raise KeyError(record_id)
        prepared = content["preparation"]
        preview = prepared.get("change_set")
        status = application.change_set_status(project_id, preview["id"], preview["digest"]) if preview else "unavailable"
        if status not in {"applied", "undone"}:
            later = any(item["sequence"] > record["sequence"] and item["kind"] == "tool_event" and
                        item["payload"]["content"].get("event") in {"task_prepared", "placement_prepared"}
                        for item in chat["records"])
            if prepared["task"] != chat["task"] or later:
                status = "superseded"
            if any(item["kind"] == "tool_event" and item["payload"]["content"].get("event") == "proposal_declined" and
                   item["payload"]["content"].get("proposal_record_id") == record_id for item in chat["records"]):
                status = "declined"
        return ProposalStatus(record_id=record_id, status=status, can_apply=status == "ready")
    except KeyError as error:
        raise HTTPException(404, "Предложение не найдено") from error


@router.post("/{conversation_id}/proposals/{proposal_record_id}/decline", response_model=Conversation)
def decline_proposal(project_id: str, conversation_id: str, proposal_record_id: str, request: PrepareTask,
                     store: ConversationStore = Depends(project_store)):
    state = proposal_status(project_id, conversation_id, proposal_record_id, store)
    if state.status in {"applied", "undone"}:
        raise HTTPException(409, "Предложение уже было применено")
    try:
        return store.append(project_id, conversation_id, expected_revision=request.expected_revision,
                            record_id=request.record_id, kind="tool_event",
                            payload={"event": "proposal_declined", "proposal_record_id": proposal_record_id})
    except ConversationConflict as error:
        raise HTTPException(409, "Переписка изменилась. Обновите диалог.") from error


@router.post("/{conversation_id}/proposals/{proposal_record_id}/confirm", response_model=PlanMutationResult)
def confirm_proposal(project_id: str, conversation_id: str, proposal_record_id: str, request: ConfirmProposal,
                     store: ConversationStore = Depends(project_store)):
    application = get_application()
    # Serialize against append/interpret/decline commits in this local server.
    # This is NOT a cross-process SQLite transaction: multi-worker deployment
    # requires moving the conversation revision check into the plan commit.
    with store.lock:
        try:
            chat = store.get(project_id, conversation_id)
            if chat["revision"] != request.expected_revision:
                raise HTTPException(409, "Задание изменилось. Проверьте новое предложение.")
            state = proposal_status(project_id, conversation_id, proposal_record_id, store)
            if not state.can_apply:
                raise HTTPException(409, "Предложение нельзя применить. Обновите диалог.")
            record = next(item for item in chat["records"] if item["record_id"] == proposal_record_id)
            preview = record["payload"]["content"]["preparation"]["change_set"]
            # The browser selects a saved proposal, never supplies replacement
            # coordinates, digest, or a preview from another conversation.
            return application.apply_change_set(project_id, PlanChangeSetApplyRequest(
                preview_id=preview["id"], digest=preview["digest"], base_plan_version=preview["base_plan_version"]))
        except HTTPException:
            raise
        except KeyError as error:
            raise HTTPException(404, "Диалог или предложение не найдено") from error
        except Exception as error:
            raise handle(error) from error
