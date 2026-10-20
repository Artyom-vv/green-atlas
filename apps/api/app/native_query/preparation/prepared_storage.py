"""Производное расчётное SQLite/WKB-хранилище одного проверенного снимка.

Исходный SHA-конверт остаётся авторитетным. Миграция читает по одной записи,
а последующие проверки получают только метаданные и WKB выбранного окна.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import struct
from collections import OrderedDict
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from math import copysign, inf, isfinite
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock, local
from typing import Any, BinaryIO, overload

import ijson
from ijson.common import ObjectBuilder
from pydantic import TypeAdapter
from shapely import from_wkb
from shapely.geometry import LineString, shape

from app.desktop.tickets import direct_path, read_regular
from app.native_query.live.live_inventory import QueryObject
from app.native_query.policy.source_participation import SourceUseProof
from app.native_query.preparation.face_contracts import NativeFace, TopologyRecipe
from app.native_query.preparation.preparation_identity import (
    LEGACY_PREPARED_REVISION,
    PREPARED_REVISION,
)
from app.native_query.preparation.prepared_snapshot import (
    PREPARED_PROJECTION_CACHE_BYTES,
    PREPARED_PROJECTION_CACHE_ITEMS,
    PreparedGeometryUnavailable,
    PreparedObject,
    PreparedSnapshot,
    _confirmed_reference,
)
from app.native_query.search.hybrid_geometry import Projection
from app.operations.progress import cancellable_lock, check_operation_cancelled
from app.projects.backup_files import sync_directory
from app.projects.resource_lifecycle import ProjectResourceLifecycle
from app.projects.resource_scope import (
    project_resource_scope,
    register_project_resource,
)

PREPARED_SQLITE_FORMAT = "green-atlas.prepared-sqlite/1"
PREPARED_SQLITE_BUILDER = "prepared-projection-wkb-rtree/1"
_OBJECT = TypeAdapter(PreparedObject)
_JSON: TypeAdapter[Any] = TypeAdapter(Any)
_KEY = re.compile(r"[0-9a-f]{64}\Z")
_CHUNK = 1024 * 1024
_BATCH = 128
_SUPPORTED_TYPES = {"Polygon", "MultiPolygon", "LineString", "MultiLineString", "Point"}
Fingerprint = tuple[int, int, int, int, int]
ObjectKey = tuple[tuple[str, ...], int]


def _fingerprint(stream: BinaryIO) -> Fingerprint:
    if os.name == "nt":
        from app.native_query.transport.windows_job_files import stream_fingerprint

        return stream_fingerprint(stream)
    value = os.fstat(stream.fileno())
    return (
        value.st_dev,
        value.st_ino,
        value.st_mtime_ns,
        value.st_ctime_ns,
        value.st_size,
    )


def _digest(stream: BinaryIO) -> str:
    stream.seek(0)
    result = sha256()
    while data := stream.read(_CHUNK):
        check_operation_cancelled()
        result.update(data)
    stream.seek(0)
    return result.hexdigest()


def _address(item: QueryObject) -> bytes:
    return _JSON.dump_json([item.routes, item.face_id])


def _numbers(value: Any) -> Any:
    # Decimal avoids ijson's integer overflow for arbitrary foreign members.
    # The prepared JSON codec uses Python binary64 for real JSON numbers.
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, list):
        return [_numbers(item) for item in value]
    if isinstance(value, dict):
        return {key: _numbers(item) for key, item in value.items()}
    return value


def _value(events: Iterator[tuple], first: tuple) -> Any:
    builder, depth, processed = ObjectBuilder(), 0, 0
    current = first
    while True:
        if processed % 4096 == 0:
            check_operation_cancelled()
        processed += 1
        _, event, value = current
        builder.event(event, value)
        depth += (event in {"start_map", "start_array"}) - (
            event in {"end_map", "end_array"}
        )
        if depth == 0:
            return _numbers(builder.value)
        current = next(events)


def _rtree_bound(value: float, *, lower: bool) -> float:
    """Округлять наружу, включая субнормальные и большие native-координаты."""
    try:
        rounded = struct.unpack("!f", struct.pack("!f", value))[0]
    except OverflowError:
        rounded = copysign(inf, value)
    if (lower and rounded > value) or (not lower and rounded < value):
        direction = -1 if lower else 1
        if rounded == 0:
            return copysign(struct.unpack("!f", b"\x00\x00\x00\x01")[0], direction)
        bits = struct.unpack("!I", struct.pack("!f", rounded))[0]
        bits += 1 if (direction > 0) == (rounded > 0) else -1
        rounded = struct.unpack("!f", struct.pack("!I", bits))[0]
    return rounded


@dataclass(frozen=True)
class _Registration:
    key: str
    source_sha256: str
    payload_sha256: str
    sha256: str
    bytes: int

    @classmethod
    def parse(cls, value: dict) -> _Registration:
        if set(value) != {"key", "source_sha256", "payload_sha256", "sha256", "bytes"}:
            raise ValueError("Некорректная регистрация расчётного ресурса")
        result = cls(**value)
        if (
            any(
                not isinstance(item, str) or not _KEY.fullmatch(item)
                for item in (
                    result.key,
                    result.source_sha256,
                    result.payload_sha256,
                    result.sha256,
                )
            )
            or type(result.bytes) is not int
            or result.bytes <= 0
        ):
            raise ValueError("Некорректная регистрация расчётного ресурса")
        return result


class _Builder:
    def __init__(self, path: Path) -> None:
        self.connection = sqlite3.connect(path)
        self.records = self.faces = 0
        self.connection.executescript(
            "PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA cache_size=-4096;"
            "CREATE TABLE manifest(format TEXT,builder TEXT,key TEXT,source_sha256 TEXT,"
            "payload_sha256 TEXT,header BLOB,records INTEGER,faces INTEGER);"
            "CREATE TABLE records(ordinal INTEGER PRIMARY KEY,address BLOB NOT NULL UNIQUE,"
            "metadata BLOB NOT NULL,source_layer TEXT NOT NULL,projection INTEGER NOT NULL,"
            "minx REAL,miny REAL,maxx REAL,maxy REAL);"
            "CREATE INDEX source_layer ON records(source_layer);"
            "CREATE INDEX unknown_bounds ON records(ordinal) WHERE minx IS NULL;"
            "CREATE TABLE geometries(ordinal INTEGER NOT NULL,part INTEGER NOT NULL,"
            "wkb BLOB NOT NULL,PRIMARY KEY(ordinal,part));"
            "CREATE VIRTUAL TABLE bounds USING rtree(ordinal,minx,maxx,miny,maxy);"
            "CREATE TABLE faces(ordinal INTEGER PRIMARY KEY,metadata BLOB NOT NULL,display BLOB NOT NULL);"
            "CREATE TABLE topology_routes(route TEXT PRIMARY KEY);"
        )

    def record(self, value: Any) -> None:
        check_operation_cancelled()
        record = _OBJECT.validate_python(value)
        encoded = []
        for geometry_value in record.geometries:
            check_operation_cancelled()
            geometry = shape(geometry_value)
            if (
                geometry.is_empty
                or not geometry.is_valid
                or geometry.geom_type not in _SUPPORTED_TYPES
            ):
                raise ValueError("Повреждена подготовленная геометрия")
            encoded.append(geometry.wkb)
        ordinal = self.records
        metadata = _OBJECT.dump_python(record, mode="json", exclude={"geometries"})
        bounds = record.item.bounds
        if bounds is not None:
            x0, y0, x1, y1 = bounds
            bounds = min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)
        source_layer = (
            record.participation.source_layer
            if record.participation
            else record.item.layer
        )
        self.connection.execute(
            "INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?)",
            (
                ordinal,
                _address(record.item),
                _JSON.dump_json(metadata),
                source_layer,
                len(encoded),
                *(bounds or (None, None, None, None)),
            ),
        )
        self.connection.executemany(
            "INSERT INTO geometries VALUES(?,?,?)",
            ((ordinal, part, value) for part, value in enumerate(encoded)),
        )
        if bounds is not None:
            self.connection.execute(
                "INSERT INTO bounds VALUES(?,?,?,?,?)",
                (
                    ordinal,
                    _rtree_bound(bounds[0], lower=True),
                    _rtree_bound(bounds[2], lower=False),
                    _rtree_bound(bounds[1], lower=True),
                    _rtree_bound(bounds[3], lower=False),
                ),
            )
        self.records += 1

    def face(self, value: Any) -> None:
        check_operation_cancelled()
        face = NativeFace.model_validate(value)
        self.connection.execute(
            "INSERT INTO faces VALUES(?,?,?)",
            (
                self.faces,
                face.model_dump_json(exclude={"display"}).encode(),
                LineString(face.display).wkb,
            ),
        )
        self.connection.executemany(
            "INSERT OR IGNORE INTO topology_routes VALUES(?)",
            ((route,) for route in set(face.routes) - set(face.physical_routes)),
        )
        self.faces += 1

    def consume(self, stream: BinaryIO, key: str) -> PreparedSnapshot:
        values: dict[str, Any] = {}
        events = iter(ijson.parse(stream, use_float=False))
        try:
            if next(events) != ("", "start_map", None):
                raise ValueError("Расчётный снимок должен быть JSON-объектом")
            for prefix, event, name in events:
                if (prefix, event) == ("", "end_map"):
                    if next(events, None) is not None:
                        raise ValueError("Снимок должен содержать один JSON-объект")
                    break
                if prefix or event != "map_key":
                    raise ValueError("Некорректное поле расчётного снимка")
                if name not in PreparedSnapshot.model_fields:
                    raise ValueError("Неизвестное поле расчётного снимка")
                first = next(events)
                if name not in {"records", "faces"}:
                    values[name] = _value(events, first)
                    continue
                if first != (name, "start_array", None):
                    raise ValueError("Коллекция снимка должна быть массивом")
                if name in values:
                    if name == "records":
                        self.connection.executescript(
                            "DELETE FROM records;DELETE FROM geometries;DELETE FROM bounds;"
                        )
                        self.records = 0
                    else:
                        self.connection.executescript(
                            "DELETE FROM faces;DELETE FROM topology_routes;"
                        )
                        self.faces = 0
                values[name] = ()
                accept = self.record if name == "records" else self.face
                while (first := next(events)) != (name, "end_array", None):
                    accept(_value(events, first))
            else:
                raise ValueError("Незавершённый расчётный снимок")
        except StopIteration as error:
            raise ValueError("Незавершённый расчётный снимок") from error
        if values.get("schema_version") not in {
            PREPARED_REVISION,
            LEGACY_PREPARED_REVISION,
        }:
            raise PreparedGeometryUnavailable("unsupported")
        header = PreparedSnapshot.model_validate(values)
        header.assert_storage_basis(key)
        self._validate_participation(header)
        return header

    def _validate_participation(self, header: PreparedSnapshot) -> None:
        reference_layers = frozenset(
            layer["source_name"]
            for layer in (header.geometry_basis or {}).get("layers", [])
            if layer["mapping_confirmed"] is True and layer["mapped_kind"] == "ignore"
        )
        recipe_value = (header.geometry_basis or {}).get("topology_recipe")
        recipe = (
            TopologyRecipe.model_validate(recipe_value)
            if recipe_value is not None
            else None
        )
        for row in self.connection.execute(
            "SELECT ordinal,metadata,projection FROM records ORDER BY ordinal"
        ):
            check_operation_cancelled()
            record = _OBJECT.validate_json(row[1])
            proof = record.participation
            if not record.reason and not row[2]:
                if (
                    record.detail
                    or record.tolerance_m != 0
                    or not _confirmed_reference(
                        record.item,
                        proof,
                        header.capture.snapshot_sha256,
                        header.key,
                        reference_layers,
                    )
                ):
                    raise ValueError("Повреждена подготовленная геометрия")
                self.connection.execute(
                    "UPDATE records SET projection=-1 WHERE ordinal=?", (row[0],)
                )
            if proof is None:
                continue
            if not proof.current(header.capture.snapshot_sha256, header.key):
                raise ValueError(
                    "Участие объектов не соответствует основанию геометрии"
                )
            if proof.proof not in {
                SourceUseProof.PREPARED_TOPOLOGY,
                SourceUseProof.PREPARED_BOUNDARY,
            }:
                continue
            if recipe is None or proof.source_layer not in recipe.companion_layers:
                raise ValueError("Подготовленная граница отсутствует в рецепте")
            if record.item.face_id or set(record.item.routes) & set(
                recipe.excluded_routes
            ):
                raise ValueError("Граница имеет несовместимое операторское решение")
            if proof.proof == SourceUseProof.PREPARED_TOPOLOGY:
                if any(
                    self.connection.execute(
                        "SELECT 1 FROM topology_routes WHERE route=?", (route,)
                    ).fetchone()
                    is None
                    for route in record.item.routes
                ):
                    raise ValueError("Граница не участвовала в native-области")
            elif record.reason or not row[2]:
                raise ValueError("Контекст границы не имеет проверенной геометрии")

    def finish(
        self, header: PreparedSnapshot, source_sha256: str, payload_sha256: str
    ) -> None:
        check_operation_cancelled()
        self.connection.execute(
            "INSERT INTO manifest VALUES(?,?,?,?,?,?,?,?)",
            (
                PREPARED_SQLITE_FORMAT,
                PREPARED_SQLITE_BUILDER,
                header.key,
                source_sha256,
                payload_sha256,
                header.model_dump_json().encode(),
                self.records,
                self.faces,
            ),
        )
        missing = self.connection.execute(
            "SELECT 1 FROM records r LEFT JOIN bounds b ON b.ordinal=r.ordinal "
            "WHERE r.minx IS NOT NULL AND (b.ordinal IS NULL OR b.minx>r.minx OR "
            "b.maxx<r.maxx OR b.miny>r.miny OR b.maxy<r.maxy) LIMIT 1"
        ).fetchone()
        if (
            missing
            or self.connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]
            or self.connection.execute("SELECT rtreecheck('bounds')").fetchone()[0]
            != "ok"
        ):
            raise ValueError("Повреждён пространственный индекс расчётного снимка")
        self.connection.commit()
        self.connection.close()


class _RecordSequence(Sequence[PreparedObject]):
    def __init__(self, resource: SqlitePreparedResource) -> None:
        self.resource = resource

    def __len__(self) -> int:
        return self.resource.count

    @overload
    def __getitem__(self, index: int) -> PreparedObject: ...
    @overload
    def __getitem__(self, index: slice) -> tuple[PreparedObject, ...]: ...
    def __getitem__(
        self, index: int | slice
    ) -> PreparedObject | tuple[PreparedObject, ...]:
        if isinstance(index, slice):
            return tuple(
                self[position] for position in range(*index.indices(len(self)))
            )
        if index < 0:
            index += len(self)
        with self.resource._connection() as connection:
            row = connection.execute(
                "SELECT metadata FROM records WHERE ordinal=?", (index,)
            ).fetchone()
            if row is None:
                raise IndexError(index)
            return _OBJECT.validate_json(row[0])

    def __iter__(self) -> Iterator[PreparedObject]:
        return self.resource.iter_records()


class _FaceSequence(Sequence[NativeFace]):
    def __init__(self, resource: SqlitePreparedResource) -> None:
        self.resource = resource

    def __len__(self) -> int:
        return self.resource.face_count

    @overload
    def __getitem__(self, index: int) -> NativeFace: ...
    @overload
    def __getitem__(self, index: slice) -> tuple[NativeFace, ...]: ...
    def __getitem__(self, index: int | slice) -> NativeFace | tuple[NativeFace, ...]:
        if isinstance(index, slice):
            return tuple(
                self[position] for position in range(*index.indices(len(self)))
            )
        if index < 0:
            index += len(self)
        with self.resource._connection() as connection:
            row = connection.execute(
                "SELECT metadata,display FROM faces WHERE ordinal=?", (index,)
            ).fetchone()
            if row is None:
                raise IndexError(index)
            return self._decode(row)

    @staticmethod
    def _decode(row: tuple) -> NativeFace:
        value = json.loads(row[0])
        # The v1 builder writes internal Pydantic field names in metadata.
        # Native input still requires the public "from" alias; only this trusted
        # derived-resource reader restores it before validating a repaired face.
        for repair in value.get("repairs", ()):
            if "from_" in repair:
                if "from" in repair:
                    raise ValueError("Повторное начало native-восстановления")
                repair["from"] = repair.pop("from_")
        value["display"] = tuple(from_wkb(row[1]).coords)
        return NativeFace.model_validate(value)

    def __iter__(self) -> Iterator[NativeFace]:
        with self.resource._connection() as connection:
            for row in connection.execute(
                "SELECT metadata,display FROM faces ORDER BY ordinal"
            ):
                check_operation_cancelled()
                yield self._decode(row)


class SqlitePreparedResource:
    def __init__(
        self,
        store: PreparedSqliteStore,
        source: Path,
        path: Path,
        registration: _Registration,
        source_fingerprint: Fingerprint,
        binary_fingerprint: Fingerprint,
    ) -> None:
        self.store, self.source, self.path = store, source, path
        self.registration = registration
        self._source_fingerprint, self._binary_fingerprint = (
            source_fingerprint,
            binary_fingerprint,
        )
        self._lock = RLock()
        self._sessions = local()
        self._cache: OrderedDict[bytes, tuple[Projection, int]] = OrderedDict()
        self._cache_bytes = 0
        with self._connection() as connection:
            rows = connection.execute("SELECT * FROM manifest").fetchall()
            if len(rows) != 1 or rows[0][:5] != (
                PREPARED_SQLITE_FORMAT,
                PREPARED_SQLITE_BUILDER,
                registration.key,
                registration.source_sha256,
                registration.payload_sha256,
            ):
                raise ValueError("Основание расчётного ресурса не соответствует снимку")
            self.header = PreparedSnapshot.model_validate_json(rows[0][5])
            self.header.assert_storage_basis(registration.key)
            self.count, self.face_count = int(rows[0][6]), int(rows[0][7])
            if self.count < 0 or self.face_count < 0:
                raise ValueError("Повреждены счётчики расчётного ресурса")
        self.records = _RecordSequence(self)
        self.faces = _FaceSequence(self)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.header, name)

    @property
    def source_fingerprint(self) -> Fingerprint:
        return self._source_fingerprint

    def materialize(self) -> PreparedSnapshot:
        """Явный совместимый экспорт для диагностики, вне рабочего чтения окна."""
        from app.native_query.preparation.prepared_snapshot import PreparedStore

        self.assert_unchanged()
        result = PreparedStore(self.store.directory).load(self.key)
        if result is None:
            raise PreparedGeometryUnavailable("missing")
        self.assert_unchanged()
        return result

    def model_dump(self, *args: Any, **kwargs: Any) -> dict:
        return self.materialize().model_dump(*args, **kwargs)

    def model_dump_json(self, *args: Any, **kwargs: Any) -> str:
        return self.materialize().model_dump_json(*args, **kwargs)

    def assert_unchanged(self) -> None:
        check_operation_cancelled()
        active = getattr(self._sessions, "active", None)
        if active is not None:
            _, source, binary = active
            if (
                _fingerprint(source) != self._source_fingerprint
                or _fingerprint(binary) != self._binary_fingerprint
            ):
                raise PreparedGeometryUnavailable("corrupt")
            return
        try:
            self.store._reject_sidecars(self.path)
            for path, expected, digest in (
                (
                    self.source,
                    self._source_fingerprint,
                    self.registration.source_sha256,
                ),
                (self.path, self._binary_fingerprint, self.registration.sha256),
            ):
                direct_path(path, self.store.directory)
                with read_regular(path) as stream:
                    if _fingerprint(stream) != expected:
                        raise PreparedGeometryUnavailable("corrupt")
                    # Windows ctime can be creation time; unchanged metadata alone
                    # cannot establish unchanged content on that platform.
                    if os.name == "nt" and _digest(stream) != digest:
                        raise PreparedGeometryUnavailable("corrupt")
                    if _fingerprint(stream) != expected:
                        raise PreparedGeometryUnavailable("corrupt")
        except (OSError, ValueError) as error:
            raise PreparedGeometryUnavailable("corrupt") from error

    @contextmanager
    def read_scope(self) -> Iterator[None]:
        """Удерживать проверенные файлы и одно соединение на порцию расчёта.

        Вложенные чтения проверяют открытые дескрипторы. При завершении порции
        повторно проверяются сами пути; ресурс не переносится между потоками.
        """
        with self._connection():
            yield

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        active = getattr(self._sessions, "active", None)
        if active is not None:
            self.assert_unchanged()
            yield active[0]
            self.assert_unchanged()
            return
        self.assert_unchanged()
        with read_regular(self.source) as source, read_regular(self.path) as binary:
            if (
                _fingerprint(source) != self._source_fingerprint
                or _fingerprint(binary) != self._binary_fingerprint
            ):
                raise PreparedGeometryUnavailable("corrupt")
            connection = sqlite3.connect(
                self.path.as_uri() + "?mode=ro&immutable=1", uri=True
            )
            try:
                connection.execute("PRAGMA query_only=ON")
                connection.execute("PRAGMA cache_size=-4096")
                connection.execute("BEGIN")
                self.assert_unchanged()
                self._sessions.active = connection, source, binary
                try:
                    yield connection
                finally:
                    del self._sessions.active
                self.assert_unchanged()
                if (
                    _fingerprint(source) != self._source_fingerprint
                    or _fingerprint(binary) != self._binary_fingerprint
                ):
                    raise PreparedGeometryUnavailable("corrupt")
            finally:
                connection.close()

    def iter_records(self) -> Iterator[PreparedObject]:
        with self._connection() as connection:
            cursor = connection.execute("SELECT metadata FROM records ORDER BY ordinal")
            while rows := cursor.fetchmany(_BATCH):
                check_operation_cancelled()
                for row in rows:
                    yield _OBJECT.validate_json(row[0])

    def iter_face_routes(self) -> Iterator[tuple[int, tuple[str, ...]]]:
        """Покрытию нужны адреса native-области без её контура отображения.

        Метаданные прошли NativeFace при построении; зарегистрированный SHA
        связывает эти байты с той же проверенной схемой и версией построителя.
        """
        with self._connection() as connection:
            for row in connection.execute(
                "SELECT metadata FROM faces ORDER BY ordinal"
            ):
                check_operation_cancelled()
                value = json.loads(row[0])
                yield value["id"], tuple(value["physical_routes"])

    def record(self, item: QueryObject) -> PreparedObject:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT metadata FROM records WHERE address=?", (_address(item),)
            ).fetchone()
            if row is None:
                raise KeyError((item.routes, item.face_id))
            return _OBJECT.validate_json(row[0])

    def candidates(
        self,
        bounds: tuple[float, float, float, float],
        *,
        always_layers: set[str] | frozenset[str] = frozenset(),
        always_keys: set[ObjectKey] | frozenset[ObjectKey] = frozenset(),
    ) -> Iterator[PreparedObject]:
        if len(bounds) != 4 or not all(isfinite(value) for value in bounds):
            raise ValueError("Некорректное окно расчётного снимка")
        x0, y0, x1, y1 = bounds
        if x0 > x1 or y0 > y1:
            raise ValueError("Некорректное окно расчётного снимка")
        with self._connection() as connection:
            # UNION deduplicates candidates on disk without a full Python index.
            sql = (
                "SELECT b.ordinal FROM bounds b JOIN records r ON r.ordinal=b.ordinal "
                "WHERE b.maxx>=? AND b.minx<=? AND b.maxy>=? AND b.miny<=? "
                "AND r.maxx>=? AND r.minx<=? AND r.maxy>=? AND r.miny<=? "
                "UNION SELECT ordinal FROM records WHERE minx IS NULL"
            )
            parameters: list[Any] = [x0, x1, y0, y1, x0, x1, y0, y1]
            if always_layers:
                sql += (
                    " UNION SELECT ordinal FROM records WHERE source_layer IN ("
                    + ",".join("?" for _ in always_layers)
                    + ")"
                )
                parameters.extend(sorted(always_layers))
            if always_keys:
                sql += (
                    " UNION SELECT ordinal FROM records WHERE address IN ("
                    + ",".join("?" for _ in always_keys)
                    + ")"
                )
                parameters.extend(
                    _JSON.dump_json([routes, face])
                    for routes, face in sorted(always_keys)
                )
            cursor = connection.execute(
                "SELECT metadata FROM records WHERE ordinal IN ("
                + sql
                + ") ORDER BY ordinal",
                parameters,
            )
            while rows := cursor.fetchmany(_BATCH):
                check_operation_cancelled()
                for row in rows:
                    yield _OBJECT.validate_json(row[0])

    def get(self, item: QueryObject, *, linear: bool = False) -> Projection:
        del linear
        self.assert_unchanged()
        key = _address(item)
        with cancellable_lock(self._lock):
            if key in self._cache:
                self._cache.move_to_end(key)
                result = self._cache[key][0]
                self.assert_unchanged()
                return result
            with self._connection() as connection:
                row = connection.execute(
                    "SELECT ordinal,metadata,projection FROM records WHERE address=?",
                    (key,),
                ).fetchone()
                if row is None:
                    raise KeyError((item.routes, item.face_id))
                if row[2] == -1:
                    raise ValueError("Справочный объект не содержит расчётную проекцию")
                record = _OBJECT.validate_json(row[1])
                encoded = tuple(
                    value[0]
                    for value in connection.execute(
                        "SELECT wkb FROM geometries WHERE ordinal=? ORDER BY part",
                        (row[0],),
                    )
                )
                value = Projection(
                    tuple(from_wkb(blob) for blob in encoded),
                    record.tolerance_m,
                    record.reason,
                    record.detail,
                )
            weight = 256 + sum(map(len, encoded))
            if weight <= PREPARED_PROJECTION_CACHE_BYTES:
                self._cache[key] = value, weight
                self._cache_bytes += weight
                while (
                    len(self._cache) > PREPARED_PROJECTION_CACHE_ITEMS
                    or self._cache_bytes > PREPARED_PROJECTION_CACHE_BYTES
                ):
                    _, (_, removed) = self._cache.popitem(last=False)
                    self._cache_bytes -= removed
            return value


class PreparedSqliteStore:
    def __init__(
        self,
        directory: Path,
        *,
        database_path: Path | None = None,
        resource_lifecycle: ProjectResourceLifecycle | None = None,
    ) -> None:
        self.directory = directory.absolute()
        self.root = self.directory / "binary"
        self.database_path = database_path or self.root / "catalog.sqlite3"
        self.resource_lifecycle = resource_lifecycle
        self._lock = RLock()

    def _reject_sidecars(self, path: Path) -> None:
        for suffix in ("-wal", "-shm", "-journal"):
            candidate = direct_path(Path(str(path) + suffix), self.root)
            if candidate.exists():
                raise ValueError(
                    "Расчётный ресурс содержит незарегистрированный журнал"
                )

    def open(
        self, source_path: Path, key: str, *, project_id: str | None = None
    ) -> SqlitePreparedResource:
        if self.resource_lifecycle is not None and project_id is not None:
            with project_resource_scope(self, project_id):
                return self._open(source_path, key)
        return self._open(source_path, key)

    def _open(self, source_path: Path, key: str) -> SqlitePreparedResource:
        from app.native_query.preparation.prepared_registry import (
            read_prepared_registration,
            save_prepared_registration,
        )

        if not _KEY.fullmatch(key):
            raise ValueError("Некорректный ключ расчётного снимка")
        register_project_resource("prepared", key)
        source = direct_path(source_path.absolute(), self.directory)
        if source != self.directory / f"{key}.json":
            raise ValueError("Путь расчётного снимка не соответствует ключу")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        registration_path = direct_path(self.root / f"{key}.json", self.root)
        with cancellable_lock(self._lock):
            with read_regular(source) as stream:
                observed = _fingerprint(stream)
                source_sha256 = _digest(stream)
                if _fingerprint(stream) != observed:
                    raise PreparedGeometryUnavailable("corrupt")
            try:
                value = read_prepared_registration(
                    self.database_path, key, source_sha256
                )
                if value is None:
                    raise ValueError("Расчётный ресурс ещё не зарегистрирован")
                registration = _Registration.parse(value)
                if (
                    registration.key != key
                    or registration.source_sha256 != source_sha256
                ):
                    raise ValueError("Регистрация относится к другому снимку")
                return self._open_resource(source, registration, observed)
            except (OSError, ValueError, sqlite3.Error, KeyError, TypeError):
                # Only a derivative is rebuilt; original evidence is never fixed,
                # replaced or silently downgraded by this cache.
                try:
                    registration = self._build(source, key)
                except sqlite3.Error as error:
                    raise ValueError("Повреждён расчётный снимок") from error
                resource = self._open_resource(source, registration, observed)
                save_prepared_registration(
                    self.database_path,
                    registration.__dict__,
                    prepared_directory=self.directory,
                )
                self._publish_registration(registration_path, registration)
                return resource

    def _open_resource(
        self, source: Path, registration: _Registration, source_fingerprint: Fingerprint
    ) -> SqlitePreparedResource:
        path = direct_path(self.root / f"{registration.sha256}.sqlite3", self.root)
        self._reject_sidecars(path)
        with read_regular(path) as stream:
            observed = _fingerprint(stream)
            if (
                observed[-1] != registration.bytes
                or _digest(stream) != registration.sha256
                or _fingerprint(stream) != observed
            ):
                raise ValueError("Нарушена целостность расчётного ресурса")
        return SqlitePreparedResource(
            self, source, path, registration, source_fingerprint, observed
        )

    def _build(self, source: Path, key: str) -> _Registration:
        from app.native_query.preparation.prepared_stream import open_prepared_payload

        pending = None
        builder = None
        try:
            with NamedTemporaryFile(
                dir=self.root, prefix=f".{key}.", suffix=".pending", delete=False
            ) as stream:
                pending = Path(stream.name)
            builder = _Builder(pending)
            with open_prepared_payload(source, self.directory) as payload:
                header = builder.consume(payload.stream, key)
                builder.finish(header, payload.source_sha256, payload.payload_sha256)
                # Windows _commit requires write access. Flush only our pending
                # derivative, then verify it through the usual strict reader.
                with pending.open("r+b") as stream:
                    os.fsync(stream.fileno())
                with read_regular(pending) as stream:
                    registration = _Registration(
                        key,
                        payload.source_sha256,
                        payload.payload_sha256,
                        _digest(stream),
                        os.fstat(stream.fileno()).st_size,
                    )
                check_operation_cancelled()
                register_project_resource("prepared-binary", registration.sha256)
                destination = direct_path(
                    self.root / f"{registration.sha256}.sqlite3", self.root
                )
                try:
                    os.link(pending, destination)
                except FileExistsError:
                    # A corrupt content-addressed derivative is replaced only
                    # after rebuilding from the unchanged authoritative source.
                    self._reject_sidecars(destination)
                    with read_regular(destination) as stream:
                        corrupt = _digest(stream) != registration.sha256
                    # Strict Windows readers deny replacement while open.
                    if corrupt:
                        pending.replace(destination)
                sync_directory(self.root)
                return registration
        finally:
            if builder is not None:
                builder.connection.close()
            if pending is not None:
                pending.unlink(missing_ok=True)

    @staticmethod
    def _publish_registration(path: Path, registration: _Registration) -> None:
        pending = None
        try:
            with NamedTemporaryFile(
                dir=path.parent,
                prefix=f".{path.stem}.",
                suffix=".pending",
                delete=False,
            ) as stream:
                pending = Path(stream.name)
                stream.write(_JSON.dump_json(registration.__dict__))
                stream.flush()
                os.fsync(stream.fileno())
            check_operation_cancelled()
            pending.replace(path)
            sync_directory(path.parent)
        finally:
            if pending is not None:
                pending.unlink(missing_ok=True)
