"""Decode proven raw colors misplaced in derived DIMSTYLE ACI fields only."""

from ezdxf.colors import (
    COLOR_TYPE_ACI,
    COLOR_TYPE_BY_BLOCK,
    COLOR_TYPE_BY_LAYER,
    decode_raw_color_int,
    encode_raw_color,
)
from ezdxf.document import Drawing

from app.cad_import.aoi_contracts import AoiEntityLink, AoiStyleRepair
from app.cad_import.contracts import CadConversionError

DIMSTYLE_ACI_FIELDS = {"dimclrd": 176, "dimclre": 177, "dimclrt": 178}
LOSSLESS_ACI_RAW_TYPES = {COLOR_TYPE_ACI, COLOR_TYPE_BY_BLOCK, COLOR_TYPE_BY_LAYER}


def normalize_dimension_colors(
    target: Drawing,
    sources: dict[str, Drawing],
    links: list[AoiEntityLink],
) -> list[AoiStyleRepair]:
    """Keep valid ACI; require exact raw-color roundtrip, never RGB reduction.

    Autodesk DIMCLRD/E/T use ACI 0..256. LibreDWG-derived source evidence has
    canonical 32-bit raw color values in those 16-bit fields. Only the output
    style changes; its original handle/value and chosen policy stay in evidence.
    """
    origins = {
        link.output_handle: link for link in links if link.entity_type == "DIMSTYLE"
    }
    repairs = []
    for style in target.dimstyles:
        for attribute, group in DIMSTYLE_ACI_FIELDS.items():
            raw = int(style.dxf.get(attribute, 0))
            if 0 <= raw <= 256:
                continue
            try:
                kind, aci = decode_raw_color_int(raw)
                canonical = (
                    kind in LOSSLESS_ACI_RAW_TYPES and encode_raw_color(aci) == raw
                )
            except ValueError:
                canonical = False
            origin = origins.get(style.dxf.handle)
            if not canonical or origin is None or origin.source_sha256 not in sources:
                raise CadConversionError(
                    f"DIMSTYLE {style.dxf.name}: цвет группы {group}={raw} "
                    "не имеет подтверждённого преобразования в ACI"
                )
            original = sources[origin.source_sha256].entitydb[origin.source_handle]
            repairs.append(
                AoiStyleRepair(
                    source_sha256=origin.source_sha256,
                    source_handle=origin.source_handle,
                    source_name=original.dxf.name,
                    output_handle=style.dxf.handle,
                    output_name=style.dxf.name,
                    group=group,
                    original_value=raw,
                    normalized_value=aci,
                )
            )
            style.dxf.set(attribute, aci)
    return repairs
