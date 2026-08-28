from __future__ import annotations

import re

from ezdxf.tools.codepage import toencoding


# Header variables are ASCII DXF tags even when the body uses a legacy code
# page.  Read only this bounded prefix as latin-1 (a lossless byte mapping),
# then let the declared DXF version and code page decide how to decode the
# complete source.  A generic CP1251 fallback is not a safe default: it
# silently turns a CP1252 label such as ``Café`` into another word.
_HEADER_VARIABLE = re.compile(
    r"^[ \t]*9[ \t]*\r?\n\$(ACADVER|DWGCODEPAGE)[ \t]*\r?\n[ \t]*(?:1|3)[ \t]*\r?\n[ \t]*([^\r\n]+)",
    re.MULTILINE | re.IGNORECASE,
)
_DXF_2007_VERSION = 1021
_HEADER_PROBE_BYTES = 256 * 1024


def _header_values(content: bytes | bytearray) -> dict[str, str]:
    probe = content[:_HEADER_PROBE_BYTES].decode("latin-1")
    return {key.upper(): value.strip() for key, value in _HEADER_VARIABLE.findall(probe)}


def _is_utf8_dxf(version: str | None) -> bool:
    if not version:
        return False
    match = re.fullmatch(r"AC(\d+)", version.upper())
    return bool(match and int(match.group(1)) >= _DXF_2007_VERSION)


def decode_text_dxf(content: bytes | bytearray) -> str:
    """Decode an ASCII DXF without guessing across declared code pages.

    DXF R2007 and newer use UTF-8. Earlier text DXF uses the code page named
    by ``$DWGCODEPAGE``. The final fallbacks preserve compatibility with
    malformed drawings that omit either header variable, but only after a
    declared encoding was tried first.
    """

    header = _header_values(content)
    attempts: list[str] = []
    if _is_utf8_dxf(header.get("ACADVER")):
        attempts.append("utf-8-sig")
    codepage = header.get("DWGCODEPAGE")
    if codepage:
        try:
            attempts.append(toencoding(codepage))
        except (KeyError, LookupError, ValueError):
            pass
    attempts.extend(("utf-8-sig", "cp1251", "latin-1"))

    tried: set[str] = set()
    for encoding in attempts:
        normalized = encoding.lower().replace("_", "-")
        if normalized in tried:
            continue
        tried.add(normalized)
        try:
            return content.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    raise ValueError("Не удалось определить кодировку DXF")
