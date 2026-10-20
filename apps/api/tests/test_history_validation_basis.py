"""History retains edits without presenting a previous input basis as current."""

from collections.abc import Iterator
from pathlib import Path
from typing import Literal
from unittest.mock import Mock

import pytest
from test_utility_context import mapped_project

from app.geometry.networks.utility_contracts import UtilityContext
from app.history.adapters import SqliteProjectHistory
from app.planning.contracts import PlanObject
from app.projects.adapters import SqliteProjectRepository
from app.projects.contracts import Project
from app.validation.adapters import RuleBasedPlanValidator
from app.validation.application import PlanValidation
from app.validation.contracts import CalculationInputBasis

HistorySetup = tuple[SqliteProjectRepository, SqliteProjectHistory, Project, Project]


def input_basis(project: Project) -> CalculationInputBasis:
    """Controlled external basis oracle; history must not derive it itself."""
    context = project.layers[1].utility_context
    assert context is not None
    return CalculationInputBasis(
        geometry_digest="a" * 64,
        calculation_digest=("b" if context.network_type == "gas" else "d") * 64,
        evidence_digest=("c" if context.confirmed_by == "Reviewer" else "e") * 64,
    )


@pytest.fixture(params=["sqlite-memory", "sqlite-file"])
def saved_history(
    request: pytest.FixtureRequest, tmp_path: Path
) -> Iterator[HistorySetup]:
    repository = SqliteProjectRepository(
        ":memory:" if request.param == "sqlite-memory" else tmp_path / "history.sqlite3"
    )
    history = SqliteProjectHistory(repository, basis_reader=input_basis)
    project = mapped_project()
    assert project.plan is not None
    project.plan.objects = [PlanObject(id="first", kind="tree", x=20, y=50, radius=0.5)]
    validation = PlanValidation(RuleBasedPlanValidator(), basis_reader=input_basis)
    validation.refresh(project, project.plan)
    before = repository.create(project)
    after = before.model_copy(deep=True)
    assert after.plan is not None
    after.plan.objects.append(
        PlanObject(id="second", kind="tree", x=40, y=50, radius=0.5)
    )
    validation.refresh(after, after.plan, increment_version=True)
    history.commit(
        after, before, "Add tree", "change", receipt={"added_ids": ["second"]}
    )
    yield repository, history, before, after
    repository.close()


@pytest.mark.parametrize(
    "field,value",
    [
        ("network_type", "water"),
        ("confirmed_by", "Second reviewer"),
    ],
)
def test_undo_redo_retains_historical_findings_but_invalidates_changed_inputs(
    saved_history: HistorySetup,
    field: Literal["network_type", "confirmed_by"],
    value: str,
) -> None:
    repository, history, before, after = saved_history
    current = repository.get(before.id)
    assert current.layers[1].utility_context is not None
    current.layers[1].utility_context = UtilityContext.model_validate(
        {
            **current.layers[1].utility_context.model_dump(),
            field: value,
        }
    )
    current = repository.save(current)
    undone = history.undo(current)
    assert undone.plan is not None and before.plan is not None
    assert undone.plan.objects == before.plan.objects
    assert undone.plan.issues == before.plan.issues
    assert undone.plan.validation_basis is None
    assert getattr(undone.layers[1].utility_context, field) == value
    redone = history.redo(repository.get(before.id))
    assert redone.plan is not None and after.plan is not None
    assert redone.plan.objects == after.plan.objects
    assert redone.plan.issues == after.plan.issues
    assert redone.plan.validation_basis is None
    assert redone.layers[1].utility_context == undone.layers[1].utility_context


def test_known_unchanged_inputs_preserve_validated_history_basis(
    saved_history: HistorySetup,
) -> None:
    repository, history, before, after = saved_history
    undone = history.undo(repository.get(before.id))
    assert undone.plan is not None and after.plan is not None
    assert before.plan is not None and before.plan.validation_basis is not None
    assert undone.plan.version > after.plan.version
    assert undone.plan.validation_basis == before.plan.validation_basis.model_copy(
        update={"plan_version": undone.plan.version}
    )
    redone = history.redo(repository.get(before.id))
    assert redone.plan is not None and after.plan.validation_basis is not None
    assert redone.plan.version > undone.plan.version
    assert redone.plan.validation_basis == after.plan.validation_basis.model_copy(
        update={"plan_version": redone.plan.version}
    )


def test_sqlite_history_targets_remain_exact_beyond_visible_entries(
    tmp_path: Path,
) -> None:
    repository = SqliteProjectRepository(tmp_path / "deep-history.sqlite3")
    history = SqliteProjectHistory(repository)
    project = repository.create(mapped_project())
    try:
        for index in range(1, 31):
            before = repository.get(project.id)
            assert before.plan is not None
            after = before.model_copy(
                update={"plan": before.plan.model_copy(deep=True)}
            )
            assert after.plan is not None
            after.plan.version += 1
            project = history.commit(after, before, f"Edit {index}", f"change-{index}")
        assert project.plan is not None
        previous_version = project.plan.version
        for _ in range(21):
            project = history.undo(repository.get(project.id))
            assert project.plan is not None
            assert project.plan.version == previous_version + 1
            previous_version = project.plan.version
        state = history.state(project.id)
        assert state.cursor_ordinal == 9
        assert state.undo_target_id == "change-9"
        assert state.redo_target_id == "change-10"
        assert "change-9" not in {entry.id for entry in state.entries}
        project = history.undo(repository.get(project.id))
        assert project.plan is not None
        assert project.plan.version == previous_version + 1
        state = history.state(project.id)
        assert state.cursor_ordinal == 8
        assert state.redo_target_id == "change-9"
    finally:
        repository.close()


@pytest.mark.parametrize("legacy", [False, True])
def test_unavailable_or_legacy_unknown_basis_cannot_become_fresh_on_restore(
    saved_history: HistorySetup, legacy: bool
) -> None:
    repository, history, before, _ = saved_history
    if legacy:
        # A history payload written before digests existed is unknown even
        # when today's local reader can resolve a complete input basis.
        history.clear(before.id)
        historical = before.model_copy(deep=True)
        assert historical.plan is not None
        assert historical.plan.validation_basis is not None
        historical.plan.validation_basis = historical.plan.validation_basis.model_copy(
            update={
                "geometry_digest": None,
                "calculation_digest": None,
                "evidence_digest": None,
            }
        )
        history.commit(repository.get(before.id), historical, "Legacy edit")
    else:
        history.set_basis_reader(None)
    restored = history.undo(repository.get(before.id))
    assert restored.plan is not None and before.plan is not None
    assert restored.plan.objects == before.plan.objects
    assert restored.plan.validation_basis is None


def test_basis_read_failure_does_not_commit_project_history_cursor_or_receipt(
    saved_history: HistorySetup,
) -> None:
    repository, history, before, _ = saved_history
    original = repository.get(before.id)
    original_state = history.state(before.id)
    original_receipt = history.receipt(before.id, "change")

    def unavailable(project: Project) -> CalculationInputBasis:
        raise ValueError("Prepared basis is corrupt")

    history.set_basis_reader(unavailable)
    with pytest.raises(ValueError, match="corrupt"):
        history.undo(repository.get(before.id))
    assert repository.get(before.id) == original
    assert history.state(before.id) == original_state
    assert history.receipt(before.id, "change") == original_receipt


def test_validation_resolves_basis_before_mutating_a_plan() -> None:
    project = mapped_project()
    assert project.plan is not None
    original = project.plan.model_copy(deep=True)
    validator = Mock()
    reader = Mock(side_effect=ValueError("Prepared basis is corrupt"))
    validation = PlanValidation(validator, basis_reader=reader)
    with pytest.raises(ValueError, match="corrupt"):
        validation.refresh(project, project.plan, increment_version=True)
    assert project.plan == original
    validator.validate_plan.assert_not_called()
