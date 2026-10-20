"""Производная сводка покрытия CAD для повторного открытия паспорта.

Сводку создаёт проверенный расчётный движок. Чтение связывает её с точными
байтами подготовки и текущими назначениями; оно не загружает расчётный движок.
"""

import logging
import os
from collections import OrderedDict
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
from typing import BinaryIO, Literal

from pydantic import Field

from app.desktop.tickets import direct_path, read_regular
from app.geometry.coverage_contracts import GeometryCoverage
from app.native_query.contracts import NativeDto, Sha256
from app.native_query.preparation.preparation_identity import digest, preparation_basis
from app.native_query.preparation.prepared_snapshot import (
    FileFingerprint,
    PreparedGeometryUnavailable,
)
from app.native_query.search.domain_checkpoint import (
    CHECKPOINT_IO_BYTES,
    DomainCheckpointStore,
)
from app.operations.progress import check_operation_cancelled
from app.projects.contracts import Project

# Менять вместе с проверками проекций и правилами подсчёта покрытия.
COVERAGE_CACHE_POLICY = "validated-prepared-coverage/1"
MAX_VERIFIED_COVERAGE_FILES = 2
_LOGGER = logging.getLogger(__name__)


def coverage_identity(project: Project, linear_layers: frozenset[str]) -> str:
    """Счётчики слоёв нужны для inventory_missing сверх основания расчёта."""
    source = project.source_file
    if source is None or source.native_session is None:
        raise ValueError("Сводка покрытия требует native-захват")
    return digest(
        {
            "policy": COVERAGE_CACHE_POLICY,
            "basis": asdict(
                preparation_basis(project, source.native_session, linear_layers)
            ),
            "layers": sorted(
                (layer.source_name, layer.object_count) for layer in project.layers
            ),
        }
    )


class _CoverageArtifact(NativeDto):
    schema_version: Literal["green-atlas.coverage-cache/1"] = (
        "green-atlas.coverage-cache/1"
    )
    identity: Sha256
    source_sha256: Sha256
    prepared_key: Sha256
    prepared_sha256: Sha256
    prepared_bytes: int = Field(gt=0)
    linear_layers: frozenset[str]
    coverage: GeometryCoverage


def _fingerprint(stat: os.stat_result) -> FileFingerprint:
    return stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size


def _path_fingerprint(path: Path) -> FileFingerprint:
    if os.name == "nt":
        from app.native_query.transport.windows_job_files import file_fingerprint

        return file_fingerprint(path)
    return _fingerprint(path.stat())


def _stream_fingerprint(stream: BinaryIO) -> FileFingerprint:
    if os.name == "nt":
        from app.native_query.transport.windows_job_files import stream_fingerprint

        return stream_fingerprint(stream)
    return _fingerprint(os.fstat(stream.fileno()))


class PreparedCoverageCache:
    """Кеш результата проверки отдельно от основания для выполнения расчёта."""

    def __init__(self, prepared_directory: Path) -> None:
        self.directory = prepared_directory.absolute()
        # Подпапка производных данных не входит в резервные копии *.json снимков.
        self.files = DomainCheckpointStore(self.directory / "derived" / "coverage")
        self._verified: OrderedDict[str, FileFingerprint] = OrderedDict()

    def verified_fingerprint(self, key: str) -> FileFingerprint | None:
        """Повторная сводка использует только отпечаток собственных проверенных байтов."""
        previous = self._verified.get(key)
        if previous is None:
            return None
        try:
            path = direct_path(self.directory / f"{key}.json", self.directory)
            current = _path_fingerprint(path)
        except (OSError, ValueError):
            current = None
        if previous != current:
            self._verified.pop(key, None)
            return None
        self._verified.move_to_end(key)
        return previous

    def _remember(self, key: str, fingerprint: FileFingerprint) -> None:
        self._verified[key] = fingerprint
        self._verified.move_to_end(key)
        while len(self._verified) > MAX_VERIFIED_COVERAGE_FILES:
            self._verified.popitem(last=False)

    def _file_digest(
        self, key: str, *, expected: FileFingerprint | None = None
    ) -> tuple[str, FileFingerprint]:
        """Хешировать закреплённый файл и отвергать замену во время чтения."""
        path = direct_path(self.directory / f"{key}.json", self.directory)
        with read_regular(path) as stream:
            before = _stream_fingerprint(stream)
            if expected is not None and before != expected:
                raise PreparedGeometryUnavailable("stale")
            hashed = sha256()
            try:
                while chunk := stream.read(CHECKPOINT_IO_BYTES):
                    check_operation_cancelled()
                    hashed.update(chunk)
            except PermissionError as error:
                # Windows can deny a concurrent replacement while this handle
                # pins the file. The attempted mutation invalidates this read.
                raise PreparedGeometryUnavailable("stale") from error
            check_operation_cancelled()
            try:
                unchanged = before == _stream_fingerprint(stream) == _path_fingerprint(path)
            except (OSError, ValueError) as error:
                raise PreparedGeometryUnavailable("stale") from error
            if not unchanged:
                raise PreparedGeometryUnavailable("stale")
        return hashed.hexdigest(), before

    def read(
        self, project: Project, key: str
    ) -> tuple[GeometryCoverage, FileFingerprint, frozenset[str]] | None:
        """Повреждённый или устаревший кеш возвращает полную проверку исходного файла."""
        try:
            direct_path(self.files.directory / f"{key}.json", self.directory)
            payload = self.files.load_payload(key)
            if payload is None:
                return None
            artifact = _CoverageArtifact.model_validate_json(payload)
        except (OSError, ValueError):
            _LOGGER.warning("Повреждён кеш покрытия; будет выполнена полная проверка")
            return None
        source = project.source_file
        if (
            source is None
            or artifact.prepared_key != key
            or artifact.source_sha256 != source.content_sha256
            or artifact.coverage.source_sha256 != source.content_sha256
            or artifact.coverage.prepared_key != key
            or source.native_session is None
            or preparation_basis(
                project, source.native_session, artifact.linear_layers
            ).geometry
            != key
            or artifact.identity != coverage_identity(project, artifact.linear_layers)
        ):
            return None
        # Прежний формат проходит load_project со всеми условиями совместимости.
        if not (self.directory / f"{key}.json").exists():
            return None
        try:
            content_sha, fingerprint = self._file_digest(key)
        except PreparedGeometryUnavailable:
            raise
        except (OSError, ValueError) as error:
            raise PreparedGeometryUnavailable("corrupt") from error
        if (content_sha, fingerprint[-1]) != (
            artifact.prepared_sha256,
            artifact.prepared_bytes,
        ):
            self._verified.pop(key, None)
            return None
        if artifact.identity != coverage_identity(project, artifact.linear_layers):
            raise PreparedGeometryUnavailable("stale")
        self._remember(key, fingerprint)
        return (
            artifact.coverage.model_copy(deep=True),
            fingerprint,
            artifact.linear_layers,
        )

    def save(
        self,
        project: Project,
        key: str,
        coverage: GeometryCoverage,
        linear_layers: frozenset[str],
        fingerprint: FileFingerprint,
        identity: str,
    ) -> None:
        """Сохранить уже проверенную сводку; ошибка записи кеша не отменяет подготовку."""
        if not (self.directory / f"{key}.json").exists():
            return
        try:
            content_sha, observed = self._file_digest(key, expected=fingerprint)
        except PreparedGeometryUnavailable:
            raise
        except (OSError, ValueError) as error:
            raise PreparedGeometryUnavailable("corrupt") from error
        if identity != coverage_identity(project, linear_layers):
            raise PreparedGeometryUnavailable("stale")
        artifact = _CoverageArtifact(
            identity=identity,
            source_sha256=coverage.source_sha256,
            prepared_key=key,
            prepared_sha256=content_sha,
            prepared_bytes=observed[-1],
            linear_layers=linear_layers,
            coverage=coverage,
        )
        check_operation_cancelled()
        try:
            direct_path(self.files.directory / f"{key}.json", self.directory)
            self.files.save(key, artifact.model_dump(mode="json"))
        except (OSError, ValueError):
            _LOGGER.warning("Не удалось сохранить кеш покрытия", exc_info=True)
            return
        check_operation_cancelled()
        try:
            current = _path_fingerprint(self.directory / f"{key}.json")
        except (OSError, ValueError) as error:
            raise PreparedGeometryUnavailable("stale") from error
        if observed != current:
            raise PreparedGeometryUnavailable("stale")
        self._remember(key, observed)
