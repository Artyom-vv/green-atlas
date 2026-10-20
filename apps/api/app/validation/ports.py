"""Порт повторной проверки плана с устойчивой идентичностью замечаний и явной ревизией
правил.
"""

from typing import Protocol

from app.planning.contracts import Plan
from app.projects.contracts import Project
from app.validation.contracts import ValidationIssue


class PlanValidatorPort(Protocol):
    revision: str

    # Повторная проверка того же входа сохраняет идентичность и значения замечаний.
    def validate_plan(self, project: Project, plan: Plan) -> list[ValidationIssue]: ...

    def validate_growth_scenario(
        self, project: Project, plan: Plan, year: int
    ) -> list[ValidationIssue]: ...

    def discard(self, project_id: str) -> None: ...
