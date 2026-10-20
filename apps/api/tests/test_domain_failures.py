"""Known refusals carry facts without changing domain guard order or state."""

from pathlib import Path
from typing import Literal
from unittest.mock import patch

import pytest
from pydantic import ValidationError
from shapely.geometry import LineString, mapping
from test_placement_allocation import application

from app.composition import create_runtime
from app.dxf_import.contracts import ImportEditability, ImportStatus, SourceFile
from app.native_query.preparation.face_review import (
    NativeFaceDecision,
    NativeFaceReview,
    NativeFaceReviewItem,
    change_face_decision,
)
from app.planning.changes.evaluation import PlanEvaluation
from app.planning.contracts import Plan, PlanObject, PlanObjectCreate, PlanObjectUpdate
from app.planning.domain import PlanVersionConflict
from app.planning.patterns.contracts import FillPatternRequest, RowPatternRequest
from app.planning.patterns.generators import generate_fill
from app.planning.rules import effective_pattern_spacing
from app.projects.adapters import SqliteProjectRepository
from app.projects.concurrency import ProjectVersionConflict
from app.projects.contracts import Project
from app.shared.error_messages import public_failure_message
from app.shared.failures import (
    DomainFailure,
    FailureReason,
    ProjectNotFound,
    ResourceNotFound,
)
from app.species.catalog import get_species

SHRUB_REVISION = "spiraea-japonica@2026-08-28.1"


def test_unexpected_provider_configuration_keeps_private_detail_local(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    private_detail = "synthetic credential /private/provider-settings.json"

    def fail_provider() -> None:
        raise ValueError(private_detail)

    monkeypatch.setattr("app.composition.configured_provider", fail_provider)
    runtime = create_runtime(tmp_path / "configuration.sqlite3")
    try:
        recognition = runtime.layer_recognition
        assert recognition.provider is None
        assert recognition.configuration_error
        assert private_detail not in recognition.configuration_error
        assert private_detail in caplog.text
    finally:
        runtime.close()


def test_known_failure_contains_only_typed_cause_and_parameters() -> None:
    failure = DomainFailure(FailureReason.PLAN_MISSING)
    assert isinstance(failure, ValueError)
    assert failure.reason is FailureReason.PLAN_MISSING
    assert failure.params == {}
    assert str(failure) == ""


def test_native_import_memory_refusal_uses_public_catalogue() -> None:
    failure = DomainFailure(FailureReason.NATIVE_IMPORT_MEMORY_EXCEEDED)
    failure.args = ("лимит 500 байт, измеренный пик 1000 байт",)
    assert failure.params == {}
    assert public_failure_message(failure, "handoff_failed") == (
        "Открытие остановлено: превышен лимит памяти импорта"
    )


@pytest.mark.parametrize(
    "error,expected",
    [
        (
            ProjectVersionConflict("project", 1, 2),
            "Проект изменён в другой вкладке. Обновите данные перед повтором действия.",
        ),
        (
            PlanVersionConflict(1, 2),
            "План изменился после предпросмотра. Рассчитайте изменения ещё раз.",
        ),
    ],
)
def test_version_conflict_public_message_uses_catalogue(
    error: Exception, expected: str
) -> None:
    error.args = ("synthetic credential /private/source.dwg",)
    assert public_failure_message(error, "operation_failed") == expected


@pytest.mark.parametrize("storage", ["sqlite-memory", "sqlite-file"])
def test_missing_project_retains_keyerror_contract_with_typed_identity(
    tmp_path: Path, storage: Literal["sqlite-memory", "sqlite-file"]
) -> None:
    repository = SqliteProjectRepository(
        ":memory:"
        if storage == "sqlite-memory"
        else tmp_path / "missing-project.sqlite3"
    )
    try:
        with pytest.raises(ProjectNotFound) as failure:
            repository.get("missing-project")
        assert isinstance(failure.value, KeyError)
        assert failure.value.project_id == "missing-project"
        assert str(failure.value) == ""
        with pytest.raises(ProjectNotFound) as source_failure:
            repository.get_source("missing-project")
        assert source_failure.value.project_id == "missing-project"
    finally:
        repository.close()


def test_missing_zone_is_reported_before_candidate_generation() -> None:
    service, project = application()
    before = service.get(project.id).model_dump()
    with patch.object(service.patterns.candidate_generator, "generate") as generate:
        with pytest.raises(DomainFailure) as failure:
            service.preview_pattern(
                project.id,
                FillPatternRequest(
                    base_plan_version=1, zone_ids=["missing-z", "west", "missing-a"]
                ),
            )
    assert failure.value.reason is FailureReason.ZONE_MISSING
    assert failure.value.params == {"zone_ids": ["missing-a", "missing-z"]}
    generate.assert_not_called()
    assert service.get(project.id).model_dump() == before


def test_pattern_spacing_reports_species_kind_without_text() -> None:
    with pytest.raises(DomainFailure) as failure:
        effective_pattern_spacing(
            FillPatternRequest(
                base_plan_version=1,
                zone_ids=["west"],
                plant_kind="tree",
                species_revision_id=SHRUB_REVISION,
            )
        )
    assert failure.value.reason is FailureReason.SPECIES_KIND_MISMATCH
    assert failure.value.params == {
        "species_revision_id": SHRUB_REVISION,
        "expected_kind": "tree",
        "actual_kind": "shrub",
    }


def test_missing_plan_object_refusal_retains_keyerror_and_plan_state() -> None:
    service, project = application()
    assert project.plan is not None
    before = project.plan.model_dump()
    with pytest.raises(ResourceNotFound) as failure:
        service.evaluation.preview_update(
            project, project.plan, "missing-object", PlanObjectUpdate(x=20)
        )
    assert isinstance(failure.value, KeyError)
    assert failure.value.reason is FailureReason.PLAN_OBJECT_MISSING
    assert failure.value.params == {}
    assert project.plan.model_dump() == before


def test_missing_species_revision_reports_requested_identity() -> None:
    with pytest.raises(DomainFailure) as failure:
        get_species("missing-revision")
    assert failure.value.reason is FailureReason.SPECIES_REVISION_MISSING
    assert failure.value.params == {"species_revision_id": "missing-revision"}


def test_invalid_zone_geometry_retains_zone_label_as_data() -> None:
    _service, project = application()
    zone = project.planting_zones[0].model_copy(deep=True)
    zone.geometry = mapping(LineString([(0, 0), (20, 20)]))
    with pytest.raises(DomainFailure) as failure:
        generate_fill(
            FillPatternRequest(base_plan_version=1, zone_ids=[zone.id]), [zone]
        )
    assert failure.value.reason is FailureReason.ZONE_NOT_CLOSED
    assert failure.value.params == {"zone_label": "West"}


def test_row_validation_preserves_domain_cause_inside_pydantic_context() -> None:
    with pytest.raises(ValidationError) as failure:
        RowPatternRequest(
            base_plan_version=1,
            zone_ids=["west"],
            axis=mapping(LineString([(0, 0), (20, 20)])),
            side="left",
        )
    [issue] = failure.value.errors()
    cause = issue["ctx"]["error"]
    assert isinstance(cause, DomainFailure)
    assert cause.reason is FailureReason.ROW_LATERAL_OFFSET_REQUIRED
    assert cause.params == {}


@pytest.mark.parametrize("update", [False, True])
def test_species_kind_refusal_does_not_modify_the_plan(update: bool) -> None:
    service, project = application()
    plan = Plan(objects=[PlanObject(id="tree", kind="tree", x=20, y=20, radius=1.6)])
    before = plan.model_dump()
    evaluation: PlanEvaluation = service.evaluation
    with pytest.raises(DomainFailure) as failure:
        if update:
            evaluation.preview_update(
                project,
                plan,
                "tree",
                PlanObjectUpdate(species_revision_id=SHRUB_REVISION),
            )
        else:
            evaluation.preview_addition(
                project,
                plan,
                PlanObjectCreate(
                    kind="tree", x=50, y=50, species_revision_id=SHRUB_REVISION
                ),
            )
    assert failure.value.reason is FailureReason.SPECIES_KIND_MISMATCH
    assert failure.value.params == {
        "species_revision_id": SHRUB_REVISION,
        "expected_kind": "tree",
        "actual_kind": "shrub",
    }
    assert plan.model_dump() == before


@pytest.mark.parametrize(
    "fault,reason,explanation",
    [
        (
            "read_only",
            FailureReason.NATIVE_FACE_READ_ONLY,
            "Источник доступен только для просмотра",
        ),
        ("hash", FailureReason.GROUP_SOURCE_CHANGED, "Исходные данные изменились"),
        (
            "missing",
            FailureReason.NATIVE_FACE_UNAVAILABLE,
            "Область изменена другим решением или недоступна",
        ),
        (
            "overridden",
            FailureReason.NATIVE_FACE_UNAVAILABLE,
            "Область изменена другим решением или недоступна",
        ),
    ],
)
def test_native_face_refusal_preserves_source_and_first_guard(
    fault: str,
    reason: FailureReason,
    explanation: str,
) -> None:
    project = Project(
        name="Captured source",
        source_file=SourceFile(
            name="source.dwg",
            content_sha256="a" * 64,
            size=1,
            imported_at="2026-10-08",
            dxf_version="AutoCAD live snapshot",
            units="metres",
            entity_count=1,
        ),
        import_status=ImportStatus(
            editability=ImportEditability.READ_ONLY
            if fault == "read_only"
            else ImportEditability.EDITABLE
        ),
    )
    requested_hash = "b" * 64 if fault in {"read_only", "hash"} else "a" * 64
    request = NativeFaceDecision(
        source_sha256=requested_hash, key="c" * 64, rejected=True
    )
    review = NativeFaceReview(
        source_sha256=requested_hash,
        items=[
            NativeFaceReviewItem(
                key=request.key,
                anchor="AA",
                layer="Building",
                area_m2=1,
                member_count=1,
                repair_count=0,
                status="overridden",
                path=[],
                repairs=[],
            )
        ]
        if fault == "overridden"
        else [],
    )
    before = project.model_dump()
    with pytest.raises(DomainFailure) as failure:
        change_face_decision(project, request, review)
    assert failure.value.reason is reason
    assert failure.value.params == {}
    assert public_failure_message(failure.value) == explanation
    assert project.model_dump() == before
