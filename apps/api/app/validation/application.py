from __future__ import annotations

import json
from hashlib import sha256

from app.planning.contracts import Plan
from app.projects.contracts import Project
from app.validation.contracts import PlanValidationBasis
from app.validation.ports import PlanValidatorPort


class PlanValidation:
    def __init__(self, validator: PlanValidatorPort) -> None:
        self.validator = validator

    def refresh(
        self, project: Project, plan: Plan, *, increment_version: bool = False
    ) -> None:
        if increment_version:
            plan.version += 1
        plan.issues = self.validator.validate_plan(project, plan)
        objects = [
            item.model_dump(mode="json", exclude={"status"})
            for item in sorted(plan.objects, key=lambda item: item.id)
        ]
        plan.validation_basis = PlanValidationBasis(
            plan_version=plan.version,
            geometry_version=project.geometry_version,
            validator_revision=self.validator.revision,
            objects_digest=sha256(
                json.dumps(
                    objects, sort_keys=True, ensure_ascii=False, separators=(",", ":")
                ).encode("utf-8")
            ).hexdigest(),
        )
