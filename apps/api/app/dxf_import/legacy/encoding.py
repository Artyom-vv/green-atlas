"""Строгое декодирование текста DXF по версии и объявленной кодовой странице без замены
повреждённых символов.
"""

from __future__ import annotations

import codecs
import re
from dataclasses import dataclass
from enum import StrEnum

from ezdxf.tools.codepage import codepage_to_encoding, toencoding

# Переменные заголовка являются ASCII-тегами даже при старой кодовой странице.
# Ограниченный префикс читаем как latin-1 без потери байтов; полный исходник
# декодируется по объявленной версии DXF и странице. Общий запасной CP1251
# исказил бы, например, подпись Café из CP1252.
_HEADER_VARIABLE = re.compile(
    r"^[ \t]*9[ \t]*\r?\n\$(ACADVER|DWGCODEPAGE)[ \t]*\r?\n[ \t]*(?:1|3)[ \t]*\r?\n[ \t]*([^\r\n]+)",
    re.MULTILINE | re.IGNORECASE,
)
_DXF_2007_VERSION = 1021
DXF_HEADER_PROBE_BYTES = 256 * 1024
DECODE_VALIDATION_CHUNK_BYTES = 64 * 1024
_DECLARED_CODEPAGES = frozenset(f"ANSI_{value}" for value in codepage_to_encoding)


class DxfTextEncoding(StrEnum):
    """Browser TextDecoder labels for codecs supported by the DXF reader."""

    UTF8 = "utf-8"
    CP874 = "windows-874"
    CP932 = "shift_jis"
    GBK = "gbk"
    CP949 = "euc-kr"
    CP950 = "big5"
    CP1250 = "windows-1250"
    CP1251 = "windows-1251"
    CP1252 = "windows-1252"
    CP1253 = "windows-1253"
    CP1254 = "windows-1254"
    CP1255 = "windows-1255"
    CP1256 = "windows-1256"
    CP1257 = "windows-1257"
    CP1258 = "windows-1258"


@dataclass(frozen=True)
class DeclaredDxfEncoding:
    python_codec: str
    browser_codec: DxfTextEncoding


def _header_values(content: bytes | bytearray) -> dict[str, str]:
    probe = content[:DXF_HEADER_PROBE_BYTES].decode("latin-1")
    return {
        key.upper(): value.strip() for key, value in _HEADER_VARIABLE.findall(probe)
    }


def declared_dxf_version(content: bytes | bytearray) -> str | None:
    """Читать тег формата файла без вывода версии по году продукта AutoCAD."""
    version = _header_values(content).get("ACADVER", "").upper()
    return version if re.fullmatch(r"AC\d{4}", version) else None


def _is_utf8_dxf(version: str | None) -> bool:
    if not version:
        return False
    match = re.fullmatch(r"AC(\d+)", version.upper())
    return bool(match and int(match.group(1)) >= _DXF_2007_VERSION)


def declared_text_encoding(
    content: bytes | bytearray, *, expected_version: str
) -> DeclaredDxfEncoding | None:
    """Разрешать только явно объявленную браузерную кодировку.

    Прежний импорт мог использовать запасные варианты decode_text_dxf. Без
    однозначного заголовка renderer сохраняет OL-представление, не угадывая другую
    интерпретацию исходных байтов.
    """
    header = _header_values(content)
    version = header.get("ACADVER")
    if not version or version.upper() != expected_version.upper():
        return None
    if _is_utf8_dxf(version):
        return DeclaredDxfEncoding("utf-8-sig", DxfTextEncoding.UTF8)
    codepage = header.get("DWGCODEPAGE", "").upper()
    if codepage not in _DECLARED_CODEPAGES:
        return None
    codec = toencoding(codepage)
    return DeclaredDxfEncoding(codec, DxfTextEncoding[codec.upper()])


def validate_text_encoding(content: bytes, encoding: DeclaredDxfEncoding) -> None:
    """Строго проверять байты без построения полной декодированной CAD-строки."""
    decoder = codecs.getincrementaldecoder(encoding.python_codec)(errors="strict")
    view = memoryview(content)
    for offset in range(0, len(view), DECODE_VALIDATION_CHUNK_BYTES):
        decoder.decode(view[offset : offset + DECODE_VALIDATION_CHUNK_BYTES])
    decoder.decode(b"", final=True)


def decode_text_dxf(content: bytes | bytearray) -> str:
    """Декодировать текстовый DXF по объявленной кодовой странице.

    DXF R2007 и новее используют UTF-8; старые версии задают $DWGCODEPAGE. Запасные
    варианты сохраняют совместимость неполных заголовков только после попытки
    объявленной кодировки.
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
