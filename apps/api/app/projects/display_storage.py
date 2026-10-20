"""Производный SQLite/WKB/RTree отображения, без владения CAD и расчётами.

Авторитетный gzip остаётся неизменным. Ресурс строится по одному объекту,
публикуется только после проверки потока и регистрируется SHA в базе проекта.
Открытый читатель удерживает только SQLite cache и выбранные объекты.
"""

from __future__ import annotations

import json
import os
import sqlite3
import struct
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from itertools import islice
from math import copysign, inf, isfinite
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, BinaryIO

from pydantic import TypeAdapter
from shapely import from_wkb, get_num_coordinates, to_wkb
from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry

from app.desktop.tickets import direct_path, read_regular
from app.operations.progress import check_operation_cancelled
from app.projects.backup_files import sync_directory
from app.projects.geometry_storage import GeometryReference, GeometryStore

DISPLAY_FORMAT = "green-atlas.display-sqlite/1"
DISPLAY_BUILDER = "wkb-lossless-rtree/1"
_JSON: TypeAdapter[Any] = TypeAdapter(Any)
_CHUNK = 1024 * 1024
_SQL_BATCH = 512


def display_identity(reference: GeometryReference, geometry_field: str) -> str:
    return sha256(
        _JSON.dump_json(
            [
                DISPLAY_FORMAT,
                DISPLAY_BUILDER,
                reference.model_dump(by_alias=True),
                geometry_field,
            ]
        )
    ).hexdigest()


def _fingerprint(stream: BinaryIO) -> tuple[int, int, int, int, int]:
    observed = os.fstat(stream.fileno())
    return (
        observed.st_dev,
        observed.st_ino,
        observed.st_size,
        observed.st_mtime_ns,
        observed.st_ctime_ns,
    )


def _digest(stream: BinaryIO) -> str:
    stream.seek(0)
    digest = sha256()
    while data := stream.read(_CHUNK):
        check_operation_cancelled()
        digest.update(data)
    stream.seek(0)
    return digest.hexdigest()


def _lists(value: Any) -> Any:
    if isinstance(value, (tuple, list)):
        return [_lists(item) for item in value]
    if isinstance(value, dict):
        return {key: _lists(item) for key, item in value.items()}
    return value


def _coordinate_nodes(geometry: dict) -> Iterator[tuple[dict, str]]:
    if "coordinates" in geometry:
        yield geometry, "coordinates"
    for child in geometry.get("geometries", []):
        yield from _coordinate_nodes(child)


def _restore_numbers(value: Any, bits: bytes, cursor: list[int]) -> Any:
    if isinstance(value, list):
        return [_restore_numbers(item, bits, cursor) for item in value]
    index = cursor[0]
    cursor[0] += 1
    return int(value) if bits and bits[index // 8] & (1 << (index % 8)) else value


def _geometry_extras(original: dict, decoded: dict) -> dict:
    extras: dict[str, Any] = {
        "members": {
            key: value
            for key, value in original.items()
            if key not in {"coordinates", "geometries", "type"}
        }
    }
    if "geometries" in original:
        extras["children"] = [
            _geometry_extras(child, target)
            for child, target in zip(
                original["geometries"], decoded.get("geometries", []), strict=True
            )
        ]
    return extras


def _apply_extras(decoded: dict, extras: dict) -> None:
    decoded.update(extras["members"])
    if "children" in extras:
        for child, values in zip(
            decoded["geometries"], extras["children"], strict=True
        ):
            _apply_extras(child, values)


def _lossless_wkb(original: dict, geometry: BaseGeometry) -> tuple[bytes, bytes, bytes]:
    """WKB binary64 плюс битовая маска исходных JSON integer и foreign members.

    GEOS может закрыть кольцо или изменить пустые компоненты. Такой объект
    использует raw compatibility codec; проверка сравнивает структуру и биты,
    а не только топологическое равенство.
    """
    encoded = bytes(to_wkb(geometry, output_dimension=3, byte_order=1))
    decoded = _lists(mapping(from_wkb(encoded)))
    flags: list[bool] = []

    def compare(source: Any, target: Any) -> None:
        if isinstance(source, list) and isinstance(target, list):
            if len(source) != len(target):
                raise ValueError("WKB изменил структуру координат")
            for left, right in zip(source, target, strict=True):
                compare(left, right)
        elif isinstance(source, int) and not isinstance(source, bool):
            if not isinstance(target, (int, float)) or int(target) != source:
                raise ValueError("Координата не представляется binary64")
            flags.append(True)
        elif isinstance(source, float) and isinstance(target, float):
            if struct.pack("!d", source) != struct.pack("!d", target):
                raise ValueError("WKB изменил точные биты координаты")
            flags.append(False)
        else:
            raise ValueError("Неподдерживаемая структура координат")

    for (source, key), (target, _) in zip(
        _coordinate_nodes(original), _coordinate_nodes(decoded), strict=True
    ):
        compare(source[key], target[key])
    mask = bytearray((len(flags) + 7) // 8)
    for index, integer in enumerate(flags):
        if integer:
            mask[index // 8] |= 1 << (index % 8)
    integer_mask = bytes(mask) if any(mask) else b""
    if integer_mask:
        cursor = [0]
        for target, key in _coordinate_nodes(decoded):
            target[key] = _restore_numbers(target[key], integer_mask, cursor)
    extras = _geometry_extras(original, decoded)
    _apply_extras(decoded, extras)
    # The coordinate walk already proves scalar types, integer values and exact
    # binary64 bits; foreign members were copied unchanged. Compare remaining
    # keys/layout directly without serializing the entire coordinate tree twice.
    if original != decoded:
        raise ValueError("WKB изменил исходную геометрию")
    return encoded, integer_mask, _JSON.dump_json(extras)


def _metadata(feature: Any) -> dict:
    if not isinstance(feature, dict):
        return {}
    properties = feature.get("properties", {})
    if not isinstance(properties, dict):
        return {
            "properties": properties,
            **({"id": feature["id"]} if "id" in feature else {}),
        }
    result = {
        "properties": {
            key: properties[key]
            for key in ("kind", "source_layer", "source_handle", "source_context_only")
            if key in properties
        }
    }
    if "id" in feature:
        result["id"] = feature["id"]
    return result


def _rtree_bound(value: float, *, lower: bool) -> float:
    """Консервативный float32, включая subnormal и выход за диапазон RTree."""
    try:
        rounded = struct.unpack("!f", struct.pack("!f", value))[0]
    except OverflowError:
        rounded = copysign(inf, value)
    if lower and rounded > value or not lower and rounded < value:
        direction = -1 if lower else 1
        if rounded == 0:
            return copysign(struct.unpack("!f", b"\x00\x00\x00\x01")[0], direction)
        bits = struct.unpack("!I", struct.pack("!f", rounded))[0]
        bits += 1 if (direction > 0) == (rounded > 0) else -1
        rounded = struct.unpack("!f", struct.pack("!I", bits))[0]
    return rounded


@dataclass(frozen=True)
class DisplayRegistration:
    identity: str
    sha256: str
    bytes: int


class _DisplayBuilder:
    def __init__(self, directory: Path, identity: str) -> None:
        with NamedTemporaryFile(
            dir=directory, suffix=".pending", delete=False
        ) as output:
            self.path = Path(output.name)
        self.closed = True
        try:
            self.connection = sqlite3.connect(self.path)
            self.closed = False
            self._initialize(identity)
        except Exception:
            self.close()
            raise
        self.count = 0

    def _initialize(self, identity: str) -> None:
        self.connection.execute("PRAGMA journal_mode=OFF")
        self.connection.execute("PRAGMA synchronous=OFF")
        self.connection.execute("PRAGMA cache_size=-4096")
        self.connection.execute(
            "CREATE TABLE manifest(identity TEXT, format TEXT, builder TEXT, "
            "features INTEGER, renderable INTEGER, coordinates INTEGER)"
        )
        self.connection.execute(
            "INSERT INTO manifest VALUES(?,?,?,NULL,NULL,NULL)",
            (identity, DISPLAY_FORMAT, DISPLAY_BUILDER),
        )
        self.connection.execute(
            "CREATE TABLE features(ordinal INTEGER PRIMARY KEY, envelope BLOB NOT NULL, "
            "metadata BLOB NOT NULL, codec TEXT NOT NULL, geometry BLOB, integer_mask BLOB, "
            "extras BLOB, raw BLOB, status TEXT NOT NULL, geom_type TEXT, coordinates INTEGER NOT NULL, "
            "minx REAL, miny REAL, maxx REAL, maxy REAL)"
        )
        self.connection.execute(
            "CREATE VIRTUAL TABLE bounds USING rtree(ordinal,minx,maxx,miny,maxy)"
        )

    def accept(self, feature: Any) -> None:
        check_operation_cancelled()
        ordinal = self.count
        self.count += 1
        envelope = dict(feature) if isinstance(feature, dict) else feature
        raw_geometry = (
            envelope.pop("geometry", None) if isinstance(envelope, dict) else None
        )
        present = isinstance(feature, dict) and "geometry" in feature
        codec, status = "raw", "non_feature"
        encoded = integer_mask = extras = raw = None
        geom_type, coordinates = None, 0
        bounds: tuple[float, float, float, float] | None = None
        if isinstance(feature, dict):
            status = (
                "missing"
                if not present
                else "null"
                if raw_geometry is None
                else "unsupported"
            )
        if present:
            raw = _JSON.dump_json(raw_geometry)
        if isinstance(raw_geometry, dict):
            status = "unsupported"
            try:
                geometry = shape(raw_geometry)
            except Exception:
                pass
            else:
                geom_type = geometry.geom_type
                coordinates = int(get_num_coordinates(geometry))
                encoded = bytes(to_wkb(geometry, output_dimension=3, byte_order=1))
                status = "empty" if geometry.is_empty else "renderable"
                if not geometry.is_empty:
                    observed = tuple(float(value) for value in geometry.bounds)
                    if len(observed) == 4 and all(
                        isfinite(value) for value in observed
                    ):
                        bounds = (observed[0], observed[1], observed[2], observed[3])
                    else:
                        status = "nonfinite_bounds"
                try:
                    encoded, integer_mask, extras = _lossless_wkb(
                        raw_geometry, geometry
                    )
                except (ValueError, TypeError, OverflowError, KeyError):
                    pass
                else:
                    codec, raw = "wkb", None
        self.connection.execute(
            "INSERT INTO features VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                ordinal,
                _JSON.dump_json(envelope),
                _JSON.dump_json(_metadata(feature)),
                codec,
                encoded,
                integer_mask,
                extras,
                raw,
                status,
                geom_type,
                coordinates,
                *(bounds or (None, None, None, None)),
            ),
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

    def finish(self) -> Path:
        check_operation_cancelled()
        total = self.connection.execute(
            "SELECT count(*),min(ordinal),max(ordinal),"
            "coalesce(sum(CASE WHEN status='renderable' THEN 1 ELSE 0 END),0),"
            "coalesce(sum(CASE WHEN status='renderable' THEN coordinates ELSE 0 END),0) FROM features"
        ).fetchone()
        if total[0] != self.count or (
            self.count and (total[1], total[2]) != (0, self.count - 1)
        ):
            raise ValueError("Неполный перечень производного отображения")
        self.connection.execute(
            "UPDATE manifest SET features=?,renderable=?,coordinates=?",
            (total[0], total[3], total[4]),
        )
        missing = self.connection.execute(
            "SELECT 1 FROM features f LEFT JOIN bounds b ON b.ordinal=f.ordinal "
            "WHERE f.status='renderable' AND (b.ordinal IS NULL OR "
            "b.minx>f.minx OR b.maxx<f.maxx OR b.miny>f.miny OR b.maxy<f.maxy) LIMIT 1"
        ).fetchone()
        if (
            missing
            or self.connection.execute("SELECT count(*) FROM bounds").fetchone()[0]
            != total[3]
        ):
            raise ValueError("Неполный пространственный индекс отображения")
        if (
            self.connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]
            or self.connection.execute("SELECT rtreecheck('bounds')").fetchone()[0]
            != "ok"
        ):
            raise ValueError("Повреждён пространственный индекс отображения")
        check_operation_cancelled()
        self.connection.commit()
        self.connection.close()
        self.closed = True
        # Windows _commit requires a writable descriptor even after SQLite
        # has closed its own connection. This is our unpublished pending file.
        with self.path.open("r+b") as stream:
            os.fsync(stream.fileno())
        return self.path

    def close(self) -> None:
        if not self.closed:
            self.connection.close()
            self.closed = True
        self.path.unlink(missing_ok=True)


class SqliteDisplayResource:
    """Один readonly snapshot. Геометрия декодируется только по запросу."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        identity: str,
        project_roles: Callable[[list[dict]], None] | None = None,
    ) -> None:
        self.connection, self.identity, self.project_roles = (
            connection,
            identity,
            project_roles,
        )
        row = connection.execute(
            "SELECT renderable,coordinates FROM manifest"
        ).fetchone()
        self.count, self.coordinates = int(row[0]), int(row[1])

    def _roles(self, feature: dict) -> dict:
        if self.project_roles is not None:
            self.project_roles([feature])
        return feature

    def query_indexes(self, extent: tuple[float, float, float, float]) -> list[int]:
        minx, miny, maxx, maxy = extent
        return [
            int(row[0])
            for row in self.connection.execute(
                "SELECT f.ordinal FROM bounds b JOIN features f ON f.ordinal=b.ordinal "
                "WHERE b.minx<=? AND b.maxx>=? AND b.miny<=? AND b.maxy>=? "
                "AND f.minx<=? AND f.maxx>=? AND f.miny<=? AND f.maxy>=? ORDER BY f.ordinal",
                (maxx, minx, maxy, miny, maxx, minx, maxy, miny),
            )
        ]

    def metadata(self, index: int) -> dict:
        row = self.connection.execute(
            "SELECT metadata FROM features WHERE ordinal=?", (index,)
        ).fetchone()
        if row is None:
            raise IndexError(index)
        return self._roles(json.loads(row[0]))

    def envelope(self, index: int) -> dict:
        """Полные свойства и foreign members без чтения координат WKB."""
        row = self.connection.execute(
            "SELECT envelope FROM features WHERE ordinal=?", (index,)
        ).fetchone()
        if row is None:
            raise IndexError(index)
        feature = json.loads(row[0])
        return self._roles(feature) if isinstance(feature, dict) else feature

    def _batches(self, indexes: Iterable[int], columns: str) -> Iterator[sqlite3.Row]:
        source = iter(indexes)
        while batch := list(islice(source, _SQL_BATCH)):
            check_operation_cancelled()
            rows = {
                int(row["ordinal"]): row
                for row in self.connection.execute(
                    f"SELECT ordinal,{columns} FROM features WHERE ordinal IN ({','.join('?' for _ in batch)})",
                    batch,
                )
            }
            for index in batch:
                if index not in rows:
                    raise IndexError(index)
                yield rows[index]

    def iter_metadata(self, indexes: Iterable[int]) -> Iterator[tuple[int, dict]]:
        source = self._batches(indexes, "metadata")
        while rows := list(islice(source, _SQL_BATCH)):
            features = [json.loads(row["metadata"]) for row in rows]
            if self.project_roles is not None:
                self.project_roles(features)
            for row, feature in zip(rows, features, strict=True):
                yield int(row["ordinal"]), feature

    def geometry(self, index: int) -> BaseGeometry:
        row = self.connection.execute(
            "SELECT geometry FROM features WHERE ordinal=?", (index,)
        ).fetchone()
        if row is None or row[0] is None:
            raise ValueError("Объект не имеет декодируемой геометрии")
        return from_wkb(row[0])

    def iter_geometries(
        self, indexes: Iterable[int]
    ) -> Iterator[tuple[int, BaseGeometry]]:
        for row in self._batches(indexes, "geometry"):
            check_operation_cancelled()
            yield int(row["ordinal"]), from_wkb(row["geometry"])

    def feature(self, index: int) -> dict:
        row = self.connection.execute(
            "SELECT * FROM features WHERE ordinal=?", (index,)
        ).fetchone()
        if row is None:
            raise IndexError(index)
        feature = json.loads(row["envelope"])
        if row["codec"] == "wkb":
            geometry = _lists(mapping(from_wkb(row["geometry"])))
            if row["integer_mask"]:
                cursor = [0]
                for target, key in _coordinate_nodes(geometry):
                    target[key] = _restore_numbers(
                        target[key], row["integer_mask"], cursor
                    )
            _apply_extras(geometry, json.loads(row["extras"]))
            feature["geometry"] = geometry
        elif row["raw"] is not None:
            feature["geometry"] = json.loads(row["raw"])
        return self._roles(feature) if isinstance(feature, dict) else feature

    def iter_indexes(self) -> Iterator[int]:
        for row in self.connection.execute(
            "SELECT ordinal FROM features WHERE status='renderable' ORDER BY ordinal"
        ):
            check_operation_cancelled()
            yield int(row[0])

    def iter_kind_indexes(
        self,
        kinds: set[str],
        extent: tuple[float, float, float, float] | None = None,
    ) -> Iterator[int]:
        """Выборка вида без декодирования WKB и Python-словарей всей карты.

        Без окна сохраняются также пустые и неподдерживаемые объекты: наличие
        вида и ошибки направляющих не должны зависеть от пригодности к отрисовке.
        Текущие роли source editor применяются до проверки вида.
        """
        if not kinds:
            return
        if self.project_roles is not None:
            indexes = (
                self.query_indexes(extent)
                if extent is not None
                else (
                    int(row[0])
                    for row in self.connection.execute(
                        "SELECT ordinal FROM features ORDER BY ordinal"
                    )
                )
            )
            for index, feature in self.iter_metadata(indexes):
                properties = feature.get("properties", {})
                if isinstance(properties, dict) and properties.get("kind") in kinds:
                    yield index
            return
        values: list[Any] = sorted(kinds)
        query = (
            "SELECT f.ordinal FROM features f WHERE "
            "json_extract(f.metadata,'$.properties.kind') IN ("
            + ",".join("?" for _ in values)
            + ")"
        )
        if extent is not None:
            minx, miny, maxx, maxy = extent
            query += (
                " AND f.ordinal IN (SELECT ordinal FROM bounds WHERE "
                "minx<=? AND maxx>=? AND miny<=? AND maxy>=?)"
                " AND f.minx<=? AND f.maxx>=? AND f.miny<=? AND f.maxy>=?"
            )
            values.extend((maxx, minx, maxy, miny, maxx, minx, maxy, miny))
        query += " ORDER BY f.ordinal"
        for row in self.connection.execute(query, values):
            check_operation_cancelled()
            yield int(row[0])

    def iter_scene_candidates(
        self, tree_layers: set[str], curb_layers: set[str]
    ) -> Iterator[dict]:
        source = self.connection.execute(
            "SELECT ordinal,metadata,geom_type FROM features WHERE status='renderable' ORDER BY ordinal"
        )
        while rows := source.fetchmany(_SQL_BATCH):
            check_operation_cancelled()
            features = [json.loads(row["metadata"]) for row in rows]
            if self.project_roles is not None:
                self.project_roles(features)
            for row, feature in zip(rows, features, strict=True):
                properties = feature.get("properties", {})
                layer, kind, geometry_type = (
                    properties.get("source_layer"),
                    properties.get("kind"),
                    row["geom_type"],
                )
                if (
                    layer in tree_layers
                    or layer in curb_layers
                    and kind in {"road", "lawn"}
                    and geometry_type in {"LineString", "MultiLineString"}
                    or kind in {"building", "road", "lawn", "water"}
                    and geometry_type in {"Polygon", "MultiPolygon"}
                ):
                    yield self.feature(int(row["ordinal"]))


class DisplayStore:
    def __init__(self, root: Path) -> None:
        self.root = root.absolute()

    def directory(self, project_id: str) -> Path:
        return direct_path(
            self.root / sha256(project_id.encode()).hexdigest(), self.root
        )

    def path(self, project_id: str, registration: DisplayRegistration) -> Path:
        return direct_path(
            self.directory(project_id) / f"{registration.sha256}.sqlite3", self.root
        )

    def _sidecars(self, path: Path) -> Iterator[Path]:
        for suffix in ("-wal", "-shm", "-journal"):
            yield direct_path(Path(str(path) + suffix), self.root)

    def _reject_sidecars(self, path: Path) -> None:
        if any(candidate.exists() for candidate in self._sidecars(path)):
            raise ValueError(
                "Производное отображение содержит незарегистрированный журнал"
            )

    def discard_corrupt(
        self, project_id: str, registration: DisplayRegistration
    ) -> None:
        """Убрать только известные файлы собственного производного ресурса."""
        path = self.path(project_id, registration)
        for candidate in (path, *self._sidecars(path)):
            if candidate.exists():
                with read_regular(candidate):
                    pass
                candidate.unlink(missing_ok=True)

    @contextmanager
    def build(
        self,
        project_id: str,
        identity: str,
        geometry_store: GeometryStore,
        stream: BinaryIO,
        reference: GeometryReference,
    ) -> Iterator[tuple[DisplayRegistration, Path]]:
        directory = self.directory(project_id)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        builders: list[_DisplayBuilder] = []

        def factory() -> _DisplayBuilder:
            # Numeric fallback and duplicate features keys restart their consumer.
            while builders:
                builders.pop().close()
            builder = _DisplayBuilder(directory, identity)
            builders.append(builder)
            return builder

        try:
            path = geometry_store.consume_features(stream, reference, factory)
            with read_regular(path) as produced:
                registration = DisplayRegistration(
                    identity, _digest(produced), os.fstat(produced.fileno()).st_size
                )
            check_operation_cancelled()
            yield registration, path
        finally:
            for builder in builders:
                builder.close()

    def publish(
        self, project_id: str, registration: DisplayRegistration, pending: Path
    ) -> bool:
        destination = self.path(project_id, registration)
        created = False
        try:
            os.link(pending, destination)
            created = True
        except FileExistsError:
            # Existing content addressed file is not trusted by its filename.
            try:
                self._reject_sidecars(destination)
                with read_regular(destination) as stream:
                    self._verify(stream, registration)
            except (ValueError, OSError):
                self.discard_corrupt(project_id, registration)
                os.link(pending, destination)
                created = True
        sync_directory(destination.parent)
        return created

    @staticmethod
    def _verify(stream: BinaryIO, registration: DisplayRegistration) -> None:
        before = _fingerprint(stream)
        if before[2] != registration.bytes or _digest(stream) != registration.sha256:
            raise ValueError("Нарушена целостность производного отображения")
        if _fingerprint(stream) != before:
            raise ValueError("Производное отображение изменилось во время проверки")

    @contextmanager
    def open(
        self,
        project_id: str,
        registration: DisplayRegistration,
        project_roles: Callable[[list[dict]], None] | None = None,
    ) -> Iterator[SqliteDisplayResource]:
        path = self.path(project_id, registration)
        self._reject_sidecars(path)
        with read_regular(path) as pinned:
            self._verify(pinned, registration)
            fingerprint = _fingerprint(pinned)
            # A closed/fsynced content-addressed file is never updated in place.
            # Immutable mode prevents SQLite from accepting unregistered WAL or
            # rollback bytes. Independent hashes still detect external mutation.
            connection = sqlite3.connect(
                path.as_uri() + "?mode=ro&immutable=1", uri=True
            )
            connection.row_factory = sqlite3.Row
            try:
                connection.execute("PRAGMA query_only=ON")
                connection.execute("PRAGMA cache_size=-4096")
                connection.execute("BEGIN")
                # SQLite reopens by pathname. Pin/check both ends, and verify the
                # complete bytes again before any result leaves this context.
                with read_regular(path) as reopened:
                    if _fingerprint(reopened) != fingerprint:
                        raise ValueError(
                            "Производное отображение заменено при открытии"
                        )
                manifest = connection.execute(
                    "SELECT identity,format,builder FROM manifest"
                ).fetchall()
                if [tuple(row) for row in manifest] != [
                    (registration.identity, DISPLAY_FORMAT, DISPLAY_BUILDER)
                ]:
                    raise ValueError(
                        "Формат производного отображения не соответствует проекту"
                    )
                yield SqliteDisplayResource(
                    connection, registration.identity, project_roles
                )
                check_operation_cancelled()
                self._reject_sidecars(path)
                self._verify(pinned, registration)
                with read_regular(path) as reopened:
                    if _fingerprint(reopened) != fingerprint:
                        raise ValueError(
                            "Производное отображение заменено во время чтения"
                        )
            finally:
                connection.close()

    def discard_project(self, project_id: str) -> None:
        directory = self.directory(project_id)
        if directory.exists():
            for path in directory.glob("*.sqlite3"):
                path.unlink()
            try:
                directory.rmdir()
            except OSError:
                if not any(directory.iterdir()):
                    raise
