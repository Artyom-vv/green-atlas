from typing import Protocol

from app.planning.contracts import Plan
from app.projects.contracts import Project
from app.validation.contracts import ValidationIssue


class PlanValidatorPort(Protocol):
    revision: str

    # Rechecking identical inputs must preserve issue identities and values.
    def validate_plan(self, project: Project, plan: Plan) -> list[ValidationIssue]: ...

    def discard(self, project_id: str) -> None: ...
