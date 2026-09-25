"""Prepare native faces once per layer selection of a checked capture."""

from app.native_query.live_inventory import LiveInventory


class NativeFacePreparation:
    def __init__(self):
        self.layers = None

    def prepare(self, inventory, session, client, layers):
        if not inventory.face_preparation_available or self.layers == layers:
            return inventory
        client.inspect(session)
        prepared = client.prepare_faces(session, layers)
        values = inventory.model_dump(by_alias=True)
        values.update(
            prepared.model_dump(
                by_alias=True,
                exclude={"session_id", "request_sha256", "layers"},
            )
        )
        result = LiveInventory.model_validate(values)
        self.layers = layers  # A failed validation must remain retryable.
        return result
