"""DXF ``$INSUNITS`` conversion shared by reading and round-trip export.

Geometry inside the application is always expressed in metres.  The DXF
header, however, may use any unit specified by Autodesk's INSUNITS table.
Keeping the mapping in one place prevents an import/export pair from silently
changing the physical size of a planting plan.
"""

from __future__ import annotations


# DXF R2000+ INSUNITS values. Unitless (0) is deliberately absent: without a
# declared physical scale the import warns and follows the product's explicit
# metre assumption instead of pretending that a conversion is known.
DXF_UNIT_FACTORS: dict[int, tuple[str, float]] = {
    1: ("дюймы", 0.0254),
    2: ("футы", 0.3048),
    3: ("мили", 1609.344),
    4: ("мм", 0.001),
    5: ("см", 0.01),
    6: ("м", 1.0),
    7: ("км", 1_000.0),
    8: ("микродюймы", 0.0000000254),
    9: ("милы", 0.0000254),
    10: ("ярды", 0.9144),
    11: ("ангстремы", 0.0000000001),
    12: ("нанометры", 0.000000001),
    13: ("микроны", 0.000001),
    14: ("дм", 0.1),
    15: ("дам", 10.0),
    16: ("гм", 100.0),
    17: ("гигаметры", 1_000_000_000.0),
    18: ("астрономические единицы", 149_597_870_700.0),
    19: ("световые годы", 9_460_730_472_580_800.0),
    20: ("парсеки", 30_856_775_814_913_673.0),
}


def meters_per_dxf_unit(unit_code: int) -> float | None:
    """Return physical metres per DXF unit, or ``None`` when scale is absent."""

    item = DXF_UNIT_FACTORS.get(unit_code)
    return item[1] if item else None
