"""Подготовка native-областей один раз для выбранных слоёв проверенного захвата."""

from __future__ import annotations

from app.native_query.capture.capture_client import CaptureClient
from app.native_query.live.live_client import LiveSession
from app.native_query.live.live_inventory import LiveInventory
from app.native_query.preparation.face_contracts import TopologyRecipe


class NativeFacePreparation:
    def __init__(self) -> None:
        self.layers: tuple[tuple[str, ...], TopologyRecipe | None] | None = None

    def prepare(
        self,
        inventory: LiveInventory,
        session: LiveSession,
        client: CaptureClient,
        layers: tuple[str, ...],
    ) -> LiveInventory:
        selection = (layers, getattr(client, "preparation_recipe", None))
        if not inventory.face_preparation_available or self.layers == selection:
            return inventory
        client.inspect(session)
        prepared = client.prepare_faces(session, layers)
        # Перечень уже проверен при чтении захвата. Сохраняем его объекты,
        # повторно проверяя связи новых областей без копирования всего каталога.
        values = {
            field.alias or name: getattr(inventory, name)
            for name, field in LiveInventory.model_fields.items()
        }
        values.update(
            {
                name: getattr(prepared, name)
                for name in type(prepared).model_fields
                if name not in {"session_id", "request_sha256", "layers"}
            }
        )
        result = LiveInventory.model_validate(values)
        self.layers = selection
        return result
