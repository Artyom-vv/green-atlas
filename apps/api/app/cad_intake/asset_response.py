"""The response owns the verified descriptor for its complete ASGI lifetime."""

from fastapi.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from app.cad_intake.asset_application import OpenCadAsset
from app.cad_intake.asset_files import asset_chunks


class CadAssetResponse(StreamingResponse):
    def __init__(self, asset: OpenCadAsset) -> None:
        self._stream = asset.stream
        super().__init__(
            asset_chunks(asset.stream),
            media_type="application/dxf",
            headers={
                "ETag": f'"{asset.metadata.asset_sha256}"',
                "Content-Length": str(asset.metadata.asset_bytes),
                "Content-Disposition": f'inline; filename="source-{asset.metadata.asset_sha256}.dxf"',
                "Cache-Control": "private, no-cache",
                "X-Content-Type-Options": "nosniff",
            },
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            # StreamingResponse.background is skipped when send fails. The
            # iterator may not have started yet, so its finally is insufficient.
            self._stream.close()
