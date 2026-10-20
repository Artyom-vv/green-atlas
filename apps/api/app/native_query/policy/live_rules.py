"""Общие правила посадки применяются к геометрическим измерениям, а не к отображаемым
полигонам карты.
"""

from dataclasses import dataclass
from typing import Protocol

from app.dxf_import.layers.contracts import Layer
from app.geometry.checking.domain import PositionViolation
from app.native_query.live.live_inventory import QueryObject
from app.native_query.policy.calculation_rules import calculation_rule
from app.regulations.network_rules import DISTANCE_TOLERANCE_M


class ObjectMeasurement(Protocol):
    @property
    def interior_known(self) -> bool: ...
    @property
    def capability(self) -> str: ...
    @property
    def preparation_error(self) -> str: ...


class PointMeasurement(Protocol):
    @property
    def membership(self) -> str: ...
    @property
    def distance_units(self) -> float | None: ...
    @property
    def status(self) -> int: ...
    @property
    def error(self) -> str: ...


@dataclass(frozen=True)
class MeasuredObstacle:
    item: QueryObject
    measurement: ObjectMeasurement | None
    answer: PointMeasurement | None


def required_distance(layer: Layer, kind: str, radius: float) -> float | None:
    rule = calculation_rule(layer, kind, radius)
    return rule.distance_m if rule else None


def violation(
    row: MeasuredObstacle, layer: Layer, kind: str, radius: float, factor: float
) -> PositionViolation | None:
    answer = row.answer
    if (
        not answer
        or answer.status
        or answer.error
        or layer.mapped_kind == "site_border"
    ):
        return None
    role = layer.mapped_kind
    required = required_distance(layer, kind, radius)
    occupied = answer.membership in {"occupied", "edge"}
    distance = (
        answer.distance_units * factor if answer.distance_units is not None else None
    )
    # Известная принадлежность CAD-препятствию сохраняется и при неизвестном правиле отступа.
    if not occupied and (
        required is None
        or distance is None
        or distance + DISTANCE_TOLERANCE_M >= required
    ):
        return None
    label = {
        "building": "здания",
        "road": "дороги или проезда",
        "utility": "инженерной сети",
        "existing_green": "существующего озеленения",
        "water": "водного объекта",
        "restricted": "технической зоны",
    }.get(role or "", "препятствия")
    rule = calculation_rule(layer, kind, radius)
    rule_id = rule.id if rule else f"native-{role}"
    description = (
        f"Позиция внутри {label}, слой «{layer.source_name}»"
        if occupied
        else (
            f"До {label} {distance:.2f} м, требуется {required:.2f} м".replace(".", ",")
            + f", слой «{layer.source_name}»"
        )
    )
    return PositionViolation(
        code="NATIVE_OCCUPIED" if occupied else "NATIVE_CLEARANCE",
        title="Ограничение посадки",
        description=description,
        rule_id=rule_id,
        actual=None if occupied else distance,
        required=None if occupied else required,
        suggested_action="Выбрать другую позицию",
        source_layer=layer.source_name,
        source_feature_ids=row.item.routes,
    )
