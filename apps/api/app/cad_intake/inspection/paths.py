"""Разрешение выбранного CAD-пути внутри серверного каталога и стабильное имя соседнего
снимка без поиска совпадений.
"""

from pathlib import Path

from app.cad_intake.contracts import relative_path
from app.cad_intake.inspection.config import CadIntakeConfig

DRAWING_EXTENSIONS = {".dxf", ".dwg"}


def adjacent_cad_snapshot_path(drawing_path: str) -> str:
    """Устойчивое имя результата bridge без поиска каталога и эвристического
    сопоставления.
    """

    return f"{drawing_path}.green-atlas.snapshot.json"


class CadDiscovery:
    def __init__(self, config: CadIntakeConfig) -> None:
        self.config = config

    def resolve(self, root_id: str, path: str, *, drawing: bool = False) -> Path:
        root = self.config.root(root_id)
        safe = relative_path(path)
        try:
            resolved = (root.path / safe).resolve(strict=True)
            if not resolved.is_relative_to(root.path):
                raise ValueError("Путь выходит за пределы разрешённого каталога")
            if drawing and (
                not resolved.is_file()
                or resolved.suffix.lower() not in DRAWING_EXTENSIONS
            ):
                raise ValueError("Выберите чертёж DWG или DXF")
            return resolved
        except OSError as error:
            raise ValueError("Путь недоступен в разрешённом каталоге") from error
