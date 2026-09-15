"""Resolve source CAD styling without repairing the original drawing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ezdxf.colors import aci2rgb
from ezdxf.document import Drawing
from ezdxf.lldxf.const import DXFTableEntryError

DEFAULT_LINETYPE = "CONTINUOUS"
BY_BLOCK = 0
BY_LAYER = 256
INHERIT_LINEWEIGHT = -1
MONOCHROME_COLORS = {"#FFFFFF", "#000000"}


def _color(rgb: Any, aci: int, fallback: str) -> str:
    if rgb is None:
        if not 1 <= abs(aci) <= 255:
            return fallback
        rgb = aci2rgb(abs(aci))
    value = "#{:02X}{:02X}{:02X}".format(*(int(part) for part in rgb))
    return fallback if value in MONOCHROME_COLORS else value


@dataclass(frozen=True)
class SourceLayerStyle:
    color: str
    linetype: str = DEFAULT_LINETYPE
    lineweight: int = INHERIT_LINEWEIGHT
    definition_missing: bool = False

    @property
    def lineweight_mm(self) -> float | None:
        return round(self.lineweight / 100, 2) if self.lineweight >= 0 else None


class SourceStyleResolver:
    """One import owns the cache and missing-definition diagnostics."""

    def __init__(self, document: Drawing) -> None:
        self.document = document
        self.missing_layers: set[str] = set()
        self._layers: dict[tuple[str, str], SourceLayerStyle] = {}

    def layer(self, name: str, fallback: str) -> SourceLayerStyle:
        key = (name, fallback)
        if key in self._layers:
            return self._layers[key]
        try:
            layer = self.document.layers.get(name)
        except DXFTableEntryError:
            self.missing_layers.add(name)
            result = SourceLayerStyle(color=fallback, definition_missing=True)
        else:
            result = SourceLayerStyle(
                color=_color(layer.rgb, int(layer.dxf.get("color", 7)), fallback),
                linetype=str(layer.dxf.get("linetype", DEFAULT_LINETYPE)),
                lineweight=int(layer.dxf.get("lineweight", INHERIT_LINEWEIGHT)),
            )
        self._layers[key] = result
        return result

    def entity(self, entity: Any, name: str, fallback: str) -> dict[str, Any]:
        layer = self.layer(name, fallback)
        aci = int(entity.dxf.get("color", BY_LAYER))
        rgb = entity.rgb
        color = (
            layer.color
            if rgb is None and aci in {BY_BLOCK, BY_LAYER}
            else _color(rgb, aci, fallback)
        )
        linetype = str(entity.dxf.get("linetype", "BYLAYER"))
        if linetype.upper() in {"BYLAYER", "BYBLOCK"}:
            linetype = layer.linetype
        lineweight = int(entity.dxf.get("lineweight", INHERIT_LINEWEIGHT))
        if lineweight < 0:
            lineweight = layer.lineweight
        result: dict[str, Any] = {
            "source_color": color,
            "source_linetype": linetype,
            "source_lineweight_mm": round(lineweight / 100, 2)
            if lineweight >= 0
            else None,
        }
        if layer.definition_missing:
            result["source_layer_definition_missing"] = True
        return result

    def warnings(self) -> list[str]:
        if not self.missing_layers:
            return []
        names = ", ".join(sorted(self.missing_layers))
        return [
            f"В таблице DXF нет определений слоёв: {names}. "
            "Ссылки сущностей и геометрия сохранены; для отсутствующего "
            "оформления используется резервный стиль карты. Исходный DXF не изменён."
        ]
