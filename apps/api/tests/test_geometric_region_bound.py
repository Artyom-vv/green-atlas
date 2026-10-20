"""Граница только подготовленной области не отменяет числовой контракт и замечания.

Малый габарит ограничивает число пар внутри этой области. Он не подтверждает
полноту исходника, отсутствие других областей или допуск хотя бы одной посадки.
"""

from math import sqrt

import pytest
from shapely.geometry import box, mapping
from test_geometric_search_acceptance import _application, _request

from app.dxf_import.contracts import SourceFile
from app.geometry.checking.domain import PositionAdvisory
from app.operations.progress import OperationCancelled, operation_cancellation
from app.planning.changes.evaluation import CandidateIssue
from app.planning.contracts import Plan, PlanObject, PlanObjectCreate
from app.planning.domain import PlantSpacingIndex
from app.planning.ports import PatternCandidate
from app.planning.search import geometric_search
from app.planning.search.geometry_candidates import GeometryCandidateSearch
from app.projects.contracts import Project


@pytest.mark.parametrize("minimum", [0.0, -1e-16])
def test_a_float_allowed_pair_is_not_removed_by_an_exact_rational_bound(
    minimum: float,
) -> None:
    # Общий проверяющий модуль допускает два угла при float-равенстве sqrt(2).
    # Точная рациональная диагональ единичного квадрата чуть меньше этого float.
    assert not geometric_search._prepared_region_fits_one(
        box(minimum, minimum, 1, 1), sqrt(2)
    )


@pytest.mark.parametrize("origin", [(0, 0), (16_000, -5_000), (1e10, -1e10)])
def test_a_microgap_bound_has_a_large_margin_in_projected_coordinates(
    origin: tuple[float, float],
) -> None:
    x, y = origin
    assert geometric_search._prepared_region_fits_one(
        box(x, y, x + 0.21, y + 0.08), 3.5
    )


def test_one_admitted_position_stops_restarts_and_preserves_source_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Совместимый сохранённый источник проверяет политику общего замечания."""
    app, project = _application(box(0, 0, 3.4, 3.4))
    assert project.geometry is not None
    project.geometry.calculation_scope = "available_data"
    project.source_file = SourceFile(
        name="saved-project.dxf",
        content_sha256="a" * 64,
        size=1,
        imported_at="2026-10-01T00:00:00+00:00",
        dxf_version="AC1032",
        units="m",
        entity_count=0,
        accept_partial_geometry=True,
        warnings=["Внешняя ссылка не прочитана: site-base.dwg"],
    )
    app.repository.save(project)
    source_before = project.source_file.model_dump()
    calls: set[GeometryCandidateSearch] = set()

    class ObservedSearch(GeometryCandidateSearch):
        def next_batch(self, limit: int) -> list[PatternCandidate]:
            calls.add(self)
            return super().next_batch(limit)

    monkeypatch.setattr(geometric_search, "GeometryCandidateSearch", ObservedSearch)
    monkeypatch.setattr(
        app.geometry,
        "placement_advisory_detail",
        lambda *args: PositionAdvisory(
            "SOURCE_GEOMETRY_PARTIAL",
            "Исходник",
            "Полнота исходника не подтверждена",
            "Проверить исходные данные",
        ),
    )
    preview = app.preview_pattern(project.id, _request(40))
    assert preview.accepted_count == 1
    assert preview.search_stop_reason == "domain_exhausted"
    assert len(calls) == 1
    assert preview.change_set is not None and preview.change_set.can_apply
    assert preview.change_set.additions[0].status == "warning"
    assert preview.change_set.candidate_results[0].code == "SOURCE_GEOMETRY_PARTIAL"
    retained = app.get(project.id)
    assert retained.source_file is not None
    assert retained.source_file.model_dump() == source_before
    assert retained.plan == project.plan


def test_small_bound_without_an_admitted_position_does_not_skip_other_trials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, project = _application(box(0, 0, 3.4, 3.4))
    config = geometric_search.PLACEMENT_CONFIG
    limits = config.geometry_search.model_copy(
        update={"maximum_cells": 10_000, "maximum_trials": 2}
    )
    monkeypatch.setattr(
        geometric_search,
        "PLACEMENT_CONFIG",
        config.model_copy(update={"geometry_search": limits}),
    )
    calls: set[GeometryCandidateSearch] = set()

    class ObservedSearch(GeometryCandidateSearch):
        def next_batch(self, limit: int) -> list[PatternCandidate]:
            calls.add(self)
            return super().next_batch(limit)

    monkeypatch.setattr(geometric_search, "GeometryCandidateSearch", ObservedSearch)
    monkeypatch.setattr(
        app.geometry,
        "placement_advisory_detail",
        lambda *args: PositionAdvisory(
            "NATIVE_LOCAL_UNKNOWN",
            "Проверка",
            "Позиция не подтверждена",
            "Уточнить геометрию позиции",
        ),
    )
    preview = app.preview_pattern(project.id, _request(40))
    assert preview.accepted_count == 0
    assert preview.change_set is None
    assert preview.search_stop_reason == "domain_exhausted"
    assert len(calls) == 2
    assert app.get(project.id).plan == project.plan


def test_pending_domain_is_not_replaced_by_a_small_region_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, project = _application(box(0, 0, 3.4, 3.4))
    zone = project.planting_zones[0].model_copy(deep=True)
    issue = {"code": "XREF_MISSING", "source_path": "site-base.dwg"}
    zone.geometry["ga_search_domain"] = {
        "revision": "pending-test",
        "geometry": mapping(box(1.6, 1.6, 1.8, 1.8)),
        "unresolved_geometry": {"type": "GeometryCollection", "geometries": []},
        "pending_geometry": mapping(box(1.8, 1.6, 1.9, 1.8)),
        "available_area_m2": 0.04,
        "excluded_area_m2": 0,
        "unresolved_area_m2": 0,
        "pending_area_m2": 0.02,
        "measured_cells": 1,
        "elapsed_s": 0,
        "stop_reason": "probe_limit",
        "source_issues": [issue],
        "final_check": "prepared_geometry",
    }
    monkeypatch.setattr(
        app.evaluation, "automatic_generation_zones", lambda *args, **kwargs: [zone]
    )

    def premature(*args: object, **kwargs: object) -> None:
        raise AssertionError("Кандидаты не ищутся до завершения подготовки области")

    monkeypatch.setattr(
        "app.planning.patterns.application.search_geometric_placement", premature
    )
    preview = app.preview_pattern(project.id, _request(40))
    assert preview.accepted_count == 0
    assert preview.change_set is None
    assert preview.search_stop_reason is None
    assert preview.search_domains[0].pending_area_m2 == 0.02
    assert preview.search_domains[0].source_issues == [issue]


def test_cancellation_after_a_found_position_does_not_publish_that_position(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, project = _application(box(0, 0, 3.4, 3.4))
    original = app.evaluation.preview_addition
    found = False

    def addition(
        snapshot: Project,
        plan: Plan,
        payload: PlanObjectCreate,
        spacing_index: PlantSpacingIndex | None = None,
        compiled_zones: list[geometric_search.CompiledZone] | None = None,
    ) -> tuple[PlanObject, CandidateIssue | None]:
        nonlocal found
        result = original(snapshot, plan, payload, spacing_index, compiled_zones)
        found = True
        return result

    def cancel_after_found() -> None:
        if found:
            raise OperationCancelled()

    monkeypatch.setattr(app.evaluation, "preview_addition", addition)
    with operation_cancellation(cancel_after_found), pytest.raises(OperationCancelled):
        app.preview_pattern(project.id, _request(40))
    assert found
    assert not app.changes._previews
    assert app.get(project.id).plan == project.plan
