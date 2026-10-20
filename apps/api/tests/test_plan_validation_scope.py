"""A full persisted validation holds one checked calculation scope, including basis."""

from collections.abc import Iterator
from contextlib import contextmanager
from functools import partial
from unittest.mock import Mock

import pytest
from test_binary_prepared_engine import saved
from test_geometry_preparation import geometry_fake, project_with
from test_native_live_provider import setup as setup

from app.geometry.preparation import calculation_scope
from app.native_query.preparation.prepared_snapshot import PreparedGeometryUnavailable
from app.native_query.preparation.prepared_storage import SqlitePreparedResource
from app.native_query.project_runtime import ProjectLiveGeometry
from app.validation.adapters import RuleBasedPlanValidator
from app.validation.application import PlanValidation
from app.validation.contracts import CalculationInputBasis


class ScopedQueries:
    def __init__(self):
        self.geometry = geometry_fake()
        self.depth = 0
        self.outer_entries = 0
        self.exit_error = None
        for name in (
            "prepare_positions",
            "position_violation",
            "placement_advisory_detail",
            "future_growth_advisory_detail",
            "position_rule_trace",
        ):
            method = getattr(self.geometry, name)
            original = method.side_effect

            def inside(*args, _name=name, _original=original, **kwargs):
                assert self.depth > 0, f"{_name} is outside checked plan scope"
                return _original(*args, **kwargs)

            method.side_effect = inside

    def __getattr__(self, name):
        return getattr(self.geometry, name)

    @contextmanager
    def calculation_scope(self, project) -> Iterator[None]:
        self.outer_entries += int(self.depth == 0)
        self.depth += 1
        try:
            yield
            if self.exit_error:
                raise self.exit_error
        finally:
            self.depth -= 1


def test_direct_full_validator_keeps_every_point_query_in_one_scope() -> None:
    project = project_with((10, 5), (40, 5), (70, 5))
    geometry = ScopedQueries()
    assert project.plan is not None
    issues = RuleBasedPlanValidator(geometry=geometry).validate_plan(
        project, project.plan
    )
    assert issues
    assert geometry.outer_entries == 1 and geometry.depth == 0
    assert geometry.geometry.prepare_positions.call_count == 1
    assert geometry.geometry.position_violation.call_count == 3
    assert geometry.geometry.position_rule_trace.call_count == 3


def test_refresh_keeps_basis_reader_and_full_validator_in_the_same_scope() -> None:
    project = project_with((10, 5), (40, 5))
    geometry = ScopedQueries()
    validator = RuleBasedPlanValidator(geometry=geometry)
    basis_reads = []

    def basis_reader(current):
        assert current is project
        assert geometry.depth > 0, "Basis was read outside the full validation scope"
        basis_reads.append(geometry.outer_entries)
        return CalculationInputBasis(
            geometry_digest="a" * 64,
            calculation_digest="b" * 64,
            evidence_digest="c" * 64,
        )

    validation = PlanValidation(
        validator,
        basis_reader=basis_reader,
        calculation_scope_factory=partial(calculation_scope, geometry),
    )
    assert project.plan is not None
    validation.refresh(project, project.plan)
    assert basis_reads == [1]
    assert geometry.outer_entries == 1 and geometry.depth == 0
    assert project.plan.validation_basis is not None
    assert project.plan.validation_basis.geometry_digest == "a" * 64


def test_scope_exit_failure_does_not_publish_new_issues_or_validation_basis() -> None:
    project = project_with((10, 5))
    geometry = ScopedQueries()
    geometry.exit_error = PreparedGeometryUnavailable("corrupt")
    validation = PlanValidation(
        RuleBasedPlanValidator(geometry=geometry),
        calculation_scope_factory=partial(calculation_scope, geometry),
    )
    assert project.plan is not None
    before = project.model_dump_json()
    with pytest.raises(PreparedGeometryUnavailable):
        validation.refresh(project, project.plan, increment_version=True)
    assert geometry.depth == 0
    assert project.model_dump_json() == before


def test_prepared_full_validation_holds_one_connection_for_basis_and_all_points(
    setup, tmp_path, monkeypatch
) -> None:
    project, _, store, client = saved(setup, tmp_path)
    project.plan = project_with((5, 0), (23, 0), (50, 0), (75, 0)).plan
    assert project.plan is not None
    router = ProjectLiveGeometry(Mock(), prepared_store=store)
    engine = router.load_local(project)
    resource = engine.snapshot
    assert isinstance(resource, SqlitePreparedResource)
    real_read_scope = resource.read_scope
    outer_connections = []

    @contextmanager
    def counted_scope():
        was_active = getattr(resource._sessions, "active", None) is not None
        with real_read_scope():
            if not was_active:
                outer_connections.append(resource._sessions.active[0])
            yield

    monkeypatch.setattr(resource, "read_scope", counted_scope)
    original_basis = router.validation_input_basis

    def checked_basis(current):
        assert getattr(resource._sessions, "active", None) is not None
        return original_basis(current)

    validation = PlanValidation(
        RuleBasedPlanValidator(geometry=router),
        basis_reader=checked_basis,
        calculation_scope_factory=partial(calculation_scope, router),
    )
    validation.refresh(project, project.plan)
    assert len(outer_connections) == 1
    assert getattr(resource._sessions, "active", None) is None
    assert project.plan.validation_basis is not None
    assert client.calls == []


class CountingScopes:
    def __init__(self):
        self.entries = []
        self.enter_error = None
        self.exit_error = None

    @contextmanager
    def calculation_scope(self, project):
        from threading import get_ident

        self.entries.append(
            (get_ident(), project.id, project.state_version, project.geometry_version)
        )
        if self.enter_error:
            raise self.enter_error
        yield
        if self.exit_error:
            raise self.exit_error


def test_shared_scope_deduplicates_same_input_but_keeps_other_projects_and_revisions() -> (
    None
):
    geometry = CountingScopes()
    project = project_with()
    with calculation_scope(geometry, project):
        with calculation_scope(geometry, project.model_copy(deep=True)):
            assert len(geometry.entries) == 1
        for changed in (
            project.model_copy(update={"id": "other-project"}),
            project.model_copy(update={"state_version": project.state_version + 1}),
            project.model_copy(
                update={"geometry_version": project.geometry_version + 1}
            ),
        ):
            with calculation_scope(geometry, changed):
                assert geometry.entries[-1][1:] == (
                    changed.id,
                    changed.state_version,
                    changed.geometry_version,
                )
        assert len(geometry.entries) == 4
        with calculation_scope(geometry, project):
            assert len(geometry.entries) == 4
    with calculation_scope(geometry, project):
        assert len(geometry.entries) == 5


def test_shared_scope_does_not_combine_different_providers() -> None:
    first, second, project = CountingScopes(), CountingScopes(), project_with()
    with calculation_scope(first, project), calculation_scope(second, project):
        assert len(first.entries) == len(second.entries) == 1
        with calculation_scope(first, project), calculation_scope(second, project):
            assert len(first.entries) == len(second.entries) == 1


@pytest.mark.parametrize("failure_stage", ["enter", "body", "exit"])
def test_shared_scope_recovers_after_any_exception_without_retaining_old_resource(
    failure_stage: str,
) -> None:
    geometry, project = CountingScopes(), project_with()
    failure = RuntimeError(f"Failure at scope {failure_stage}")
    geometry.enter_error = failure if failure_stage == "enter" else None
    geometry.exit_error = failure if failure_stage == "exit" else None
    with pytest.raises(RuntimeError) as caught, calculation_scope(geometry, project):
        if failure_stage == "body":
            raise failure
    assert caught.value is failure
    geometry.enter_error = geometry.exit_error = None
    with calculation_scope(geometry, project):
        assert len(geometry.entries) == 2
        with calculation_scope(geometry, project):
            assert len(geometry.entries) == 2


def test_shared_scope_never_reuses_another_threads_checked_resource() -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, get_ident

    geometry, project, ready = CountingScopes(), project_with(), Barrier(2)
    main_thread = get_ident()

    def worker():
        with calculation_scope(geometry, project):
            thread = get_ident()
            ready.wait(timeout=5)
            with calculation_scope(geometry, project):
                assert sum(entry[0] == thread for entry in geometry.entries) == 1
            return thread

    with (
        calculation_scope(geometry, project),
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        first = pool.submit(worker)
        second = pool.submit(worker)
        assert first.result(timeout=10) != second.result(timeout=10)
        assert len(geometry.entries) == 3
        with calculation_scope(geometry, project):
            assert sum(entry[0] == main_thread for entry in geometry.entries) == 1
