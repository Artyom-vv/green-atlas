"""Порт CAD-транспорта для явной подготовки областей и адресного просмотра объектов."""

from typing import Protocol

from app.native_query.contracts import NativeObjectQuery, NativeObjectReply
from app.native_query.live.live_client import LiveSession
from app.native_query.preparation.face_contracts import FacePreparation


class CaptureClient(Protocol):
    def inspect(self, session: LiveSession) -> None: ...

    def prepare_faces(
        self, session: LiveSession, layers: tuple[str, ...]
    ) -> FacePreparation: ...

    def measure(
        self, session: LiveSession, query: NativeObjectQuery
    ) -> NativeObjectReply: ...
