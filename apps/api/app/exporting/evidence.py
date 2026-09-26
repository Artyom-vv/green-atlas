"""An unavailable check is explicit; draft export must not trigger CAD recovery."""

from app.regulations.profiles import LCT_REQUIREMENT_PROFILE
from app.regulations.registry import REGISTRY_REVISION
from app.regulations.trace_contracts import (
    PlantingRuleTrace,
    RuleTraceBasis,
    RuleTraceEntry,
)

UNAVAILABLE_CHECKS = "Подготовленная расчётная геометрия недоступна. Посадки включены в черновик без повторной проверки"


class UnavailableReleaseEvidence:
    def source_coverage(self, project):
        return None

    def position_rule_trace(
        self, project, x, y, plant_kind, mature_crown_diameter_m=None
    ):
        return PlantingRuleTrace(
            basis=RuleTraceBasis(
                project_id=project.id,
                state_version=project.state_version,
                geometry_version=project.geometry_version,
                plan_version=project.plan.version if project.plan else None,
                requirement_profile=LCT_REQUIREMENT_PROFILE,
                registry_revision=REGISTRY_REVISION,
                source_content_sha256=project.source_file.content_sha256
                if project.source_file
                else None,
            ),
            x=x,
            y=y,
            plant_kind=plant_kind,
            mature_crown_diameter_m=mature_crown_diameter_m,
            entries=[
                RuleTraceEntry(
                    obstacle_kind=kind,
                    status="not_checked",
                    code="GEOMETRY_NOT_READY",
                    note=UNAVAILABLE_CHECKS,
                )
                for kind in (
                    "building",
                    "road",
                    "utility",
                    "existing_green",
                    "restricted",
                )
            ],
        )
