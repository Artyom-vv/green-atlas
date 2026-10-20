"""Паспорт данных объясняет, на каких сведениях основан расчёт посадок.

Карта показывает объекты, но не доказывает полноту исходника. Паспорт сопоставляет
решения по слоям с текущим расчётным покрытием, сохраняет адресные пропуски и
оценивает готовность массовой посадки. Он читает состояние проекта и готовые
сведения проверяющего модуля; не восстанавливает CAD и не подтверждает нормы.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TypedDict

from app.dxf_import.evidence_contracts import (
    DataPassport,
    DataPassportDecision,
    DataPassportEntry,
    DataPassportEntryStatus,
    DataPassportKind,
)
from app.dxf_import.layers.contracts import Layer, LayerKind
from app.dxf_import.review.passport_policy import (
    CRITICAL_CLASSES,
    DATA_CLASSES,
    ENTRY_CONFIDENCE,
)
from app.geometry.coverage_contracts import GeometryCoverage, LayerGeometryCoverage
from app.geometry.ports import GeometryCoveragePort, GeometryRuleTracePort
from app.native_query.preparation.prepared_snapshot import PreparedGeometryUnavailable
from app.planning.config import DEFAULT_PLANNING_HORIZON_YEAR
from app.projects.contracts import Project
from app.shared.messages import message


def build_data_passport(
    project: Project,
    *,
    geometry: GeometryCoveragePort | GeometryRuleTracePort | None = None,
    metadata_only: bool = False,
    display_counts: Mapping[str, int] | None = None,
) -> DataPassport:
    """Собираем паспорт одного состояния проекта без изменения его данных.

    Актуальное покрытие имеет приоритет над отчётом импорта. У прежнего движка
    эта возможность может отсутствовать: тогда сохраняем прежнюю оценку слоя.
    Число частей отображения не становится числом проверенных объектов CAD.
    """
    evidence = _read_layer_evidence(
        project, geometry, metadata_only=metadata_only, display_counts=display_counts
    )
    source = project.source_file
    provenance: _SourceProvenance = {
        "source_file_name": source.name if source else None,
        "source_imported_at": source.imported_at if source else None,
        "source_owner": source.owner if source else None,
    }
    calculation_enabled = bool(
        project.map_ready
        and project.source_review is None
        and evidence.preparation_gap is None
    )
    partial = bool(
        project.geometry and project.geometry.calculation_scope == "available_data"
    )
    entries: list[DataPassportEntry] = []
    used_layers: list[str] = []
    critical_gaps: list[str] = []
    for kind in DATA_CLASSES:
        entry, used, gap = _class_entry(kind, evidence, calculation_enabled, partial)
        entries.append(entry)
        used_layers.extend(used)
        if gap is not None:
            critical_gaps.append(gap)

    unclassified = [
        layer.source_name
        for layer in evidence.layers
        if layer.suggested_kind == LayerKind.IGNORE
        and layer.mapped_kind in {None, LayerKind.IGNORE}
        and layer.mapping_confirmed is not True
    ]
    incomplete = [
        layer.source_name
        for layer in evidence.layers
        if not _explicitly_excluded(layer) and not evidence.complete(layer)
    ]
    excluded = [
        layer.source_name
        for layer in evidence.layers
        if layer.suggested_kind != LayerKind.IGNORE
        and layer.mapped_kind in {None, LayerKind.IGNORE}
    ]
    if unclassified:
        entries.append(_unclassified_entry(evidence.layers, unclassified))
    for entry in entries:
        entry.source_file_name = provenance["source_file_name"]
        entry.source_imported_at = provenance["source_imported_at"]
        entry.source_owner = provenance["source_owner"]
    critical_gaps.extend(
        _project_gaps(project, evidence.layers, incomplete, excluded, unclassified)
    )
    if evidence.preparation_gap is not None:
        critical_gaps.append(evidence.preparation_gap)
    passport = DataPassport(
        planning_horizon_year=DEFAULT_PLANNING_HORIZON_YEAR if project.plan else None,
        overall_status="not_ready",
        calculation_status="not_ready",
        mass_placement_status="blocked",
        summary=message("passport.summary.not_ready"),
        **provenance,
        coordinate_reference=project.coordinate_reference.model_copy(deep=True),
        entries=entries,
        unclassified_layers=unclassified,
        incomplete_layers=incomplete,
        excluded_layers=excluded,
        used_in_calculation=list(dict.fromkeys(used_layers)),
        missing_classes=[entry.label for entry in entries if entry.status == "missing"],
        gaps=critical_gaps,
    )
    _assess_readiness(
        passport,
        calculation_ready=calculation_enabled and project.geometry is not None,
        has_crs=bool(project.coordinate_reference.crs_id),
    )
    return passport


class _SourceProvenance(TypedDict):
    source_file_name: str | None
    source_imported_at: str | None
    source_owner: str | None


@dataclass(frozen=True)
class _LayerEvidence:
    """Сведения только о слоях: импорт, отображение и текущее покрытие."""

    layers: list[Layer]
    classes: dict[LayerKind, list[Layer]]
    display_counts: Counter[str]
    display_counts_loaded: bool
    records: dict[str, LayerGeometryCoverage] | None
    has_snapshot: bool
    preparation_gap: str | None

    def complete(self, layer: Layer) -> bool:
        if self.preparation_gap is not None:
            return False
        record = (self.records or {}).get(layer.source_name)
        return (
            not record.unresolved
            if record is not None
            else self.display_counts_loaded and layer.geometry_complete
        )

    def represented(self, layer: Layer) -> bool:
        record = (self.records or {}).get(layer.source_name)
        return (
            bool(record.represented)
            if record is not None
            else layer.source_name in self.display_counts
            if self.display_counts_loaded
            else any(layer.projected_geometry_types.values())
        )

    def class_coverage(self, included: list[Layer]) -> LayerGeometryCoverage | None:
        if self.records is None:
            return None
        values = [
            self.records[layer.source_name]
            for layer in included
            if layer.source_name in self.records
        ]
        return LayerGeometryCoverage(
            area_count=sum(value.area_count for value in values),
            linear_count=sum(value.linear_count for value in values),
            point_count=sum(value.point_count for value in values),
            context_count=sum(value.context_count for value in values),
            unresolved=[issue for value in values for issue in value.unresolved],
        )


def _read_layer_evidence(
    project: Project,
    geometry: GeometryCoveragePort | GeometryRuleTracePort | None,
    *,
    metadata_only: bool = False,
    display_counts: Mapping[str, int] | None = None,
) -> _LayerEvidence:
    """Получаем покрытие один раз; группировка не дублирует назначенные слои."""
    layers = list(project.layers)
    provider: Callable[[Project], GeometryCoverage | None] | None = getattr(
        geometry,
        "metadata_source_coverage" if metadata_only else "source_coverage",
        None,
    )
    coverage = None
    preparation_gap = None
    if callable(provider) and project.source_file:
        try:
            coverage = provider(project)
        except PreparedGeometryUnavailable as error:
            # Паспорт предшествует явной подготовке CAD. Отсутствующий расчёт
            # означает неготовность, а повреждённый или устаревший — отказ чтения.
            if error.state != "missing" or project.source_file.native_session is None:
                raise
            preparation_gap = str(error)
        if (
            coverage is not None
            and coverage.source_sha256 != project.source_file.content_sha256
        ):
            raise ValueError(message("passport.coverage_source_mismatch"))
    snapshot = project.geometry or project.source_geometry
    # Потоковая сводка проходит те же проверки сохранённых байтов и ревизии.
    # Полные координаты не нужны паспорту; расчётное покрытие читается отдельно.
    counts: Counter[str] = Counter(display_counts or {})
    if snapshot is not None and not metadata_only and display_counts is None:
        for feature in snapshot.feature_collection.get("features", []):
            if not isinstance(feature, dict):
                continue
            properties = feature.get("properties", {})
            source_layer = str(properties.get("source_layer", "")).strip()
            kind = str(properties.get("kind", ""))
            if (
                source_layer
                and kind in DATA_CLASSES
                and not properties.get("source_context_only")
            ):
                counts[source_layer] += 1
    classes: dict[LayerKind, list[Layer]] = defaultdict(list)
    for layer in layers:
        # Подтверждённое переназначение заменяет предложение, а не создаёт второй класс.
        role = (
            layer.mapped_kind
            if layer.mapped_kind is not None and layer.mapped_kind != LayerKind.IGNORE
            else layer.suggested_kind
        )
        classes[role].append(layer)
    return _LayerEvidence(
        layers,
        classes,
        counts,
        not metadata_only,
        coverage.layers if coverage is not None else None,
        snapshot is not None,
        preparation_gap,
    )


def _explicitly_excluded(layer: Layer) -> bool:
    return layer.mapped_kind == LayerKind.IGNORE and layer.mapping_confirmed is True


def _class_entry(
    kind: DataPassportKind,
    evidence: _LayerEvidence,
    calculation_enabled: bool,
    partial: bool,
) -> tuple[DataPassportEntry, list[str], str | None]:
    """Оцениваем один класс, сохраняя исключения и разницу двух видов участия."""
    matching = evidence.classes.get(LayerKind(kind), [])
    included = [layer for layer in matching if layer.mapped_kind == kind]
    excluded = [
        layer for layer in matching if layer.mapped_kind in {None, LayerKind.IGNORE}
    ]
    unresolved_exclusions = any(not _explicitly_excluded(layer) for layer in excluded)
    records = evidence.records or {}
    snapshot_complete = evidence.has_snapshot and all(
        layer.object_count == 0
        or layer.source_name in records
        or (
            layer.source_name in evidence.display_counts
            if evidence.display_counts_loaded
            else evidence.represented(layer)
        )
        for layer in included
    )
    complete = (
        bool(included)
        and all(evidence.complete(layer) for layer in included)
        and snapshot_complete
    )
    coverage = evidence.class_coverage(included)
    used = bool(calculation_enabled and included and (complete or partial))
    if used:
        used = (
            bool(coverage.represented)
            if coverage is not None
            else any(evidence.represented(layer) for layer in included)
        )
    # При частичном отчёте отдельный слой сохраняет fallback отображения; класс
    # при наличии отчёта использует только объединённое расчётное покрытие.
    used_layers = [
        layer.source_name
        for layer in included
        if calculation_enabled
        and (evidence.complete(layer) or partial)
        and evidence.represented(layer)
    ]
    status: DataPassportEntryStatus
    if not matching:
        status = "missing"
    elif excluded and not included:
        status = "excluded"
    elif not complete or unresolved_exclusions:
        status = "partial"
    else:
        status = "verified"
    label = message(f"passport.label.{kind}")
    gap = None
    if (
        kind in CRITICAL_CLASSES
        and status != "verified"
        and (status != "excluded" or unresolved_exclusions)
    ):
        gap = message(f"passport.gap.class_{status}", {"label": label})
    note_key = (
        ("excluded_unconfirmed" if unresolved_exclusions else "excluded_confirmed")
        if status == "excluded"
        else status
    )
    note = message(f"passport.note.{note_key}")
    if coverage is not None and included:
        note = message(
            "passport.note.coverage",
            {
                "area_count": coverage.area_count,
                "linear_count": coverage.linear_count,
                "point_count": coverage.point_count,
                "unresolved_count": len(coverage.unresolved),
            },
        )
    decision: DataPassportDecision = "advisory"
    if kind == "site_border" and status in {"missing", "excluded"}:
        decision = "stop"
    elif kind in CRITICAL_CLASSES and status != "verified":
        decision = "warning"
    return (
        DataPassportEntry(
            kind=kind,
            label=label,
            status=status,
            layer_names=[layer.source_name for layer in matching],
            object_count=sum(max(0, layer.object_count) for layer in matching),
            display_feature_count=sum(
                evidence.display_counts.get(layer.source_name, 0) for layer in matching
            )
            if evidence.has_snapshot and evidence.display_counts_loaded
            else None,
            used_in_calculation=used,
            semantic_confidence=ENTRY_CONFIDENCE[status],
            decision_level=decision,
            note=note,
            geometry_coverage=coverage,
        ),
        used_layers,
        gap,
    )


def _project_gaps(
    project: Project,
    layers: list[Layer],
    incomplete: list[str],
    excluded: list[str],
    unclassified: list[str],
) -> list[str]:
    """Сохраняем замечания захвата отдельно от полноты каждого класса."""
    gaps = []
    if incomplete:
        gaps.append(
            message("passport.gap.incomplete_layers", {"layer_names": incomplete})
        )
    provenance = (
        project.source_file.prepared_provenance if project.source_file else None
    )
    if provenance:
        review = provenance.opening_review
        if review.skipped_references:
            gaps.append(message("passport.gap.skipped_references"))
        if review.skipped_drawings:
            gaps.append(message("passport.gap.skipped_drawings"))
    if project.geometry and project.geometry.calculation_scope == "available_data":
        gaps.append(message("passport.gap.available_data"))
    unconfirmed_excluded = [
        layer.source_name
        for layer in layers
        if layer.source_name in excluded and not _explicitly_excluded(layer)
    ]
    if unconfirmed_excluded:
        gaps.append(
            message(
                "passport.gap.excluded_layers", {"layer_names": unconfirmed_excluded}
            )
        )
    if unclassified:
        gaps.append(
            message("passport.gap.unclassified_layers", {"layer_names": unclassified})
        )
    return gaps


def _unclassified_entry(layers: list[Layer], names: list[str]) -> DataPassportEntry:
    """Неизвестное назначение остаётся отдельным решением, а не пропуском класса."""
    return DataPassportEntry(
        kind="unclassified",
        label=message("passport.label.unclassified"),
        status="partial",
        layer_names=names,
        object_count=sum(
            layer.object_count for layer in layers if layer.source_name in names
        ),
        semantic_confidence="low",
        decision_level="warning",
        note=message("passport.note.unclassified"),
    )


def _assess_readiness(
    passport: DataPassport, *, calculation_ready: bool, has_crs: bool
) -> None:
    """Оцениваем готовность нового DTO; полнота представлений не подтверждает CRS."""
    if not calculation_ready:
        passport.gaps.insert(0, message("passport.gap.prepare_map"))
    else:
        passport.calculation_status = "ready"
        limited = bool(passport.gaps)
        passport.overall_status = "limited" if limited else "verified"
        passport.mass_placement_status = "limited" if limited else "verified"
        passport.summary = message(
            "passport.summary.limited" if limited else "passport.summary.verified"
        )
    if not has_crs:
        passport.gaps.append(message("passport.gap.coordinate_reference"))
        if passport.mass_placement_status == "verified":
            passport.overall_status = "limited"
            passport.mass_placement_status = "limited"
            passport.summary = message("passport.summary.limited")
    passport.gaps = list(dict.fromkeys(passport.gaps))
