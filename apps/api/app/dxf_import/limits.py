"""Лимиты размера и проверка имени прежнего DXF-входа; они отделены от бюджета
подготовки native-снимков.
"""

from app.native_query.capture.capture_contracts import MAX_CAPTURE_DISPLAY_BYTES

MAX_DXF_CONTENT_BYTES = 50 * 1024 * 1024
# Native-учёт экземпляров должен оставаться полным. Реальные чертежи ЛЦТ дают
# снимки 264 МиБ и более 500 МиБ; их отказ вернул бы поддержку только малых DXF.
MAX_CAD_SNAPSHOT_BYTES = MAX_CAPTURE_DISPLAY_BYTES


def dxf_size_error(limit: int | None = None) -> str:
    effective_limit = MAX_DXF_CONTENT_BYTES if limit is None else limit
    return f"DXF должен быть не больше {effective_limit // 1024 // 1024} МБ"


def dxf_filename_error() -> str:
    return "Поддерживаются только файлы в формате DXF"


def validate_dxf_filename(filename: str) -> None:
    if not filename.lower().endswith(".dxf"):
        raise ValueError(dxf_filename_error())
