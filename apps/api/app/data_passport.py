from __future__ import annotations

from collections import Counter
from typing import Final

from app.contracts import DataPassport, DataPassportEntry, LayerKind, Project


DATA_CLASSES: Final[tuple[tuple[str, LayerKind, str], ...]] = (
    ("site_border", LayerKind.SITE_BORDER, "Граница участка"),
    ("building", LayerKind.BUILDING, "Здания и сооружения"),
    ("road", LayerKind.ROAD, "Дороги и проезды"),
    ("utility", LayerKind.UTILITY, "Инженерные сети"),
    ("existing_green", LayerKind.EXISTING_GREEN, "Существующее озеленение"),
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


def _used_source_layers(project: Project) -> Counter[str]:
    """Count source features that survived into the calculated map.

    ``geometry`` contains the original normalized features alongside derived
    ``allowed`` and ``forbidden`` features.  Counting only source layers and
    excluding context-only anchors gives the operator an honest answer to
    "what did the calculation actually use".  A prepared project with a
    projection that has no feature payload falls back to complete mapped
    layers, preserving compatibility with lightweight test repositories.
    """

    counts: Counter[str] = Counter()
    for feature in _source_features(project):
        properties = feature.get("properties", {})
        source_layer = str(properties.get("source_layer", "")).strip()
        kind = str(properties.get("kind", ""))
        if source_layer and kind in {item[0] for item in DATA_CLASSES} and not properties.get("source_context_only"):
            counts[source_layer] += 1
    return counts


def build_data_passport(project: Project) -> DataPassport:
    """Build a compact, deterministic evidence report from project state.

    This function intentionally uses only normalized layer metadata and the
    calculated/source feature snapshot. It does not infer a missing utility,
    road or existing planting layer from visual proximity, and it never turns
    an excluded or incomplete layer into a verified constraint.
    """

    source_layers = list(project.layers)
    used_counts = _used_source_layers(project)
    has_feature_payload = bool(_source_features(project))
    unclassified = [
        layer.source_name
        for layer in source_layers
        if layer.suggested_kind == LayerKind.IGNORE and (layer.mapped_kind in {None, LayerKind.IGNORE})
    ]
    incomplete = [layer.source_name for layer in source_layers if not layer.geometry_complete]
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
        matching = [layer for layer in source_layers if layer.suggested_kind == kind or layer.mapped_kind == kind]
        included = [layer for layer in matching if layer.mapped_kind == kind]
        excluded_matching = [layer for layer in matching if layer.mapped_kind in {None, LayerKind.IGNORE}]
        object_count = sum(max(0, layer.object_count) for layer in matching)
        used_object_count = sum(used_counts.get(layer.source_name, 0) for layer in included)
        snapshot_complete = not has_feature_payload or all(
            layer.object_count == 0 or layer.source_name in used_counts
            for layer in included
        )
        complete = bool(included) and all(layer.geometry_complete for layer in included) and snapshot_complete
        used = bool(project.map_ready and project.source_review is None and included and complete)
        if has_feature_payload and used:
            # If the calculated snapshot has source features, only mark a
            # layer as used when at least one real feature survived into it.
            used = any(layer.source_name in used_counts for layer in included)
        feature_missing = bool(project.map_ready and project.source_review is None and has_feature_payload and included and complete and not used)
        for layer in included:
            if project.map_ready and project.source_review is None and layer.geometry_complete and (not has_feature_payload or layer.source_name in used_counts):
                used_layers.append(layer.source_name)

        if not matching:
            status = "missing"
            missing_classes.append(label)
            if key in CRITICAL_CLASSES:
                critical_gaps.append(f"Не найден класс {label}")
            note = "В DXF нет слоя этого класса"
        elif excluded_matching and not included:
            status = "excluded"
            if key in CRITICAL_CLASSES:
                critical_gaps.append(f"Класс {label} исключён из расчёта")
            note = "Слой найден, но не участвует в расчёте"
        elif not complete or excluded_matching:
            status = "partial"
            if key in CRITICAL_CLASSES:
                critical_gaps.append(f"Класс {label} покрыт не полностью")
            note = "Часть слоя неполна или исключена из расчёта"
        elif feature_missing:
            status = "partial"
            if key in CRITICAL_CLASSES:
                critical_gaps.append(f"Геометрия класса {label} не попала в расчёт")
            note = "Слой найден, но геометрия не попала в рассчитанную карту"
        else:
            status = "verified"
            note = "Слой распознан и готов к проверке" if not used else "Слой участвовал в расчёте"
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
            used_object_count=used_object_count if used else 0,
            used_in_calculation=used,
            semantic_confidence=semantic_confidence,
            decision_level=decision_level,
            **source_provenance,
            note=note,
        ))

    if unclassified:
        entries.append(DataPassportEntry(
            kind="unclassified",
            label="Нераспознанные слои",
            status="partial",
            layer_names=unclassified,
            object_count=sum(layer.object_count for layer in source_layers if layer.source_name in unclassified),
            used_object_count=0,
            used_in_calculation=False,
            semantic_confidence="low",
            decision_level="warning",
            **source_provenance,
            note="Не участвуют в расчёте, пока им не назначен класс",
        ))

    if incomplete:
        critical_gaps.append(f"Неполные слои: {', '.join(incomplete)}")
    if excluded:
        critical_gaps.append(f"Исключённые физические слои: {', '.join(excluded)}")
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

    # A source layer with an unsupported or bounded representation is already
    # reflected by ``geometry_complete``. Keep this extra explanation stable
    # for consumers that want to explain why the mass action is limited.
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
