"""Перечень native-экземпляров; габариты выбирают объекты для запросов и не объявляют
занятый грунт.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, BinaryIO, Literal

import ijson
from ijson.common import ObjectBuilder
from pydantic import Field, FiniteFloat, TypeAdapter, model_validator

from app.native_query.capture.capture_contracts import (
    MAX_CAPTURE_INVENTORY_BYTES,
    MAX_CAPTURE_OBJECTS,
    MAX_INVENTORY_RECORD_BYTES,
)
from app.native_query.contracts import NativeDto, NativeTarget
from app.native_query.live.live_client import LiveSession
from app.native_query.preparation.face_contracts import NativeFace
from app.operations.progress import ProgressReporter, progress_items

Bounds = tuple[FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat]


class InventoryObject(NativeDto):
    # Недоступные запросу маршруты MINSERT остаются в журнале с native-ошибкой.
    route: str = Field(min_length=1)
    layer: str
    entity_type: str
    context: bool
    bounds: Bounds | None
    curve: bool
    error: str


class InventoryGroup(NativeDto):
    routes: tuple[str, ...] = Field(min_length=1)
    error: str


class LiveInventory(NativeDto):
    schema_: Literal["green-atlas.live-inventory/1"] = Field(alias="schema")
    objects: tuple[InventoryObject, ...] = Field(max_length=MAX_CAPTURE_OBJECTS)
    groups: tuple[InventoryGroup, ...]
    face_policy: Literal["native-planar-faces/1", "native-planar-faces/2"] | None = None
    companion_layers: tuple[str, ...] = ()
    face_preparation_available: bool = False
    faces: tuple[NativeFace, ...] = ()
    linear_routes: tuple[str, ...] = ()
    face_issues: tuple[str, ...] = ()

    @model_validator(mode="after")
    def consistent(self) -> LiveInventory:
        indexed = {item.route: item for item in self.objects}
        if len(indexed) != len(self.objects):
            raise ValueError("Duplicate native inventory route")
        if (self.faces or self.linear_routes) and self.face_policy is None:
            raise ValueError("Native faces require an explicit preparation policy")
        if len({f.id for f in self.faces}) != len(self.faces):
            raise ValueError("Duplicate native face identity")
        if len(set(self.companion_layers)) != len(self.companion_layers) or (
            self.companion_layers and self.face_policy != "native-planar-faces/2"
        ):
            raise ValueError("Границы не соответствуют политике native-подготовки")
        for face in self.faces:
            repair_routes = {
                route for repair in face.repairs for route in (repair.from_, repair.to)
            }
            if any(route not in indexed for route in face.routes) or any(
                indexed[route].layer != face.layer for route in face.physical_routes
            ):
                raise ValueError("Native face lost its source provenance")
            if self.face_policy == "native-planar-faces/2" and (
                face.layer in self.companion_layers
                or any(
                    indexed[route].error
                    or indexed[route].context
                    or (route in face.physical_routes and not indexed[route].curve)
                    or (
                        route not in face.physical_routes
                        and not (
                            route in repair_routes
                            and indexed[route].layer == face.layer
                            and indexed[route].curve
                        )
                        and (
                            indexed[route].layer not in self.companion_layers
                            or not (
                                indexed[route].curve
                                or indexed[route].entity_type
                                in {"AcDbRegion", "AcDbHatch", "REGION", "HATCH"}
                            )
                        )
                    )
                    for route in face.routes
                )
            ):
                raise ValueError("Область содержит незаявленную межслойную границу")
        if any(
            route not in indexed or not indexed[route].curve or indexed[route].error
            for route in self.linear_routes
        ):
            raise ValueError("Native linear remainder is not a readable curve")
        grouped: set[str] = set()
        for group in self.groups:
            if len(set(group.routes)) != len(group.routes):
                raise ValueError("Duplicate native group member")
            if any(route not in indexed or route in grouped for route in group.routes):
                raise ValueError("Native group membership is inconsistent")
            if len({indexed[route].layer for route in group.routes}) != 1:
                raise ValueError("Native group crosses effective layers")
            grouped.update(group.routes)
        for item in self.objects:
            if item.bounds and (
                item.bounds[0] > item.bounds[2] or item.bounds[1] > item.bounds[3]
            ):
                raise ValueError("Invalid native extents")
        return self


InventoryEvent = tuple[str, str, Any]


def _inventory_events(
    stream: BinaryIO, check: Callable[[], None], expected_sha256: str | None
) -> Iterator[InventoryEvent]:
    """Хешировать те же порции, которые разбирает готовый JSON-парсер."""
    events = ijson.sendable_list()
    parser = ijson.parse_coro(events, use_float=True)
    digest, size = hashlib.sha256(), 0
    try:
        while chunk := stream.read(64 * 1024):
            check()
            size += len(chunk)
            if size > MAX_CAPTURE_INVENTORY_BYTES:
                raise ValueError("Перечень объектов превышает бюджет захвата")
            digest.update(chunk)
            parser.send(chunk)
            yield from events
            events.clear()
        parser.close()
        yield from events
    except ijson.JSONError as error:
        raise ValueError("Некорректный JSON перечня объектов AutoCAD") from error
    if expected_sha256 is not None and digest.hexdigest() != expected_sha256:
        raise ValueError("Перечень объектов изменён после захвата")


def _inventory_value(events: Iterator[InventoryEvent], first: InventoryEvent) -> Any:
    """Собирать одну ограниченную запись, без всего массива словарей в памяти."""
    builder, depth, retained = ObjectBuilder(), 0, 0
    map_keys: list[set[str]] = []
    current = first
    while True:
        _, event, value = current
        retained += 64 + (4 * len(value) if isinstance(value, str) else 0)
        if retained > MAX_INVENTORY_RECORD_BYTES:
            raise ValueError("Запись перечня объектов превышает бюджет памяти")
        if event == "start_map":
            map_keys.append(set())
        elif event == "map_key":
            if value in map_keys[-1]:
                raise ValueError("Повторное поле записи перечня объектов")
            map_keys[-1].add(value)
        elif event == "end_map":
            map_keys.pop()
        builder.event(event, value)
        depth += (event in {"start_map", "start_array"}) - (
            event in {"end_map", "end_array"}
        )
        if depth == 0:
            return builder.value
        current = next(events)


def read_inventory(
    stream: BinaryIO,
    check: Callable[[], None] = lambda: None,
    *,
    expected_sha256: str | None = None,
) -> LiveInventory:
    """Читать полный типизированный перечень за один проход и проверить его до EOF."""
    adapters: dict[str, TypeAdapter[Any]] = {
        "objects": TypeAdapter(InventoryObject),
        "groups": TypeAdapter(InventoryGroup),
        "faces": TypeAdapter(NativeFace),
        "companion_layers": TypeAdapter(str),
        "linear_routes": TypeAdapter(str),
        "face_issues": TypeAdapter(str),
    }
    fields = {field.alias or name for name, field in LiveInventory.model_fields.items()}
    payload: dict[str, Any] = {}
    events = _inventory_events(stream, check, expected_sha256)
    try:
        if next(events) != ("", "start_map", None):
            raise ValueError("Перечень объектов должен быть JSON-объектом")
        for prefix, event, key in events:
            if (prefix, event) == ("", "end_map"):
                # Парсер и проверка SHA должны прочитать также завершающие байты.
                if next(events, None) is not None:
                    raise ValueError("Перечень должен содержать один JSON-объект")
                return LiveInventory.model_validate(payload)
            if prefix or event != "map_key" or key not in fields or key in payload:
                raise ValueError("Неизвестное или повторное поле перечня объектов")
            first = next(events)
            if key not in adapters:
                payload[key] = _inventory_value(events, first)
                continue
            if first != (key, "start_array", None):
                raise ValueError("Коллекция перечня объектов должна быть массивом")
            rows: list[Any] = []
            while (first := next(events)) != (key, "end_array", None):
                check()
                if len(rows) >= MAX_CAPTURE_OBJECTS:
                    raise ValueError("Перечень превышает число объектов захвата")
                rows.append(
                    adapters[key].validate_python(_inventory_value(events, first))
                )
            payload[key] = tuple(rows)
    except StopIteration as error:
        raise ValueError("Перечень объектов не завершён") from error
    raise ValueError("Перечень объектов не завершён")


def load_inventory(
    session: LiveSession, *, check: Callable[[], None] = lambda: None
) -> LiveInventory:
    from app.desktop.tickets import read_regular

    if not session.inventory_path or not session.inventory_sha256:
        raise ValueError("Сеанс AutoCAD не содержит перечня препятствий")
    path = Path(session.inventory_path)
    expected = Path(session.snapshot_path + ".inventory.json")
    if (
        path.resolve() != expected.resolve()
        or path.stat().st_size > MAX_CAPTURE_INVENTORY_BYTES
    ):
        raise ValueError("Недопустимый перечень объектов AutoCAD")
    with read_regular(path) as stream:
        return read_inventory(
            stream, check=check, expected_sha256=session.inventory_sha256
        )


class QueryObject(NativeDto):
    routes: tuple[str, ...]
    layer: str
    bounds: Bounds | None
    curve: bool
    error: str
    # Сбой сборки области не удаляет читаемые исходные кривые. Они измеряются
    # отдельно без соединения или восстановления в Python.
    curve_routes: tuple[str, ...] = ()
    reviewed_closure: bool = False
    reviewed_linear: bool = False
    native_linear: bool = False
    face_id: int = 0

    def target(self, *, area: bool) -> NativeTarget | None:
        if self.face_id:
            return NativeTarget(
                route=self.routes[0], capability="area", face_id=self.face_id
            )
        if self.error or len(self.routes) > 256:
            return None
        return NativeTarget(
            route=self.routes[0],
            capability=("closed_area" if self.reviewed_closure else "area")
            if area and not (self.reviewed_linear or self.native_linear)
            else "curve",
            additional_routes=self.routes[1:],
        )

    def distance_to_bounds(self, x: float, y: float) -> float:
        if self.bounds is None:
            return 0.0
        x0, y0, x1, y1 = self.bounds
        return (max(x0 - x, 0, x - x1) ** 2 + max(y0 - y, 0, y - y1) ** 2) ** 0.5


def query_objects(
    inventory: LiveInventory,
    group_layers: set[str] | None = None,
    reviewed_closures: frozenset[str] = frozenset(),
    linear_routes: frozenset[str] = frozenset(),
    ignored_routes: frozenset[str] = frozenset(),
    native_linear_routes: frozenset[str] = frozenset(),
    *,
    progress: ProgressReporter | None = None,
) -> tuple[QueryObject, ...]:
    indexed = {item.route: item for item in inventory.objects}
    grouped: set[str] = set()
    result = []
    overridden = (
        reviewed_closures | linear_routes | ignored_routes | native_linear_routes
    )
    for group in inventory.groups:
        # Только точные связные циклы являются native-целями области. Незамкнутые
        # и неоднозначные цепочки сохраняют общий локальный габарит неопределённости.
        routes = tuple(route for route in group.routes if route not in overridden)
        if not routes:
            continue
        members = [indexed[route] for route in routes]
        if group_layers is not None and members[0].layer not in group_layers:
            continue
        if len(members) == 1:
            continue
        # Сохраняем общий габарит неопределённости группы: разделение на рёбра
        # могло бы потерять нерешённое внутреннее пространство между ними.
        changed = len(routes) != len(group.routes)
        error = "open native endpoint chain" if changed else group.error
        grouped.update(routes)
        bounds = [item.bounds for item in members if item.bounds is not None]
        merged = (
            None
            if len(bounds) != len(members)
            else (
                min(b[0] for b in bounds),
                min(b[1] for b in bounds),
                max(b[2] for b in bounds),
                max(b[3] for b in bounds),
            )
        )
        result.append(
            QueryObject(
                routes=routes,
                layer=members[0].layer,
                bounds=merged,
                curve=True,
                error=error,
                curve_routes=routes
                if (
                    error
                    in {
                        "open native endpoint chain",
                        "ambiguous native endpoint junction",
                    }
                    or len(routes) > 256
                )
                and all(item.curve and not item.error for item in members)
                else (),
            )
        )
    items = (
        progress_items(
            inventory.objects,
            progress,
            "Проверяем объекты захвата",
            len(inventory.objects),
        )
        if progress is not None
        else inventory.objects
    )
    for item in items:
        if (
            not item.context
            and item.route not in grouped
            and item.route not in ignored_routes
        ):
            result.append(
                QueryObject(
                    routes=(item.route,),
                    layer=item.layer,
                    # Замыкание полилинии активирует последнюю дугу bulge. Старые габариты
                    # незамкнутого контура не обязаны её охватывать; до новых native-габаритов
                    # контур не исключается предварительным отбором.
                    bounds=None if item.route in reviewed_closures else item.bounds,
                    curve=item.curve,
                    error=item.error,
                    reviewed_closure=item.route in reviewed_closures,
                    reviewed_linear=item.route in linear_routes,
                    native_linear=item.route in native_linear_routes,
                )
            )
    return tuple(result)
