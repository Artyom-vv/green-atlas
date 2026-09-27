"""Read/write a compact DXF declaration without embedding private filesystem paths."""

from ezdxf.document import Drawing
from ezdxf.entities import XRecord
from pydantic import ValidationError

from app.dxf_import.preview_contracts import CadPreviewProvenance

PREVIEW_RECORD_KEY = "GREEN_ATLAS_CAD_PREVIEW"
RECORD_CHUNK_CHARACTERS = 240
MAX_RECORD_CHARACTERS = 4096


def write_preview_marker(document: Drawing, provenance: CadPreviewProvenance) -> None:
    if PREVIEW_RECORD_KEY in document.rootdict:
        raise ValueError("В производном CAD уже есть декларация предварительной карты")
    record = document.rootdict.add_xrecord(PREVIEW_RECORD_KEY)
    payload = provenance.model_dump_json()
    record.reset(
        (1, payload[offset : offset + RECORD_CHUNK_CHARACTERS])
        for offset in range(0, len(payload), RECORD_CHUNK_CHARACTERS)
    )


def read_preview_marker(document: Drawing) -> CadPreviewProvenance | None:
    record = document.rootdict.get(PREVIEW_RECORD_KEY)
    if record is None:
        return None
    try:
        if not isinstance(record, XRecord) or any(tag.code != 1 for tag in record.tags):
            raise ValueError("Неверный формат декларации")
        chunks: list[str] = []
        size = 0
        for tag in record.tags:
            size += len(str(tag.value))
            if size > MAX_RECORD_CHARACTERS:
                raise ValueError("Превышен размер декларации")
            chunks.append(str(tag.value))
        return CadPreviewProvenance.model_validate_json("".join(chunks))
    except (ValueError, ValidationError) as error:
        raise ValueError(
            "Повреждена декларация предварительной карты CAD; "
            "нельзя принять её как полный исходный чертёж"
        ) from error
