"""Разрешённые серверные каталоги CAD; браузер не выбирает произвольный путь или
исполняемый конвертер.
"""

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from app.cad_import.policy import ConversionPolicy

UPLOADED_ROOT = re.compile(r"^upload-[a-f0-9]{32}$")


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
        if UPLOADED_ROOT.fullmatch(root_id):
            uploads = (self.storage / "uploads").resolve()
            try:
                candidate = (uploads / root_id).resolve(strict=True)
                if (
                    candidate.is_dir()
                    and candidate.is_relative_to(uploads)
                    and (candidate / "upload.json").is_file()
                ):
                    return AllowedCadRoot(root_id, "Загруженный комплект", candidate)
            except OSError:
                pass
        raise ValueError("Каталог CAD не разрешён на сервере")
