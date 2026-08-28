from typing import Protocol

from app.contracts import Plan, Project, ValidationIssue


class PlanValidatorPort(Protocol):
    def validate_plan(self, project: Project, plan: Plan) -> list[ValidationIssue]: ...

    def discard(self, project_id: str) -> None: ...
