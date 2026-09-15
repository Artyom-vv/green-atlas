"""A bounded byte response on the existing download URL, including SDK Range."""

import re
from urllib.parse import quote

from fastapi.responses import Response

from app.dxf_import.native_application import NativeSourceDownload


def _range(value: str, size: int) -> tuple[int, int] | None:
    match = re.fullmatch(r"bytes=([0-9]*)-([0-9]*)", value.strip())
    if match is None or not size:
        return None
    first, last = match.groups()
    try:
        if not first:
            suffix = int(last) if last else 0
            return (max(0, size - suffix), size) if suffix else None
        start = int(first)
        end = min(size, int(last) + 1) if last else size
    except ValueError:
        return None
    return (start, end) if 0 <= start < end <= size else None


def source_download_response(
    source: NativeSourceDownload,
    range_header: str | None,
    if_range: str | None,
) -> Response:
    etag = f'"{source.sha256}"'
    size = len(source.content)
    headers = {
        "ETag": etag,
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, no-cache",
        "X-Content-Type-Options": "nosniff",
        "Content-Disposition": "attachment; filename=source.dxf; "
        f"filename*=UTF-8''{quote(source.name)}",
    }
    content = source.content
    status = 200
    if range_header is not None and (if_range is None or if_range == etag):
        selected = _range(range_header, size)
        if selected is None:
            return Response(
                status_code=416, headers={**headers, "Content-Range": f"bytes */{size}"}
            )
        start, end = selected
        content = content[start:end]
        status = 206
        headers["Content-Range"] = f"bytes {start}-{end - 1}/{size}"
    return Response(
        content=content,
        status_code=status,
        media_type="application/dxf",
        headers=headers,
    )
