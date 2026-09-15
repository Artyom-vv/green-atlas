"""Explicit server roots; no browser-supplied filesystem or converter paths."""

import json
import os
from dataclasses import dataclass
from pathlib import Path

from app.cad_import.policy import ConversionPolicy


@dataclass(frozen=True)
class AllowedCadRoot:
    id: str
    label: str
    path: Path


@dataclass(frozen=True)
class CadIntakeConfig:
    roots: tuple[AllowedCadRoot, ...]
    storage: Path
    converter: Path | None
    policy: ConversionPolicy = ConversionPolicy(timeout_seconds=600)

    @classmethod
    def from_environment(cls, database_path: str) -> "CadIntakeConfig":
        data = json.loads(os.environ.get("GREEN_ATLAS_CAD_ROOTS_JSON", "{}"))
        roots = tuple(
            AllowedCadRoot(
                id=root_id,
                label=item.get("label", root_id),
                path=Path(item["path"]).resolve(),
            )
            for root_id, item in data.items()
        )
        default = Path(database_path).resolve().parent / "cad-intake"
        storage = Path(
            os.environ.get("GREEN_ATLAS_CAD_INTAKE_PATH", str(default))
        ).resolve()
        converter = os.environ.get("GREEN_ATLAS_DWG_CONVERTER")
        for root in roots:
            if storage.is_relative_to(root.path):
                raise ValueError("CAD intake storage must be outside original roots")
        return cls(roots, storage, Path(converter).resolve() if converter else None)

    def root(self, root_id: str) -> AllowedCadRoot:
        for root in self.roots:
            if root.id == root_id:
                return root
        raise ValueError("Каталог CAD не разрешён на сервере")

    def require_enabled(self) -> None:
        if not self.roots or self.converter is None:
            raise ValueError("Приём CAD-комплектов не настроен на сервере")
