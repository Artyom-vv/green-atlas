from __future__ import annotations

from collections import Counter
from typing import Final

from app.contracts import DataPassport, DataPassportEntry, LayerKind, Project
from app.geometry.coverage_contracts import LayerGeometryCoverage

DATA_CLASSES: Final[tuple[tuple[str, LayerKind, str], ...]] = (
    ("site_border", LayerKind.SITE_BORDER, "Граница участка"),
    ("building", LayerKind.BUILDING, "Здания и сооружения"),
    ("road", LayerKind.ROAD, "Дороги и проезды"),
    ("utility", LayerKind.UTILITY, "Инженерные сети"),
    ("existing_green", LayerKind.EXISTING_GREEN, "Существующее озеленение"),
    ("lawn", LayerKind.LAWN, "Газоны"),
    ("water", LayerKind.WATER, "Водные объекты"),
    ("restricted", LayerKind.RESTRICTED, "Технические зоны"),
)

# The classes below are the minimum evidence needed before a bulk placement
# can be presented as verified.  Water is shown in the passport, but its
# absence alone does not make every terrestrial placement unsafe.
CRITICAL_CLASSES: Final[frozenset[str]] = frozenset({
    "site_border",
    "building",
    "road",
    "utility",
    "existing_green",
    "restricted",
})


def _source_features(project: Project) -> list[dict]:
    snapshot = project.geometry or project.source_geometry
    if snapshot is None:
        return []
    return [
        feature
        for feature in snapshot.feature_collection.get("features", [])
        if isinstance(feature, dict)
    ]


def _display_source_layers(project: Project) -> Counter[str]:
    """Count projected features, not distinct CAD objects or native queries.

    One CAD object can produce many features. Context anchors and derived
    allowed/forbidden areas are excluded. This cannot prove native coverage.
    """

    counts: Counter[str] = Counter()
    for feature in _source_features(project):
        properties = feature.get("properties", {})
        source_layer = str(properties.get("source_layer", "")).strip()
        kind = str(properties.get("kind", ""))
        if source_layer and kind in {item[0] for item in DATA_CLASSES} and not properties.get("source_context_only"):
            counts[source_layer] += 1
    return counts


def build_data_passport(project: Project, *, geometry=None) -> DataPassport:
    """Build a compact, deterministic evidence report from project state.

    Calculation coverage comes from the same prepared representations used by
    search. Legacy projects without that capability retain the import report.
    Display counts never certify a calculation. Explicit exclusions remain
    visible decisions, not automatically inferred missing geometry.
    """

    source_layers = list(project.layers)
    provider = getattr(geometry, 'source_coverage', None)
    coverage = provider(project) if callable(provider) and project.source_file else None
    if coverage is not None and coverage.source_sha256 != project.source_file.content_sha256:
        raise ValueError('Отчёт геометрии относится к другому захвату')
    records = coverage.layers if coverage is not None else {}
    def explicitly_excluded(layer):
        return layer.mapped_kind == LayerKind.IGNORE and layer.mapping_confirmed is True

    def layer_complete(layer):
        record = records.get(layer.source_name)
        return not record.unresolved if record is not None else layer.geometry_complete
    display_counts = _display_source_layers(project)
    has_snapshot = project.geometry is not None or project.source_geometry is not None
    unclassified = [
        layer.source_name
        for layer in source_layers
        if layer.suggested_kind == LayerKind.IGNORE
        and layer.mapped_kind in {None, LayerKind.IGNORE}
        and layer.mapping_confirmed is not True
    ]
    incomplete = [layer.source_name for layer in source_layers
                  if not explicitly_excluded(layer) and not layer_complete(layer)]
    excluded = [
        layer.source_name
        for layer in source_layers
        if layer.suggested_kind != LayerKind.IGNORE and layer.mapped_kind in {None, LayerKind.IGNORE}
    ]

    entries: list[DataPassportEntry] = []
    source_provenance = {
        "source_file_name": project.source_file.name if project.source_file else None,
        "source_imported_at": project.source_file.imported_at if project.source_file else None,
        "source_owner": project.source_file.owner if project.source_file else None,
    }
    missing_classes: list[str] = []
    critical_gaps: list[str] = []
    used_layers: list[str] = []

    for key, kind, label in DATA_CLASSES:
        # An explicit reassignment replaces the suggestion, not a second class.
        # Ignored/unassigned suggestions remain visible as excluded evidence.
        matching = [layer for layer in source_layers if (
            layer.mapped_kind if layer.mapped_kind not in {None, LayerKind.IGNORE}
            else layer.suggested_kind
        ) == kind]
        included = [layer for layer in matching if layer.mapped_kind == kind]
        excluded_matching = [layer for layer in matching if layer.mapped_kind in {None, LayerKind.IGNORE}]
        unresolved_exclusions = [layer for layer in excluded_matching if not explicitly_excluded(layer)]
        object_count = sum(max(0, layer.object_count) for layer in matching)
        display_feature_count = sum(display_counts.get(layer.source_name, 0) for layer in matching)
        snapshot_complete = has_snapshot and all(
            layer.object_count == 0 or layer.source_name in records or layer.source_name in display_counts
            for layer in included
        )
        complete = bool(included) and all(layer_complete(layer) for layer in included) and snapshot_complete
        class_coverage = None
        if coverage is not None:
            values = [records[layer.source_name] for layer in included if layer.source_name in records]
            class_coverage = LayerGeometryCoverage(
                **{field: sum(getattr(value, field) for value in values)
                   for field in ('area_count', 'linear_count', 'point_count', 'context_count')},
                unresolved=[issue for value in values for issue in value.unresolved],
            )
        partial_calculation = bool(project.geometry and project.geometry.calculation_scope == "available_data")
        used = bool(project.map_ready and project.source_review is None and included and (complete or partial_calculation))
        if used:
            used = class_coverage.represented if class_coverage is not None else any(layer.source_name in display_counts for layer in included)
        for layer in included:
            represented = records[layer.source_name].represented if layer.source_name in records else layer.source_name in display_counts
            if project.map_ready and project.source_review is None and (layer_complete(layer) or partial_calculation) and represented:
                used_layers.append(layer.source_name)

        if not matching:
            status = "missing"
            missing_classes.append(label)
            if key in CRITICAL_CLASSES:
                critical_gaps.append(f"Не найден класс {label}")
            note = "Нет слоя, сопоставленного с этим классом. Это не доказывает отсутствие таких объектов на территории."
        elif excluded_matching and not included:
            status = "excluded"
            if key in CRITICAL_CLASSES and unresolved_exclusions:
                critical_gaps.append(f"Класс {label} исключён из расчёта")
            note = "Исключение подтверждено пользователем" if not unresolved_exclusions else "Слой найден, но не участвует в расчёте"
        elif not complete or unresolved_exclusions:
            status = "partial"
            if key in CRITICAL_CLASSES:
                critical_gaps.append(f"Класс {label} покрыт не полностью")
            note = "Часть слоя неполна, исключена или не представлена в подготовленной карте"
        else:
            status = "verified"
            note = "Слой сопоставлен. Наличие на карте не подтверждает проверку каждого исходного объекта."
        if class_coverage is not None and included:
            note = (f"Площади: {class_coverage.area_count}; линии: {class_coverage.linear_count}; "
                    f"точечные объекты: {class_coverage.point_count}; "
                    f"пропуски подготовки: {len(class_coverage.unresolved)}. "
                    "Линии учитываются без приписывания им площади. Не является подтверждением нормативной полноты.")
        if status == "verified":
            semantic_confidence = "high"
            decision_level = "advisory"
        elif key == "site_border" and status in {"missing", "excluded"}:
            semantic_confidence = "low"
            decision_level = "stop"
        elif key in CRITICAL_CLASSES:
            semantic_confidence = "medium" if status == "partial" else "low"
            decision_level = "warning"
        else:
            semantic_confidence = "medium" if status == "partial" else "low"
            decision_level = "advisory"
        entries.append(DataPassportEntry(
            kind=key,
            label=label,
            status=status,
            layer_names=[layer.source_name for layer in matching],
            object_count=object_count,
            used_object_count=None,
            display_feature_count=display_feature_count if has_snapshot else None,
            used_in_calculation=used,
            semantic_confidence=semantic_confidence,
            decision_level=decision_level,
            **source_provenance,
            note=note,
            geometry_coverage=class_coverage,
        ))

    if unclassified:
        entries.append(DataPassportEntry(
            kind="unclassified",
            label="Нераспознанные слои",
            status="partial",
            layer_names=unclassified,
            object_count=sum(layer.object_count for layer in source_layers if layer.source_name in unclassified),
            used_object_count=None,
            used_in_calculation=False,
            semantic_confidence="low",
            decision_level="warning",
            **source_provenance,
            note="Не участвуют в расчёте, пока им не назначен класс",
        ))

    if incomplete:
        critical_gaps.append(f"Неполные слои: {', '.join(incomplete)}")
    provenance = project.source_file.prepared_provenance if project.source_file else None
    if provenance:
        review = provenance.opening_review
        if review.skipped_references:
            critical_gaps.append("Проект открыт без части внешних ссылок")
        if review.skipped_drawings:
            critical_gaps.append("Проект открыт без части файлов комплекта")
    if project.geometry and project.geometry.calculation_scope == "available_data":
        critical_gaps.append("Расчёт выполнен по доступным данным")
    unconfirmed_excluded = [layer.source_name for layer in source_layers
                            if layer.source_name in excluded and not explicitly_excluded(layer)]
    if unconfirmed_excluded:
        critical_gaps.append(f"Исключённые физические слои: {', '.join(unconfirmed_excluded)}")
    if unclassified:
        critical_gaps.append(f"Нераспознанные слои: {', '.join(unclassified)}")

    calculation_ready = bool(project.map_ready and project.source_review is None and project.geometry is not None)
    if not calculation_ready:
        overall_status = "not_ready"
        calculation_status = "not_ready"
        mass_status = "blocked"
        summary = "Карта ограничений ещё не подготовлена"
        gaps = ["Подтвердите слои и подготовьте карту", *critical_gaps]
    elif critical_gaps:
        overall_status = "limited"
        calculation_status = "ready"
        mass_status = "limited"
        summary = "Расчёт доступен с ограничениями"
        gaps = list(dict.fromkeys(critical_gaps))
    else:
        overall_status = "verified"
        calculation_status = "ready"
        mass_status = "verified"
        summary = "Основные пространственные ограничения подтверждены"
        gaps = []

    # Geodetic provenance is independent of representation coverage and stays
    # visible even when every available CAD object has been prepared.
    if not project.coordinate_reference.crs_id:
        gaps.append("Система координат не подтверждена")
        if mass_status == "verified":
            overall_status = "limited"
            mass_status = "limited"
            summary = "Расчёт доступен с ограничениями"

    return DataPassport(
        overall_status=overall_status,
        calculation_status=calculation_status,
        mass_placement_status=mass_status,
        summary=summary,
        source_file_name=project.source_file.name if project.source_file else None,
        source_imported_at=project.source_file.imported_at if project.source_file else None,
        source_owner=project.source_file.owner if project.source_file else None,
        coordinate_reference=project.coordinate_reference.model_copy(deep=True),
        entries=entries,
        unclassified_layers=unclassified,
        incomplete_layers=incomplete,
        excluded_layers=excluded,
        used_in_calculation=list(dict.fromkeys(used_layers)),
        missing_classes=missing_classes,
        gaps=list(dict.fromkeys(gaps)),
    )
