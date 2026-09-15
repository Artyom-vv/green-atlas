"""Scoped ezdxf binary export compatibility without changing library globals."""

from binascii import Error, unhexlify
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from ezdxf.document import Drawing, custom_export
from ezdxf.lldxf.const import DXF12, LATEST_DXF_VERSION
from ezdxf.lldxf.tagwriter import BinaryTagWriter
from ezdxf.lldxf.types import BINARY_DATA

from app.cad_import.contracts import CadConversionError


class AoiBinaryTagWriter(BinaryTagWriter):
    """ezdxf 1.4.4 proxy export supplies hexadecimal text for binary groups.

    BinaryTagWriter expects bytes. Decode only binary groups, preserving every
    byte, including leading zeroes. Other groups use the unmodified writer.
    """

    decoded_hex_chunks = 0

    def __init__(
        self,
        stream: BinaryIO,
        dxfversion: str = LATEST_DXF_VERSION,
        write_handles: bool = True,
        encoding: str = "utf8",
    ) -> None:
        super().__init__(stream, dxfversion, write_handles, encoding)
        self.exported_handles: set[str] = set()

    def write_tag2(self, code: int, value: object) -> None:
        if code in (5, 105):
            self.exported_handles.add(str(value))
        if code in BINARY_DATA and isinstance(value, str):
            try:
                value = unhexlify(value)
            except (Error, ValueError) as error:
                raise CadConversionError(
                    f"Некорректный hex в бинарной DXF-группе {code}"
                ) from error
            self.decoded_hex_chunks += 1
        super().write_tag2(code, value)


@dataclass(frozen=True)
class BinaryDxfExport:
    decoded_hex_chunks: int
    exported_handles: set[str]


def save_binary_dxf(document: Drawing, output: Path) -> BinaryDxfExport:
    """Use the library's custom-writer hook and normal pending-change lifecycle."""
    document.filename = str(output)
    document.commit_pending_changes()
    with output.open("wb") as stream:
        writer = AoiBinaryTagWriter(
            stream,
            dxfversion=document.dxfversion,
            write_handles=(
                bool(document.header.get("$HANDLING", 0))
                if document.dxfversion == DXF12
                else True
            ),
            encoding=document.output_encoding,
        )
        writer.write_signature()
        custom_export(document, writer)
    return BinaryDxfExport(writer.decoded_hex_chunks, writer.exported_handles)
