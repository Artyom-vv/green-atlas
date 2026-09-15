"""Record narrow compatibility repairs for inactive derived annotation fields."""

from ezdxf.document import Drawing
from ezdxf.entities import MultiLeader

from app.cad_import.aoi_contracts import AoiAnnotationRepair, AoiEntityLink
from app.cad_import.contracts import CadConversionError

TOP_ATTACHMENT_OVERRIDE = 1 << 28
SIGNED_SHORT_MIN = -(1 << 15)
SIGNED_SHORT_MAX = (1 << 15) - 1
VERTICAL_ATTACHMENT_TYPES = {9, 10}


def repair_inactive_attachments(
    target: Drawing, links: list[AoiEntityLink]
) -> list[AoiAnnotationRepair]:
    """Use existing context only for inactive, non-overridden vertical fields.

    The override flag alone is not authoritative in ezdxf. Also require every
    actual leader and the global direction to be explicitly horizontal.
    This repairs preview serialization, not arbitrary source CAD semantics.
    """
    origins = {link.output_handle: link for link in links}
    repairs = []
    for entity in target.entitydb.values():
        if not isinstance(entity, MultiLeader) or not entity.is_alive:
            continue
        raw = int(entity.dxf.text_top_attachment_type)
        if SIGNED_SHORT_MIN <= raw <= SIGNED_SHORT_MAX:
            continue
        directions = [leader.attachment_direction for leader in entity.context.leaders]
        override = int(entity.dxf.property_override_flags)
        replacement = entity.context.top_attachment
        origin = origins.get(entity.dxf.handle)
        if (
            entity.dxf.text_attachment_direction != 0
            or not directions
            or any(direction != 0 for direction in directions)
            or override & TOP_ATTACHMENT_OVERRIDE
            or replacement not in VERTICAL_ATTACHMENT_TYPES
            or origin is None
        ):
            raise CadConversionError(
                f"MULTILEADER #{entity.dxf.handle}: группа 273={raw} "
                "не может быть восстановлена как неактивная привязка"
            )
        repairs.append(
            AoiAnnotationRepair(
                source_sha256=origin.source_sha256,
                source_handle=origin.source_handle,
                output_handle=origin.output_handle,
                insert_chain=origin.insert_chain,
                original_value=raw,
                normalized_value=replacement,
                leader_attachment_directions=directions,
                property_override_flags=override,
            )
        )
        entity.dxf.text_top_attachment_type = replacement
    return repairs
