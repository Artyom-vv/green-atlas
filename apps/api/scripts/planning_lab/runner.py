"""Compose real planning services against a read-only, in-memory snapshot."""

from datetime import UTC, datetime
from itertools import count
from threading import RLock
from time import perf_counter

from app.geometry.adapters import ShapelyGeometryEngine
from app.history.adapters import InMemoryProjectHistory
from app.history.application import PlanHistoryApplication
from app.planning.changes import ChangeSetApplication
from app.planning.domain import PlanVersionConflict
from app.planning.evaluation import PlanEvaluation
from app.planning.pattern_application import PatternApplication
from app.planning.patterns import ShapelyCandidateGenerator
from app.projects.contracts import Project
from app.validation.adapters import RuleBasedPlanValidator
from app.validation.application import PlanValidation

from .contracts import PlanningCase
from .evidence import digest, implementation_basis
from .recording import RecordingGenerator, RecordingPreviews


class FrozenProjectReader:
    def __init__(self, project: Project) -> None:
        self.project = project.model_copy(deep=True)

    def get(self, project_id: str, *, lightweight: bool = False) -> Project:
        if project_id != self.project.id:
            raise KeyError(project_id)
        return self.project.model_copy(deep=True)

    def save(
        self, project: Project, *, source: bytes | bytearray | None = None
    ) -> Project:
        raise RuntimeError("Planning lab cannot mutate a project")


def run_case(case: PlanningCase) -> dict:
    case_input = case.model_dump(mode="json")
    input_hash = digest(case_input)
    basis = implementation_basis()
    reader = FrozenProjectReader(case.project)
    geometry = ShapelyGeometryEngine()
    identity_sequence = count()

    def new_id() -> str:
        return f"lab-{input_hash[:16]}-{next(identity_sequence)}"

    # A laboratory clock is deliberately fixed and never a production apply
    # token. Project timestamps remain intact in the input and its identity.
    def now() -> datetime:
        return datetime(2000, 1, 1, tzinfo=UTC)

    def invalidate(_project_id: str) -> None:
        raise RuntimeError("Planning lab cannot apply changes")

    lock = RLock()
    history = InMemoryProjectHistory()
    evaluation = PlanEvaluation(geometry, new_id)
    changes = ChangeSetApplication(
        repository=reader,
        evaluation=evaluation,
        validation=PlanValidation(RuleBasedPlanValidator()),
        history_application=PlanHistoryApplication(reader, history, lock, invalidate),
        history=history,
        edit_lock=lock,
        invalidate_spacing=invalidate,
        now=now,
        new_id=new_id,
    )
    generator = RecordingGenerator(ShapelyCandidateGenerator())
    previews = RecordingPreviews(changes)
    scenario = PatternApplication(reader, generator, evaluation, previews)
    start = perf_counter()
    error = None
    result = None
    try:
        result = scenario.preview_pattern(case.project.id, case.request)
    except (ValueError, KeyError, PlanVersionConflict) as exception:
        # A rejected run still has an immutable artifact. Never clear source
        # review or unknown-network flags in order to make an experiment pass.
        error = {"type": type(exception).__name__, "message": str(exception)}
    elapsed = perf_counter() - start
    if digest(case.model_dump(mode="json")) != input_hash:
        raise RuntimeError("Scenario modified its input snapshot")
    content = {
        "schema_version": 1,
        "purpose": "offline_planning_experiment",
        "applied": False,
        "normative_acceptance": "not_established",
        "input_sha256": input_hash,
        "input": case_input,
        "basis": basis,
        "outcome": "rejected" if error else "completed",
        "error": error,
        "generation_calls": generator.calls,
        "validation_calls": previews.calls,
        "result": result.model_dump(mode="json") if result else None,
        "coverage": {
            "generated_candidates": sum(
                len(call["candidates"]) for call in generator.calls
            ),
            "validation_evaluations": sum(
                len(call["draft"]["operations"]) for call in previews.calls
            ),
            "validation_count_includes_final_recheck": True,
            "scope": "All completed generator/preview calls, plus scenario skips. "
            "Not all mathematical points; safe-area exclusions are not individually evaluated.",
        },
    }
    return {
        "content_sha256": digest(content),
        # Compare result identity separately across runtime/code versions.
        "result_sha256": digest(
            {
                "outcome": content["outcome"],
                "error": error,
                "result": content["result"],
                "generation_calls": generator.calls,
                "validation_calls": previews.calls,
            }
        ),
        "content": content,
        "measurements": {
            "scenario_seconds": elapsed,
            "generator_seconds": generator.seconds,
            "preview_seconds": previews.seconds,
        },
    }
