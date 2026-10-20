"""Хранилище проекта: неизменяемые FeatureCollection вне размера строки SQLite.

Публичные GeometrySnapshot остаются прежними. Приватные ссылки связывают SHA,
размеры и число объектов; gzip и ijson читают граф без большой JSON-строки.
"""

from __future__ import annotations

import gzip
import json
import os
from collections import Counter, OrderedDict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock
from typing import Any, BinaryIO, Literal, cast, overload

import ijson
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app.desktop.tickets import direct_path, read_regular
from app.operations.progress import check_operation_cancelled
from app.projects.backup_files import BackupFile, sync_directory
from app.projects.contracts import Project
from app.projects.geometry_stream import consume_collection
from app.projects.ports import GeometryFeatureConsumer, GeometryResult

GEOMETRY_STORAGE_KEY = "_geometry_storage"
GEOMETRY_FIELDS = ("geometry", "source_geometry")
GEOMETRY_COPY_CHUNK_BYTES = 1024 * 1024
GEOMETRY_COMPRESSION_LEVEL = 1
MAX_VERIFIED_GEOMETRY_REFERENCES = 64
EMPTY_FEATURE_COLLECTION = {"type": "FeatureCollection", "features": []}
_JSON_VALUE: TypeAdapter[Any] = TypeAdapter(Any)
FileFingerprint = tuple[int, int, int, int, int]
VerificationKey = tuple[str, FileFingerprint]


class GeometryReference(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, strict=True)
    schema_version: Literal["green-atlas.project-geometry/1"] = Field(alias="schema")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    stored_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bytes: int = Field(gt=0)
    stored_bytes: int = Field(gt=0)
    features: int = Field(ge=0)


def geometry_references(payload: dict) -> dict[str, GeometryReference]:
    values = payload.get(GEOMETRY_STORAGE_KEY, {})
    if not isinstance(values, dict) or not set(values) <= set(GEOMETRY_FIELDS):
        raise ValueError("Некорректные ссылки сохранённой геометрии")
    references = {}
    for name, value in values.items():
        graph = payload.get(name)
        if (
            not isinstance(graph, dict)
            or graph.get("feature_collection") != EMPTY_FEATURE_COLLECTION
        ):
            raise ValueError("Ссылка геометрии конфликтует с данными проекта")
        references[name] = GeometryReference.model_validate(value)
    return references


class _HashedReader:
    """ijson получает ограниченные блоки и тот же поток, на котором считается SHA."""

    def __init__(self, stream: BinaryIO, reference: GeometryReference) -> None:
        self.stream, self.reference = stream, reference
        self.digest, self.size = sha256(), 0

    def read(self, size: int = -1) -> bytes:
        check_operation_cancelled()
        if size < 0:
            size = GEOMETRY_COPY_CHUNK_BYTES
        data = self.stream.read(size)
        self.size += len(data)
        if self.size > self.reference.bytes:
            raise ValueError("Превышен размер сохранённой геометрии")
        self.digest.update(data)
        return data

    def verify(self) -> None:
        if (self.size, self.digest.hexdigest()) != (
            self.reference.bytes,
            self.reference.sha256,
        ):
            raise ValueError("Нарушена целостность сохранённой геометрии")


@dataclass
class GeometryStage:
    store: GeometryStore
    project_id: str
    references: dict[str, GeometryReference] = field(default_factory=dict)
    temporary: dict[str, Path] = field(default_factory=dict)
    published: set[str] = field(default_factory=set)

    def publish(self) -> None:
        """Вызывается под SQLite writer lock; другой CAS не теряет используемый файл."""
        for digest, path in self.temporary.items():
            reference = next(
                r for r in self.references.values() if r.stored_sha256 == digest
            )
            destination = self.store.path(self.project_id, reference)
            try:
                os.link(path, destination)
                self.published.add(digest)
                sync_directory(destination.parent)
            except FileExistsError:
                with self.store.open(self.project_id, reference) as stream:
                    self.store.verify_stored(stream, reference)

    def discard(self, retained: set[str]) -> None:
        for digest in self.published - retained:
            reference = next(
                r for r in self.references.values() if r.stored_sha256 == digest
            )
            self.store.path(self.project_id, reference).unlink(missing_ok=True)

    def close(self) -> None:
        for path in self.temporary.values():
            path.unlink(missing_ok=True)


class _VerificationConsumer:
    """Проверять полный поток, не удерживая полученные объекты карты."""

    def accept(self, feature: Any) -> None:
        pass

    def finish(self) -> None:
        pass


class _KindPresenceConsumer:
    """Один объект живёт только во время проверки; весь поток проверяется до EOF."""

    def __init__(
        self,
        kind: str,
        require_geometry: bool,
        roles: Callable[[list[dict]], None] | None,
    ) -> None:
        self.kind, self.require_geometry, self.roles = kind, require_geometry, roles
        self.found = False

    def accept(self, feature: Any) -> None:
        if not isinstance(feature, dict):
            return
        if self.roles is not None:
            self.roles([feature])
        if feature.get("properties", {}).get("kind") == self.kind and (
            not self.require_geometry or bool(feature.get("geometry"))
        ):
            self.found = True

    def finish(self) -> bool:
        return self.found


class GeometryStore:
    def __init__(self, root: Path) -> None:
        self.root = root.absolute()
        self._verified: OrderedDict[VerificationKey, None] = OrderedDict()
        self._verification_lock = RLock()
        self._display_counts: OrderedDict[
            tuple[VerificationKey, tuple[str, ...]], dict[str, int]
        ] = OrderedDict()
        self._kind_presence: OrderedDict[
            tuple[VerificationKey, str, bool, str], bool
        ] = OrderedDict()

    @staticmethod
    def _verification_key(
        stream: BinaryIO, reference: GeometryReference
    ) -> VerificationKey:
        observed = os.fstat(stream.fileno())
        if observed.st_size != reference.stored_bytes:
            raise ValueError("Размер файла геометрии не соответствует проекту")
        return reference.model_dump_json(by_alias=True), (
            observed.st_dev,
            observed.st_ino,
            observed.st_size,
            observed.st_mtime_ns,
            observed.st_ctime_ns,
        )

    def _remember_verified(
        self, stream: BinaryIO, reference: GeometryReference, before: VerificationKey
    ) -> None:
        self._assert_unchanged(stream, reference, before)
        with self._verification_lock:
            self._verified[before] = None
            self._verified.move_to_end(before)
            while len(self._verified) > MAX_VERIFIED_GEOMETRY_REFERENCES:
                self._verified.popitem(last=False)

    def _assert_unchanged(
        self, stream: BinaryIO, reference: GeometryReference, before: VerificationKey
    ) -> None:
        check_operation_cancelled()
        if self._verification_key(stream, reference) != before:
            raise ValueError("Сохранённая геометрия изменилась во время проверки")
        stream.seek(0)

    def verify_reference(self, stream: BinaryIO, reference: GeometryReference) -> None:
        """Переиспользовать только завершённую проверку тех же байтов и перечня.

        Холодное чтение сохраняет SHA сжатых и исходных байтов, размеры, JSON,
        EOF и число объектов. Квитанция хранит только ссылку и отпечаток FD.
        Повторное обращение проверяет SHA сжатых байтов без разбора JSON.
        """
        check_operation_cancelled()
        before = self._verification_key(stream, reference)
        with self._verification_lock:
            hit = before in self._verified
        if hit:
            # Windows может сохранять ctime как время создания. Даже совпавший
            # отпечаток не заменяет SHA точных сжатых байтов текущего файла.
            self.verify_stored(stream, reference)
            self._remember_verified(stream, reference, before)
            return
        self.consume_features(stream, reference, _VerificationConsumer)

    def directory(self, project_id: str) -> Path:
        # Старые Project допускают строковый id; хеш ограничивает путь и сохраняет
        # их идентичность без требования миграции в UUID.
        return direct_path(
            self.root / sha256(project_id.encode()).hexdigest(), self.root
        )

    def path(self, project_id: str, reference: GeometryReference) -> Path:
        return direct_path(
            self.directory(project_id) / f"{reference.stored_sha256}.json.gz", self.root
        )

    @contextmanager
    def stage(self, project: Project) -> Iterator[GeometryStage]:
        staged = GeometryStage(self, project.id)
        try:
            for name in GEOMETRY_FIELDS:
                graph = getattr(project, name)
                if graph is None:
                    continue
                reference, path = self._stage_collection(
                    project.id, graph.feature_collection
                )
                staged.references[name] = reference
                previous = staged.temporary.setdefault(reference.stored_sha256, path)
                if previous != path:
                    path.unlink()
            yield staged
        finally:
            staged.close()

    def _stage_collection(
        self, project_id: str, collection: dict
    ) -> tuple[GeometryReference, Path]:
        directory = self.directory(project_id)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        features = collection.get("features", [])
        if not isinstance(features, list):
            raise ValueError("Объекты геометрии должны быть списком")
        digest, count = sha256(), 0
        path = None
        try:
            with NamedTemporaryFile(
                dir=directory, suffix=".pending", delete=False
            ) as output:
                path = Path(output.name)
                with gzip.GzipFile(
                    fileobj=output,
                    mode="wb",
                    filename="",
                    mtime=0,
                    compresslevel=GEOMETRY_COMPRESSION_LEVEL,
                ) as compressed:

                    def write(data: bytes) -> None:
                        nonlocal count
                        digest.update(data)
                        count += len(data)
                        compressed.write(data)

                    write(b"{")
                    for index, (key, value) in enumerate(collection.items()):
                        if index:
                            write(b",")
                        write(_JSON_VALUE.dump_json(key) + b":")
                        if key != "features":
                            write(_JSON_VALUE.dump_json(value))
                            continue
                        write(b"[")
                        for feature_index, feature in enumerate(features):
                            if feature_index:
                                write(b",")
                            write(_JSON_VALUE.dump_json(feature))
                        write(b"]")
                    write(b"}")
                output.flush()
                os.fsync(output.fileno())
            with read_regular(path) as stream:
                stored_digest = self._digest(stream)
            return GeometryReference(
                schema_version="green-atlas.project-geometry/1",
                sha256=digest.hexdigest(),
                bytes=count,
                stored_sha256=stored_digest,
                stored_bytes=path.stat().st_size,
                features=len(features),
            ), path
        except Exception:
            if path is not None:
                path.unlink(missing_ok=True)
            raise

    @staticmethod
    def _digest(stream: BinaryIO) -> str:
        digest = sha256()
        for chunk in iter(lambda: stream.read(GEOMETRY_COPY_CHUNK_BYTES), b""):
            check_operation_cancelled()
            digest.update(chunk)
        return digest.hexdigest()

    @contextmanager
    def open(self, project_id: str, reference: GeometryReference) -> Iterator[BinaryIO]:
        with read_regular(self.path(project_id, reference)) as stream:
            if os.fstat(stream.fileno()).st_size != reference.stored_bytes:
                raise ValueError("Размер файла геометрии не соответствует проекту")
            yield stream

    def verify_stored(self, stream: BinaryIO, reference: GeometryReference) -> None:
        if self._digest(stream) != reference.stored_sha256:
            raise ValueError("Нарушена целостность файла геометрии")
        stream.seek(0)

    def verify_pinned(
        self, project_id: str, stream: BinaryIO, reference: GeometryReference
    ) -> None:
        """Проверить закреплённые байты и неизменность пути перед выдачей окна."""
        before = self._verification_key(stream, reference)
        self.verify_stored(stream, reference)
        if self._verification_key(stream, reference) != before:
            raise ValueError("Сохранённая геометрия изменилась во время чтения")
        with self.open(project_id, reference) as reopened:
            if self._verification_key(reopened, reference) != before:
                raise ValueError("Сохранённая геометрия заменена во время чтения")

    @overload
    def read(
        self, stream: BinaryIO, reference: GeometryReference, layers: None = None
    ) -> dict: ...

    @overload
    def read(
        self, stream: BinaryIO, reference: GeometryReference, layers: set[str]
    ) -> list[dict]: ...

    def read(
        self,
        stream: BinaryIO,
        reference: GeometryReference,
        layers: set[str] | None = None,
    ) -> dict | list[dict]:
        before = self._verification_key(stream, reference)
        self.verify_stored(stream, reference)
        try:
            result = self._read_collection(
                stream, reference, layers, native_numbers=True
            )
        except ijson.JSONError as error:
            if str(error).splitlines()[0] not in {
                "parse error: integer overflow",
                "parse error: numeric (floating point) overflow",
            }:
                raise
        else:
            if layers is None:
                self._remember_verified(stream, reference, before)
            return result
        # Освободить traceback и частичный граф до повторного чтения. JSON Any
        # допускает большие целые; прежний потоковый режим сохраняет их точно.
        stream.seek(0)
        result = self._read_collection(stream, reference, layers, native_numbers=False)
        if layers is None:
            self._remember_verified(stream, reference, before)
        return result

    def _read_collection(
        self,
        stream: BinaryIO,
        reference: GeometryReference,
        layers: set[str] | None,
        *,
        native_numbers: bool,
        display_counts_kinds: set[str] | None = None,
    ) -> dict | list[dict]:
        with gzip.GzipFile(fileobj=stream, mode="rb") as compressed:
            reader = _HashedReader(cast(BinaryIO, compressed), reference)
            # Обычная CAD-геометрия читается готовым C-парсером без Python-обхода
            # каждого числа. Редкие значения вне его диапазона используют тот же
            # проверенный JSON с преобразованием только дробных числовых событий.
            source = (
                cast(BinaryIO, reader)
                if native_numbers
                else cast(
                    BinaryIO,
                    (
                        (
                            prefix,
                            event,
                            float(value) if isinstance(value, Decimal) else value,
                        )
                        for prefix, event, value in ijson.parse(cast(BinaryIO, reader))
                    ),
                )
            )
            counts: Counter[str] | None = (
                Counter() if display_counts_kinds is not None else None
            )
            if layers is None and counts is None:
                values = ijson.items(source, "", use_float=native_numbers)
                result = next(values)
                if next(values, None) is not None or not isinstance(result, dict):
                    raise ValueError("Некорректная сохранённая геометрия")
                features = result.get("features", [])
                if (
                    not isinstance(features, list)
                    or len(features) != reference.features
                ):
                    raise ValueError(
                        "Перечень объектов геометрии не соответствует проекту"
                    )
            else:
                result, count = [], 0
                for feature in ijson.items(
                    source, "features.item", use_float=native_numbers
                ):
                    count += 1
                    if counts is not None:
                        assert display_counts_kinds is not None
                        if not isinstance(feature, dict):
                            continue
                        properties = feature.get("properties", {})
                        layer = str(properties.get("source_layer", "")).strip()
                        if (
                            layer
                            and str(properties.get("kind", "")) in display_counts_kinds
                            and not properties.get("source_context_only")
                        ):
                            counts[layer] += 1
                    elif (
                        layers is not None
                        and feature.get("properties", {}).get("source_layer") in layers
                    ):
                        result.append(feature)
                if count != reference.features:
                    raise ValueError(
                        "Перечень объектов геометрии не соответствует проекту"
                    )
            reader.verify()
        return dict(counts) if counts is not None else result

    def read_display_counts(
        self, stream: BinaryIO, reference: GeometryReference, kinds: set[str]
    ) -> dict[str, int]:
        """Переиспользовать завершённую сводку тех же проверенных байтов.

        Даже попадание в кеш проверяет SHA сжатого файла. Полный JSON, EOF,
        размеры и перечень проверяются до публикации первой сводки.
        """
        before = self._verification_key(stream, reference)
        key = before, tuple(sorted(kinds))
        with self._verification_lock:
            cached = self._display_counts.get(key)
            if cached is not None:
                self._display_counts.move_to_end(key)
        if cached is not None:
            self.verify_stored(stream, reference)
            self._assert_unchanged(stream, reference, before)
            return dict(cached)
        result = self._read_display_counts(stream, reference, kinds)
        # Подсчёт features.item не доказывает форму последнего одноимённого
        # поля. Кеш сводки не является квитанцией полной проверки коллекции.
        self._assert_unchanged(stream, reference, before)
        with self._verification_lock:
            self._display_counts[key] = dict(result)
            self._display_counts.move_to_end(key)
            while len(self._display_counts) > MAX_VERIFIED_GEOMETRY_REFERENCES:
                self._display_counts.popitem(last=False)
        return result

    def read_kind_presence(
        self,
        stream: BinaryIO,
        reference: GeometryReference,
        kind: str,
        *,
        require_geometry: bool = False,
        roles: Callable[[list[dict]], None] | None = None,
        roles_identity: str = "",
    ) -> bool:
        """Точное наличие класса, включая фон и объекты без исходного слоя."""
        before = self._verification_key(stream, reference)
        key = before, kind, require_geometry, roles_identity
        with self._verification_lock:
            cached = self._kind_presence.get(key)
            if cached is not None:
                self._kind_presence.move_to_end(key)
        if cached is not None:
            self.verify_stored(stream, reference)
            self._remember_verified(stream, reference, before)
            return cached
        result = self.consume_features(
            stream,
            reference,
            lambda: _KindPresenceConsumer(kind, require_geometry, roles),
        )
        self._remember_verified(stream, reference, before)
        with self._verification_lock:
            self._kind_presence[key] = result
            self._kind_presence.move_to_end(key)
            while len(self._kind_presence) > MAX_VERIFIED_GEOMETRY_REFERENCES:
                self._kind_presence.popitem(last=False)
        return result

    def _read_display_counts(
        self, stream: BinaryIO, reference: GeometryReference, kinds: set[str]
    ) -> dict[str, int]:
        self.verify_stored(stream, reference)
        self._check_collection_header(stream, reference)
        try:
            result = self._read_collection(
                stream, reference, None, native_numbers=True, display_counts_kinds=kinds
            )
        except ijson.JSONError as error:
            if str(error).splitlines()[0] not in {
                "parse error: integer overflow",
                "parse error: numeric (floating point) overflow",
            }:
                raise
        else:
            return cast(dict[str, int], result)
        stream.seek(0)
        result = self._read_collection(
            stream,
            reference,
            None,
            native_numbers=False,
            display_counts_kinds=kinds,
        )
        return cast(dict[str, int], result)

    def consume_features(
        self,
        stream: BinaryIO,
        reference: GeometryReference,
        factory: Callable[[], GeometryFeatureConsumer[GeometryResult]],
    ) -> GeometryResult:
        """Построить производное состояние без полного словаря координат в памяти."""
        before = self._verification_key(stream, reference)
        self.verify_stored(stream, reference)
        try:
            result = self._consume_features(
                stream, reference, factory, native_numbers=True
            )
        except ijson.JSONError as error:
            if str(error).splitlines()[0] not in {
                "parse error: integer overflow",
                "parse error: numeric (floating point) overflow",
            }:
                raise
        else:
            self._remember_verified(stream, reference, before)
            return result
        # За пределами except частичный потребитель и traceback уже освобождены.
        stream.seek(0)
        result = self._consume_features(
            stream, reference, factory, native_numbers=False
        )
        self._remember_verified(stream, reference, before)
        return result

    @staticmethod
    def _consume_features(
        stream: BinaryIO,
        reference: GeometryReference,
        factory: Callable[[], GeometryFeatureConsumer[GeometryResult]],
        *,
        native_numbers: bool,
    ) -> GeometryResult:
        with gzip.GzipFile(fileobj=stream, mode="rb") as compressed:
            reader = _HashedReader(cast(BinaryIO, compressed), reference)
            consumer = consume_collection(
                cast(BinaryIO, reader),
                reference.features,
                factory,
                native_numbers=native_numbers,
            )
            reader.verify()
        check_operation_cancelled()
        return consumer.finish()

    @staticmethod
    def _check_collection_header(
        stream: BinaryIO, reference: GeometryReference
    ) -> None:
        """Проверить форму до массива без обхода координат каждого объекта."""
        try:
            with gzip.GzipFile(fileobj=stream, mode="rb") as compressed:
                reader = _HashedReader(cast(BinaryIO, compressed), reference)
                events = ijson.parse(cast(BinaryIO, reader))
                if next(events) != ("", "start_map", None):
                    raise ValueError("Некорректная сохранённая геометрия")
                for prefix, event, _ in events:
                    if prefix == "features":
                        if event != "start_array":
                            raise ValueError("Объекты геометрии должны быть списком")
                        break
        finally:
            stream.seek(0)

    def discard_project(self, project_id: str) -> None:
        directory = self.directory(project_id)
        if directory.exists():
            # Незавершённый staging принадлежит своей операции. Delete не удаляет
            # её .pending; поздний save проверит CAS и завершит собственную уборку.
            for path in directory.glob("*.json.gz"):
                path.unlink()
            try:
                directory.rmdir()
            except OSError:
                if not any(directory.iterdir()):
                    raise

    def backup(
        self, project_id: str, references: dict[str, GeometryReference], staged: Path
    ) -> list[BackupFile]:
        from app.projects.backup_files import store_backup_file

        files = []
        for reference in {r.stored_sha256: r for r in references.values()}.values():
            with self.open(project_id, reference) as stream:
                self.read(stream, reference, set())
                relative = (
                    Path(self.root.name)
                    / self.directory(project_id).name
                    / self.path(project_id, reference).name
                )
                record = store_backup_file(
                    self.path(project_id, reference), staged, relative, compress=False
                )
                if (record["bytes"], record["sha256"]) != (
                    reference.stored_bytes,
                    reference.stored_sha256,
                ):
                    raise ValueError(
                        "Геометрия изменилась во время резервного копирования"
                    )
                files.append(record)
        return files


def stored_project_payload(
    project: Project, references: dict[str, GeometryReference]
) -> str:
    value = project.model_dump(mode="json", exclude=set(GEOMETRY_FIELDS))
    for name in GEOMETRY_FIELDS:
        graph = getattr(project, name)
        value[name] = (
            graph.model_dump(mode="json", exclude={"feature_collection"})
            if graph is not None
            else None
        )
        if graph is not None:
            value[name]["feature_collection"] = EMPTY_FEATURE_COLLECTION
    if references:
        value[GEOMETRY_STORAGE_KEY] = {
            name: ref.model_dump(mode="json", by_alias=True)
            for name, ref in references.items()
        }
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
