"""Capability discovery for the autonomous agent.

The registry adapts the existing domain bridge without exposing Python
callables or the database to the model.  New platform capabilities register
here; the orchestrator does not need a new language-routing branch for each one.
"""

from dataclasses import dataclass
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agent_tools import REGISTRY
from app.contracts import PlanChangeSetApplyRequest


EffectClass = Literal["read", "preview", "write", "control"]


class CommitChangeSetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preview_id: str = Field(min_length=1, max_length=200)
    digest: str = Field(min_length=1, max_length=200)
    base_plan_version: int = Field(ge=1)


class ExistingChangePrepareQuery(BaseModel):
    """Typed preview request for editing or deleting existing plantings."""

    model_config = ConfigDict(extra="forbid")

    base_plan_version: int = Field(ge=1)
    operation: Literal["edit", "delete"]
    zone_ids: list[str] = Field(default_factory=list, max_length=80)
    object_ids: list[str] = Field(default_factory=list, max_length=5000)
    plant_kind: Literal["tree", "shrub"] | None = None
    species_revision_ids: list[str] = Field(default_factory=list, max_length=10)
    quantity: int | None = Field(default=None, ge=1, le=5000)
    quantity_mode: Literal["target", "maximum"] = "target"
    edit_action: Literal["species", "move", "lock", "unlock"] | None = None
    move_dx_m: float | None = Field(default=None, allow_inf_nan=False)
    move_dy_m: float | None = Field(default=None, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_target_and_action(self):
        if bool(self.zone_ids) == bool(self.object_ids):
            raise ValueError("Укажите либо zone_ids, либо object_ids")
        if self.operation == "delete" and self.edit_action is not None:
            raise ValueError("Для delete edit_action не используется")
        if self.operation == "edit" and self.edit_action is None:
            raise ValueError("Для edit укажите edit_action")
        if self.edit_action == "move" and self.move_dx_m is None and self.move_dy_m is None:
            raise ValueError("Для move укажите move_dx_m или move_dy_m")
        if self.edit_action == "species" and len(self.species_revision_ids) != 1:
            raise ValueError("Для species укажите одну species_revision_id")
        return self


def _commit_change_set(application, project_id: str, request: CommitChangeSetRequest):
    return application.apply_change_set(project_id, PlanChangeSetApplyRequest(
        preview_id=request.preview_id,
        digest=request.digest,
        base_plan_version=request.base_plan_version,
    ))


def _prepare_existing_change(application, project_id: str, request: ExistingChangePrepareQuery):
    """Adapt one typed request into the existing validated task planner."""
    from app.agent_memory import TaskPatch, TaskState
    from app.agent_planning import prepare_task

    project = application.get(project_id, lightweight=True)
    if project.plan is None:
        raise ValueError("В проекте пока нет посадок")
    if project.plan.version != request.base_plan_version:
        raise ValueError("План изменился до подготовки предложения")
    scope = "objects" if request.object_ids else "zones"
    values = {
        "operation": request.operation,
        "scope": scope,
        "zone_ids": list(request.zone_ids) or None,
        "object_ids": list(request.object_ids) or None,
        "plant_kind": request.plant_kind,
        "species_revision_ids": list(request.species_revision_ids) or None,
        "quantity": request.quantity,
        "quantity_mode": request.quantity_mode,
        "edit_action": request.edit_action,
        "move_dx_m": request.move_dx_m,
        "move_dy_m": request.move_dy_m,
    }
    if request.edit_action == "species":
        values["species_mode"] = "specified"
    task = TaskState(
        values=TaskPatch(**values),
        provenance={"runtime": "agent-runtime"},
    )
    prepared = prepare_task(application, project_id, task)
    prepared["operation"] = request.operation
    prepared["requires_confirmation"] = bool(prepared.get("change_set", {}).get("can_apply"))
    return prepared


@dataclass(frozen=True)
class Capability:
    name: str
    description: str
    arguments: type[BaseModel]
    effect: EffectClass
    domain: str
    approval_required: bool
    idempotent: bool
    executor: Callable[[Any, str, BaseModel], Any]

    def descriptor(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "domain": self.domain,
            "effect": self.effect,
            "approval_required": self.approval_required,
            "idempotent": self.idempotent,
            "parameters": self.arguments.model_json_schema(),
        }


def _domain(name: str) -> str:
    if name.startswith(("species", "growth")):
        return "catalog"
    if name.startswith(("preview", "check_placement", "building", "road")):
        return "placement"
    if name.startswith(("plan", "prepare_existing", "find_plantings", "inspect_plantings")):
        return "plan"
    if name.startswith(("data", "query_geometry", "project", "inspect_zones", "select_zone")):
        return "project"
    return "project"


def _effect(name: str, effect: str) -> EffectClass:
    # The legacy bridge currently has read and preview operations.  This
    # mapping is intentionally conservative: a future write capability must
    # declare its effect explicitly instead of being inferred as safe.
    return "preview" if effect == "preview" else "read"


def _capabilities() -> tuple[Capability, ...]:
    read_preview = tuple(
        Capability(
            name=tool.name,
            description=tool.description,
            arguments=tool.arguments,
            effect=_effect(tool.name, tool.effect),
            domain=_domain(tool.name),
            approval_required=tool.effect != "read",
            idempotent=True,
            executor=tool.execute,
        )
        for tool in REGISTRY.values()
    )
    return read_preview + (
        Capability(
            name="prepare_existing_change",
            description="Подготовить проверяемое изменение или удаление существующих посадок",
            arguments=ExistingChangePrepareQuery,
            effect="preview",
            domain="plan",
            approval_required=True,
            idempotent=True,
            executor=_prepare_existing_change,
        ),
        Capability(
            name="commit_change_set",
            description="Применить ранее проверенный change-set после подтверждения пользователя",
            arguments=CommitChangeSetRequest,
            effect="write",
            domain="plan",
            approval_required=True,
            idempotent=False,
            executor=_commit_change_set,
        ),
    )


class CapabilityRegistry:
    """One policy-filtered catalog for in-process and MCP tool adapters."""

    def __init__(self, capabilities: tuple[Capability, ...] | None = None):
        self._capabilities = {item.name: item for item in (capabilities or _capabilities())}

    def get(self, name: str) -> Capability:
        try:
            return self._capabilities[name]
        except KeyError as error:
            raise KeyError(f"Unknown capability: {name}") from error

    def discover(self, *, query: str | None = None,
                 effects: set[EffectClass] | None = None,
                 domains: set[str] | None = None) -> list[dict]:
        normalized = query.casefold().strip() if query else ""
        result = []
        for item in self._capabilities.values():
            if effects is not None and item.effect not in effects:
                continue
            if domains is not None and item.domain not in domains:
                continue
            searchable = f"{item.name} {item.description} {item.domain}".casefold()
            if normalized and normalized not in searchable:
                continue
            result.append(item.descriptor())
        return sorted(result, key=lambda value: (value["domain"], value["name"]))

    def describe(self, name: str) -> dict:
        return self.get(name).descriptor()

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._capabilities))
