"""Private, capture-bound checkpoints; never a replacement for native identity checks."""

import json
import os
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile


class DomainCheckpointStore:
    def __init__(self, directory: Path):
        self.directory = directory

    def load(self, key: str) -> dict | None:
        path = self.directory / f"{key}.json"
        if not path.exists():
            return None
        try:
            envelope = json.loads(path.read_text())
            payload = envelope["payload"]
            if sha256(payload.encode()).hexdigest() != envelope["sha256"]:
                raise ValueError("checksum")
            return json.loads(payload)
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ValueError(
                "Не удалось прочитать сохранённый ход проверки области"
            ) from error

    def save(self, key: str, value: dict) -> None:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        payload = json.dumps(value, separators=(",", ":"), allow_nan=False)
        envelope = {"payload": payload, "sha256": sha256(payload.encode()).hexdigest()}
        temporary = None
        try:
            with NamedTemporaryFile(
                mode="w", dir=self.directory, suffix=".pending", delete=False
            ) as stream:
                temporary = Path(stream.name)
                json.dump(envelope, stream)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.directory / f"{key}.json")
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
