"""Производный каталог исходных маршрутов без координат для запроса областей.

Он не заменяет проверку CAD-архива. Отсутствующий или повреждённый каталог
возвращает прежнее чтение захвата; публикация возможна только после допуска.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from collections.abc import Callable, Generator
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, NamedTuple

from app.desktop.tickets import read_regular
from app.operations.progress import OperationCancelled, check_operation_cancelled

FACE_INPUT_NAME = "face-inputs.jsonl"
_SCHEMA = "green-atlas.face-inputs/1"
# Ограничение необязательного каталога, а не допуска или открытия исходника.
_MAX_BYTES = 256 * 1024**2
_MAX_LINE_BYTES = 64 * 1024
_LOG = logging.getLogger(__name__)


class FaceInputsUnavailable(ValueError):
    """Производный файл нельзя использовать вместо проверенного захвата."""


class FaceInputRow(NamedTuple):
    route: str
    layer: str
    source_layer: str | None
    authored: bool


def _fingerprint(path: Path) -> list[int]:
    with read_regular(path) as stream:
        value = os.fstat(stream.fileno())
    return [
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    ]


@dataclass
class FaceInputPublication:
    """Результат собственной публикации, передаваемый в атомарную запись проекта."""

    path: Path
    sha256: str | None = None


@dataclass
class StagedFaceInputs:
    temporary: Path
    target: Path
    source_fingerprint: list[int]
    sha256: str

    def discard(self) -> None:
        try:
            self.temporary.unlink(missing_ok=True)
        except OSError:
            _LOG.warning("Не удалось удалить временный каталог CAD", exc_info=True)

    def publish(self) -> str | None:
        """Ошибка необязательной записи не отменяет успешно допущенный импорт."""
        check_operation_cancelled()
        try:
            if self.source_fingerprint != _fingerprint(
                self.target.parent / "Drawing.autocad.json"
            ):
                return None
            if self.target.is_symlink():
                return None
            os.replace(self.temporary, self.target)
            return self.sha256
        except InterruptedError:
            raise
        except (OSError, ValueError):
            _LOG.warning("Не удалось сохранить каталог входов CAD", exc_info=True)
            return None


def stage_face_inputs(
    path: Path, probe: dict[str, Any], source_sha256: str
) -> StagedFaceInputs | None:
    """Прочитать только scalar-поля уже загруженного графа, не сохраняя ссылки на него."""
    temporary: Path | None = None
    staged: StagedFaceInputs | None = None
    try:
        source_fingerprint = _fingerprint(path.parent / "Drawing.autocad.json")
        coverage = probe["coverage"]
        if not isinstance(coverage, list):
            return None
        authored = set()
        for index, row in enumerate(probe.get("regions", ())):
            if index % 256 == 0:
                check_operation_cancelled()
            if row.get("status") == "native" and not row.get("source_handles"):
                authored.add("/".join((*row.get("instance_chain", ()), row["handle"])))
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=".face-input-", dir=path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            digest = sha256()

            def emit(value: object) -> None:
                line = (
                    json.dumps(
                        value,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        allow_nan=False,
                    )
                    + "\n"
                ).encode()
                if (
                    len(line) > _MAX_LINE_BYTES
                    or stream.tell() + len(line) > _MAX_BYTES
                ):
                    raise ValueError("Производный каталог превышает бюджет")
                stream.write(line)
                digest.update(line)

            emit(
                {
                    "schema": _SCHEMA,
                    "source_sha256": source_sha256,
                    "source_fingerprint": source_fingerprint,
                    "rows": len(coverage),
                }
            )
            for index, row in enumerate(coverage):
                if index % 256 == 0:
                    check_operation_cancelled()
                route = "/".join((*row.get("instance_chain", ()), row["handle"]))
                emit([route, row["layer"], row.get("source_layer"), route in authored])
            footer = (json.dumps({"sha256": digest.hexdigest()}) + "\n").encode()
            stream.write(footer)
            digest.update(footer)
        check_operation_cancelled()
        staged = StagedFaceInputs(
            temporary, path, source_fingerprint, digest.hexdigest()
        )
        return staged
    except (OperationCancelled, InterruptedError):
        raise
    except (OSError, KeyError, TypeError, ValueError, AttributeError):
        # Допуск компилятора сохраняет прежний порядок ошибок плохого исходника.
        _LOG.debug("Каталог входов CAD не создан", exc_info=True)
        return None
    finally:
        # Успешный staged-файл принадлежит вызывающему до допуска снимка.
        if temporary is not None and staged is None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                _LOG.warning("Не удалось удалить временный каталог CAD", exc_info=True)


def iter_face_inputs(
    root: Path, source_sha256: str, expected_sha256: str, check: Callable[[], None]
) -> Generator[FaceInputRow, None, None]:
    """Проверить весь каталог до завершения запроса, не удерживая полный список строк."""
    path = root / FACE_INPUT_NAME
    try:
        before = _fingerprint(path)
        if before[2] > _MAX_BYTES:
            raise FaceInputsUnavailable("Слишком большой каталог")
        source_fingerprint = _fingerprint(root / "Drawing.autocad.json")
        with read_regular(path) as stream:
            digest = sha256()

            def read_line() -> bytes:
                check()
                line = stream.readline(_MAX_LINE_BYTES + 1)
                if not line or len(line) > _MAX_LINE_BYTES or not line.endswith(b"\n"):
                    raise FaceInputsUnavailable("Неполная строка каталога")
                return line

            line = read_line()
            header = json.loads(line)
            digest.update(line)
            if (
                not isinstance(header, dict)
                or header.get("schema") != _SCHEMA
                or header.get("source_sha256") != source_sha256
                or header.get("source_fingerprint") != source_fingerprint
                or type(header.get("rows")) is not int
                or header["rows"] < 0
            ):
                raise FaceInputsUnavailable("Каталог относится к другому захвату")
            for _ in range(header["rows"]):
                line = read_line()
                digest.update(line)
                row = json.loads(line)
                if (
                    not isinstance(row, list)
                    or len(row) != 4
                    or not isinstance(row[0], str)
                    or not isinstance(row[1], str)
                    or (row[2] is not None and not isinstance(row[2], str))
                    or type(row[3]) is not bool
                ):
                    raise FaceInputsUnavailable("Неверная строка каталога")
                yield FaceInputRow(*row)
            footer_line = read_line()
            footer = json.loads(footer_line)
            if footer != {"sha256": digest.hexdigest()} or stream.read(1):
                raise FaceInputsUnavailable("Каталог повреждён")
            digest.update(footer_line)
            if digest.hexdigest() != expected_sha256:
                raise FaceInputsUnavailable("Каталог изменён после допуска исходника")
            check()
            if before != _fingerprint(path) or source_fingerprint != _fingerprint(
                root / "Drawing.autocad.json"
            ):
                raise FaceInputsUnavailable("Каталог или захват изменился при чтении")
    except (OperationCancelled, InterruptedError):
        raise
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise FaceInputsUnavailable("Каталог входов CAD недоступен") from error
