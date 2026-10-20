"""Объяснение локальных препятствий по тем же свидетельствам, которые использует выбор
позиций.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from app.dxf_import.layers.contracts import Layer
from app.geometry.evidence_contracts import GeometryEvidence, GeometryEvidenceItem
from app.native_query.policy.live_rules import MeasuredObstacle, required_distance
from app.native_query.policy.query_policy import query_reach_m
from app.native_query.policy.utility_requirements import utility_requirement
from app.native_query.search.domain_cells import Cell, Verdict, certify_cell
from app.projects.contracts import Project

if TYPE_CHECKING:
    from app.native_query.policy.placement_policy import PlacementPolicy


def interior_message(row: MeasuredObstacle) -> str:
    error = (
        row.measurement.preparation_error if row.measurement else ""
    ) or row.item.error
    if error == "open native endpoint chain":
        return "Линии контура не образуют замкнутую область"
    if error == "ambiguous native endpoint junction":
        return "В соединениях линий есть разветвления"
    if "no supported native area query" in error:
        return "Объект измерен как линия, площадь не получена"
    if "createFromCurves" in error:
        return "AutoCAD не создал область из кривых"
    return "Внутренняя область объекта не подтверждена"


def failure_reason(
    row: MeasuredObstacle, layer: Layer | None
) -> tuple[str, Literal["mapping", "inventory", "query", "area"], str, str]:
    if layer is None:
        return (
            "layer_missing",
            "mapping",
            "Слой отсутствует в сопоставлении",
            "Назначить роль слоя",
        )
    if not layer.mapping_confirmed:
        return (
            "layer_unconfirmed",
            "mapping",
            "Назначение слоя не подтверждено",
            "Проверить назначение слоя",
        )
    if row.item.error and row.measurement is None:
        return (
            "inventory_object_error",
            "inventory",
            "Объект не подготовлен к запросу",
            "Проверить состав и контур объекта",
        )
    if row.measurement is None and row.item.target(area=True) is None:
        return (
            "query_group_limit",
            "inventory",
            "Группа превышает лимит нашего обработчика",
            "Разобрать состав группы",
        )
    if (
        row.measurement
        and row.measurement.capability == "unavailable"
        and row.measurement.preparation_error
    ):
        return (
            "native_area_preparation_failed",
            "area",
            "Не получена внутренняя область объекта",
            "Проверить создание области в AutoCAD",
        )
    if row.answer is None:
        return (
            "measurement_missing",
            "query",
            "Измерение объекта отсутствует",
            "Проверить передачу запроса",
        )
    if row.answer.status or row.answer.error:
        return (
            "native_measurement_failed",
            "query",
            "AutoCAD вернул ошибку измерения",
            "Проверить ошибку объекта в AutoCAD",
        )
    return (
        "distance_missing",
        "query",
        "AutoCAD не вернул расстояние",
        "Проверить поддержку геометрии объекта",
    )


def explain_position(
    engine: PlacementPolicy,
    project: Project,
    x: float,
    y: float,
    radius: float,
    kind: str,
    canopy: float = 0,
    roots: float = 0,
) -> GeometryEvidence:
    with engine._lock:
        reach = query_reach_m(radius, canopy, roots)
        rows = engine._point_measurements(project, [(x, y)], reach_m=reach)[(x, y)][1]
        layers = engine._layers
        sites = tuple(
            r
            for r in rows
            if (layer := layers.get(r.item.layer))
            and layer.mapping_confirmed
            and layer.mapped_kind == "site_border"
        )
        cell = Cell(x, y, 0)

        def check(objects: tuple[MeasuredObstacle, ...]) -> Verdict:
            return certify_cell(
                cell,
                objects,
                layers,
                engine.linear_layers,
                engine.factor,
                kind,
                radius,
                canopy,
                roots,
                reach,
            )

        verdict = check(rows)
        causes = []
        site_verdict = check(sites)
        if site_verdict.state != "available":
            causes.append(
                GeometryEvidenceItem(
                    code=site_verdict.reason,
                    stage="site",
                    outcome=site_verdict.state,
                    message="Точка вне территории"
                    if site_verdict.state == "excluded"
                    else "Принадлежность территории не подтверждена",
                    action="Проверить границу территории",
                    source_feature_ids=[
                        route for r in sites for route in r.item.routes
                    ],
                    query_sent=getattr(engine, "sends_cad_queries", True)
                    and any(r.measurement is not None for r in sites),
                )
            )
        for row in rows:
            if row in sites or row.item.bounds is None:
                continue
            detail = check((row, *sites))
            if detail.state == "available" or detail.reason in {
                "site_membership",
                "outside_site",
            }:
                continue
            layer = layers.get(row.item.layer)
            required = required_distance(layer, kind, radius) if layer else None
            growth = roots if layer and layer.mapped_kind == "utility" else canopy
            distance = (
                row.answer.distance_units * engine.factor
                if row.answer and row.answer.distance_units is not None
                else None
            )
            code = detail.reason
            stage: Literal[
                "mapping", "inventory", "query", "area", "rule", "clearance", "site"
            ]
            if code == "source_object":
                code, stage, message, action = failure_reason(row, layer)
                if (
                    not getattr(engine, "sends_cad_queries", True)
                    and layer
                    and layer.mapping_confirmed
                ):
                    code, stage, message, action = (
                        "prepared_geometry_missing",
                        "area",
                        row.measurement.preparation_error
                        if row.measurement
                        else "Нет подготовленной геометрии",
                        "Обновить подготовку геометрии из AutoCAD",
                    )
            elif code.startswith("utility_"):
                stage, message, action = (
                    "rule",
                    utility_requirement(
                        layer.utility_context if layer else None, kind
                    ).description,
                    "Уточнить характеристики сети",
                )
            elif code == "object_interior":
                stage, message, action = (
                    "area",
                    interior_message(row),
                    "Проверить замкнутость и назначение контура",
                )
            else:
                stage = "clearance"
                message = (
                    "Внутри препятствия"
                    if code == "occupied"
                    else "Недостаточный отступ"
                    if code == "clearance"
                    else "Точка у границы ограничения"
                )
                action = "Проверить назначение объекта или выбрать другую точку"
            causes.append(
                GeometryEvidenceItem(
                    code=code,
                    stage=stage,
                    outcome=detail.state,
                    message=message,
                    action=action,
                    source_layer=layer.source_name if layer else row.item.layer,
                    source_feature_ids=list(row.item.routes),
                    measured_distance_m=distance,
                    required_distance_m=max(required or 0, growth)
                    if code in {"clearance", "obstacle_edge"}
                    else None,
                    requirement_basis=(
                        "roots"
                        if layer and layer.mapped_kind == "utility"
                        else "canopy"
                    )
                    if growth > (required or 0)
                    and code in {"clearance", "obstacle_edge"}
                    else "rule"
                    if code in {"clearance", "obstacle_edge"}
                    else None,
                    native_status=row.answer.status if row.answer else None,
                    native_error=(
                        (row.answer.error if row.answer else "")
                        or (
                            row.measurement.preparation_error if row.measurement else ""
                        )
                        or row.item.error
                    )
                    or None,
                    query_sent=getattr(engine, "sends_cad_queries", True)
                    and row.measurement is not None,
                )
            )
        engine.assert_current(project)
        return GeometryEvidence(
            measurement_backend=getattr(engine, "final_check", "autocad"),
            state=verdict.state,
            radius_m=radius,
            canopy_radius_m=canopy,
            root_radius_m=roots,
            considered_objects=len(rows),
            unlocated_objects=sum(r.item.bounds is None for r in rows),
            causes=causes,
        )
