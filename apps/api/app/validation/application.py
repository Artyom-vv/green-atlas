"""Обновление замечаний и отпечатка проверки плана по текущему локальному расчётному
основанию.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from hashlib import sha256

from app.planning.config import DEFAULT_PLANNING_HORIZON_YEAR
from app.planning.contracts import Plan
from app.projects.contracts import Project
from app.validation.contracts import CalculationInputBasis, PlanValidationBasis
from app.validation.ports import PlanValidatorPort


class PlanValidation:
    def __init__(
        self,
        validator: PlanValidatorPort,
        *,
        basis_reader: Callable[[Project], CalculationInputBasis | None] | None = None,
        calculation_scope_factory: Callable[[Project], AbstractContextManager[None]]
        | None = None,
    ) -> None:
        self.validator = validator
        self.basis_reader = basis_reader
        self._scope_factory = calculation_scope_factory

    def calculation_scope(self, project: Project) -> AbstractContextManager[None]:
        """Удерживать подтверждённые ресурсы на всё чтение основания и проверку."""
        if self._scope_factory is not None:
            return self._scope_factory(project)
        method = getattr(type(self.validator), "calculation_scope", None)
        return method(self.validator, project) if callable(method) else nullcontext()

    @contextmanager
    def _checked_update(self, project: Project, plan: Plan) -> Iterator[None]:
        before = plan.model_copy(deep=True)
        try:
            with self.calculation_scope(project):
                yield
        except Exception:
            # Выход из области тоже проверяет целостность. До его успеха результат
            # не должен оставаться опубликованным даже в переданном объекте плана.
            for name in type(plan).model_fields:
                setattr(plan, name, getattr(before, name))
            raise

    def refresh(
        self, project: Project, plan: Plan, *, increment_version: bool = False
    ) -> None:
        with self._checked_update(project, plan):
            self._refresh(project, plan, increment_version=increment_version)

    def _refresh(
        self, project: Project, plan: Plan, *, increment_version: bool = False
    ) -> None:
        # До изменения плана разрешаем локальное расчётное основание. Его отсутствие
        # не должно пометить новые замечания как проверенные по старому снимку.
        basis = self.basis_reader(project) if self.basis_reader else None
        if increment_version:
            plan.version += 1
        plan.issues = self.validator.validate_plan(project, plan)
        plan.validation_basis = self._validation_basis(project, plan, basis)

    def refresh_after_zone_change(
        self, previous: Project, current: Project, plan: Plan
    ) -> None:
        """Переносить актуальные замечания только при доказанной независимости зон."""
        with self._checked_update(current, plan):
            can_reuse = getattr(
                type(self.validator), "can_reuse_after_zone_change", None
            )
            if plan.validation_basis is not None and callable(can_reuse):
                old_input = self.basis_reader(previous) if self.basis_reader else None
                old_basis = self._validation_basis(previous, plan, old_input)
                if plan.validation_basis == old_basis:
                    new_input = (
                        self.basis_reader(current) if self.basis_reader else None
                    )
                    new_basis = self._validation_basis(current, plan, new_input)
                    if new_basis == old_basis.model_copy(
                        update={"geometry_version": current.geometry_version}
                    ) and can_reuse(self.validator, previous, current, plan):
                        plan.validation_basis = new_basis
                        return
            self._refresh(current, plan)

    def is_current(self, project: Project, plan: Plan) -> bool:
        """Сопоставляет сохранённую проверку с действующими условиями без новой записи."""
        with self.calculation_scope(project):
            basis = self.basis_reader(project) if self.basis_reader else None
            return plan.validation_basis == self._validation_basis(project, plan, basis)

    def _validation_basis(
        self, project: Project, plan: Plan, basis: CalculationInputBasis | None
    ) -> PlanValidationBasis:
        objects = [
            item.model_dump(mode="json", exclude={"status"})
            for item in sorted(plan.objects, key=lambda item: item.id)
        ]
        return PlanValidationBasis(
            plan_version=plan.version,
            geometry_version=project.geometry_version,
            validator_revision=self.validator.revision,
            planning_horizon_year=DEFAULT_PLANNING_HORIZON_YEAR,
            objects_digest=sha256(
                json.dumps(
                    objects, sort_keys=True, ensure_ascii=False, separators=(",", ":")
                ).encode("utf-8")
            ).hexdigest(),
            geometry_digest=basis.geometry_digest if basis else None,
            calculation_digest=basis.calculation_digest if basis else None,
            evidence_digest=basis.evidence_digest if basis else None,
        )
