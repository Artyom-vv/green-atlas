"""Проверка согласованности сохранённого отображения и перечня объектов без загрузки
всей карты.
"""

from collections.abc import Callable
from pathlib import Path

import ijson

from app.desktop.tickets import read_regular
from app.native_query.capture.capture_contracts import (
    CaptureReceipt,
    LayeredInstanceMapping,
    decode_instance,
)
from app.native_query.capture.capture_store import read_capture_receipt
from app.native_query.live.live_inventory import LiveInventory, read_inventory


def validate_inputs(
    root: Path, check: Callable[[], None], receipt: CaptureReceipt | None = None
) -> LiveInventory:
    receipt = receipt or read_capture_receipt(root)
    with read_regular(root / "Drawing.autocad.json.inventory.json") as stream:
        inventory = read_inventory(stream, check)
    indexed = {item.route: item for item in inventory.objects}
    mapped: dict[str, tuple[str, str | None]] = {}
    targets = set()
    with read_regular(root / "instances.json") as stream:
        if next(ijson.parse(stream), None) != ("", "start_array", None):
            raise ValueError("Native instance index must be an array")
        stream.seek(0)
        for value in ijson.items(stream, "item", use_float=True):
            check()
            row = decode_instance(value, receipt.schema_)
            item = indexed.get(row.source_route)
            if (
                item is None
                or row.source_route in mapped
                or item.entity_type != row.class_
                or row.archive_route in targets
                or (
                    isinstance(row, LayeredInstanceMapping)
                    and row.effective_layer != item.layer
                )
            ):
                raise ValueError("CAD-архив и перечень имеют разные исходные объекты")
            mapped[row.source_route] = (
                row.authored_layer,
                row.effective_layer
                if isinstance(row, LayeredInstanceMapping)
                else None,
            )
            targets.add(row.archive_route)
            if len(mapped) > receipt.instance_count:
                raise ValueError("Native instance count exceeded")
    if set(mapped) != set(indexed):
        raise ValueError("CAD-архив не содержит весь перечень исходных объектов")
    if len(mapped) != receipt.instance_count:
        raise ValueError("Native instance count is incomplete")
    visited = set()
    with read_regular(root / "Drawing.autocad.json") as stream:
        for row in ijson.items(stream, "coverage.item", use_float=True):
            check()
            route = "/".join((*row.get("instance_chain", ()), row["handle"]))
            item = indexed.get(route)
            authored_layer, effective_layer = mapped.get(route, (None, None))
            # В /1 нет действующего слоя. Native-слой сравнивается с явным происхождением
            # карты; без него доказуем лишь неизменённый слой, а наследование не угадывается.
            source_layer = row.get("source_layer")
            if (
                source_layer is None
                and receipt.schema_ == "green-atlas.native-session/1"
            ):
                source_layer = row["layer"]
            if (
                item is None
                or route in visited
                or item.layer != row["layer"]
                or item.entity_type != row["entity_type"]
                or item.context != (row["status"] == "context")
                or authored_layer != source_layer
                or (effective_layer is not None and effective_layer != row["layer"])
            ):
                raise ValueError("Перечень объектов не соответствует исходному снимку")
            visited.add(route)
    if visited != set(indexed):
        raise ValueError("Исходный снимок и перечень имеют разный состав объектов")
    return inventory
