"""Резервирование неизменяемых CAD-входов, на которые ссылается копия базы; временные
задания не переносятся.
"""

import json
import re
from hashlib import sha256
from pathlib import Path
from uuid import UUID

from app.projects.backup_files import BackupFile, file_digest, store_backup_file


def backup_archives(
    database: Path, staged: Path, references: list[tuple[str, str | None, bool]]
) -> list[BackupFile]:
    root = database.with_name(database.name + ".cad-archives")
    if root.is_symlink():
        raise ValueError("Каталог CAD-архивов является ссылкой")
    files = []
    for project_id, snapshot_sha, required in references:
        if snapshot_sha is None:
            if required:
                raise ValueError(
                    "Не указана идентичность сохранённого native-источника"
                )
            continue
        UUID(project_id)
        if re.fullmatch(r"[0-9a-f]{64}", snapshot_sha) is None:
            raise ValueError("Некорректное основание CAD-архива в проекте")
        package = root / project_id / snapshot_sha
        if any(path.is_symlink() for path in (package.parent, package)):
            raise ValueError("CAD-архив содержит ссылку")
        if not package.exists():
            if required:
                raise ValueError("Отсутствует единственный сохранённый native-источник")
            continue  # Старые проекты не гарантировали наличие сохранённого CAD-архива.
        marker = package / "binding.json"
        if marker.is_symlink() or marker.stat().st_size > 1024 * 1024:
            raise ValueError("Некорректная привязка CAD-архива")
        marker_bytes = marker.read_bytes()
        binding = json.loads(marker_bytes)
        if (
            binding.get("project_id") != project_id
            or binding.get("display_sha256") != snapshot_sha
        ):
            raise ValueError("CAD-архив относится к другому проекту")
        if required and not any(
            item["name"] == "Drawing.autocad.json" and item["sha256"] == snapshot_sha
            for item in binding["transfer"]["files"]
        ):
            raise ValueError("CAD-архив не содержит сохранённый native-источник")
        relative_root = Path(root.name) / project_id / snapshot_sha
        marker_record = store_backup_file(marker, staged, relative_root / marker.name)
        if (
            marker_record["sha256"] != sha256(marker_bytes).hexdigest()
            or file_digest(marker) != marker_record["sha256"]
        ):
            raise ValueError(
                "Привязка CAD-архива изменена во время резервного копирования"
            )
        files.append(marker_record)
        visited = set()
        for item in binding["transfer"]["files"]:
            name = Path(item["name"]).name
            if (
                name in visited
                or re.fullmatch(
                    r"(session|instances|semantics)\.json|(host|xref-[1-9][0-9]*)\.dwg|Drawing\.autocad\.json(\.inventory\.json)?",
                    name,
                )
                is None
            ):
                raise ValueError("Некорректный каталог CAD-архива")
            visited.add(name)
            source = package / name
            if source.is_symlink() or not source.is_file():
                raise ValueError("CAD-архив не является обычным файлом")
            record = store_backup_file(source, staged, relative_root / name)
            if (record["bytes"], record["sha256"]) != (
                item["bytes"],
                item["sha256"],
            ) or file_digest(source) != item["sha256"]:
                raise ValueError("CAD-архив изменён во время резервного копирования")
            files.append(record)
    return files
