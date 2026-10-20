"""Кодирование идентичности исходных областей по проверенному AutoCAD-сопоставлению
экземпляров архива.
"""

from collections.abc import Callable, Generator, Iterator
from contextlib import closing
from io import BytesIO
from pathlib import Path
from typing import Any, BinaryIO, Literal

import ijson

from app.cad_bridge.face_inputs import FaceInputRow, iter_face_inputs
from app.desktop.tickets import read_regular
from app.native_query.capture.capture_contracts import (
    LayeredInstanceMapping,
    decode_instance,
)
from app.native_query.capture.capture_store import _scan_check, read_capture_receipt
from app.native_query.live.live_inventory import LiveInventory

FaceProtocol = Literal[
    "green-atlas.archive-face-preparation/1",
    "green-atlas.archive-face-preparation/2",
    "green-atlas.archive-face-preparation/3",
]
LEGACY_FACE_PROTOCOL: FaceProtocol = "green-atlas.archive-face-preparation/1"
FACE_PROTOCOL: FaceProtocol = "green-atlas.archive-face-preparation/2"
TOPOLOGY_FACE_PROTOCOL: FaceProtocol = "green-atlas.archive-face-preparation/3"
MAX_FACE_REQUEST_BYTES = 96 * 1024**2


def source_route(value: dict[str, Any]) -> str:
    return "/".join((*value.get("instance_chain", ()), value["handle"]))


def clip_layer(name: str) -> bool:
    normalized = name.lower()
    return "граница заказа" in normalized or "граница проектирования" in normalized


class _CheckedFaceStream:
    """Проверить отмену при чтении, сохранив нативный потоковый JSON-парсер."""

    def __init__(self, stream: BinaryIO, check: Callable[[], None]) -> None:
        self.stream, self.check = stream, check

    def read(self, size: int = -1) -> bytes:
        self.check()
        return self.stream.read(size)


def _face_rows(
    stream: BinaryIO, prefix: str, check: Callable[[], None]
) -> Iterator[dict[str, Any]]:
    """Штатный JSON-парсер проверяет отмену и при пропуске чужих коллекций."""
    return ijson.items(_CheckedFaceStream(stream, check), prefix, use_float=True)


def encode_faces(
    root: Path,
    inventory: LiveInventory,
    session_id: str,
    source_sha: str,
    units: int,
    layers: tuple[str, ...],
    check: Callable[[], None],
    *,
    protocol: FaceProtocol = FACE_PROTOCOL,
    companion_layers: tuple[str, ...] = (),
    excluded_routes: frozenset[str] = frozenset(),
    face_inputs_sha256: str | None = None,
) -> bytes:
    """Использовать порядок захваченного покрытия, а не дескрипторы архива для порядка
    face IDs.
    """
    check()
    poll = _scan_check(check)
    receipt = read_capture_receipt(root)
    typed_topology = protocol == TOPOLOGY_FACE_PROTOCOL
    if companion_layers and not typed_topology:
        raise ValueError("Фоновый модуль не поддерживает межслойные границы")
    for names in (layers, companion_layers):
        if len(set(names)) != len(names) or any(
            not name or len(name.encode()) > 4096 or any(c in name for c in "\r\n\x00")
            for name in names
        ):
            raise ValueError("Недопустимый список слоёв подготовки")
    physical_layers, boundary_layers = frozenset(layers), frozenset(companion_layers)
    if physical_layers.intersection(boundary_layers):
        raise ValueError("Граница замыкания не является физическим слоем области")
    if (
        protocol == LEGACY_FACE_PROTOCOL
        and receipt.schema_ != "green-atlas.native-session/1"
    ):
        raise ValueError("Версия фонового модуля не поддерживает CAD-архив проекта")
    selected = {
        item.route: item
        for item in inventory.objects
        if (
            item.curve
            or (
                typed_topology
                and item.layer in boundary_layers
                and item.entity_type in {"AcDbRegion", "AcDbHatch", "REGION", "HATCH"}
            )
        )
        and not item.context
        and (not typed_topology or item.route not in excluded_routes)
        and (
            item.layer in physical_layers
            or (item.layer in boundary_layers and not item.error)
            or (not typed_topology and clip_layer(item.layer))
        )
    }
    aliases = {}
    with read_regular(root / "instances.json") as stream:
        for value in _face_rows(stream, "item", poll):
            row = decode_instance(value, receipt.schema_)
            if row.source_route in selected:
                if row.unavailable:
                    raise ValueError("Расчётный объект недоступен в CAD-архиве")
                aliases[row.source_route] = row
    if set(aliases) != set(selected):
        raise ValueError("CAD-архив не содержит все исходные объекты подготовки")

    def encode(rows: Generator[FaceInputRow, None, None]) -> bytes:
        request = BytesIO()

        def emit(lines: list[str]) -> None:
            if any(
                not value
                or len(value.encode("utf-8")) > 4096
                or any(char in value for char in "\r\n\x00")
                for value in lines
            ):
                raise ValueError(
                    "Исходные сведения не помещаются в контракт подготовки"
                )
            request.write(("\n".join(lines) + "\n").encode("utf-8"))
            if request.tell() > MAX_FACE_REQUEST_BYTES:
                raise ValueError("Подготовка областей превышает лимит CAD-задания")

        emit(
            [
                protocol,
                session_id,
                source_sha,
                (
                    f"{units} {len(layers)} {len(companion_layers)} {len(selected)}"
                    if typed_topology
                    else f"{units} {len(layers)} {len(selected)}"
                ),
                *layers,
                *companion_layers,
            ]
        )
        visited = set()
        with closing(rows):
            for value in rows:
                route = value.route
                if route not in selected:
                    continue
                if route in visited:
                    raise ValueError("Повтор расчётного объекта в исходном снимке")
                visited.add(route)
                row = aliases[route]
                source_layer = value.source_layer
                if (
                    source_layer is None
                    and receipt.schema_ == "green-atlas.native-session/1"
                ):
                    source_layer = value.layer
                if (
                    selected[route].layer != value.layer
                    or row.authored_layer != source_layer
                    or (
                        isinstance(row, LayeredInstanceMapping)
                        and row.effective_layer != value.layer
                    )
                ):
                    raise ValueError("Слой объекта изменён между снимком и перечнем")
                if (
                    protocol == LEGACY_FACE_PROTOCOL
                    and row.authored_layer != value.layer
                ):
                    raise ValueError(
                        "Обновите фоновый модуль для подготовки наследуемых слоёв"
                    )
                emit(
                    [
                        route,
                        row.archive_route,
                        row.class_,
                        row.authored_layer,
                        *([value.layer] if protocol != LEGACY_FACE_PROTOCOL else []),
                        "1" if value.authored else "0",
                        *(
                            " ".join(format(v, ".17g") for v in matrix)
                            for matrix in row.transform
                        ),
                    ]
                )
        if visited != set(selected):
            raise ValueError("В снимке нет расчётных объектов перечня AutoCAD")
        # Проверка непосредственно перед передачей не ограничивается интервалом обхода.
        check()
        return request.getvalue()

    if face_inputs_sha256 is not None:
        try:
            return encode(
                iter_face_inputs(root, receipt.display_sha256, face_inputs_sha256, poll)
            )
        except ValueError:
            # Ошибка строки может обнаружиться раньше завершающего SHA каталога.
            # Только исходный захват определяет отказ; отмена сюда не попадает.
            pass
    return encode(_original_face_inputs(root, poll))


def _original_face_inputs(
    root: Path, check: Callable[[], None]
) -> Generator[FaceInputRow, None, None]:
    """Совместимость старых захватов и повреждённого производного каталога."""
    authored = set()
    with read_regular(root / "Drawing.autocad.json") as stream:
        for value in _face_rows(stream, "regions.item", check):
            if value.get("status") == "native" and not value.get("source_handles"):
                authored.add(source_route(value))
    with read_regular(root / "Drawing.autocad.json") as stream:
        for value in _face_rows(stream, "coverage.item", check):
            route = source_route(value)
            yield FaceInputRow(
                route, value["layer"], value.get("source_layer"), route in authored
            )
