MAX_DXF_CONTENT_BYTES = 50 * 1024 * 1024
MAX_CAD_SNAPSHOT_BYTES = 128 * 1024 * 1024


def dxf_size_error(limit: int | None = None) -> str:
    effective_limit = MAX_DXF_CONTENT_BYTES if limit is None else limit
    return f"DXF должен быть не больше {effective_limit // 1024 // 1024} МБ"


def dxf_filename_error() -> str:
    return "Поддерживаются только файлы в формате DXF"


def validate_dxf_filename(filename: str) -> None:
    if not filename.lower().endswith(".dxf"):
        raise ValueError(dxf_filename_error())
