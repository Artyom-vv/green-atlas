"""Постоянный расчётный снимок из захвата AutoCAD; ошибки экспорта остаются адресными и
не скрываются при подготовке.
"""

import json
import os
from collections import OrderedDict
from collections.abc import Iterator, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass as plain_dataclass
from hashlib import sha256
from pathlib import Path
from threading import RLock, local
from time import monotonic, perf_counter
from types import MappingProxyType
from typing import TYPE_CHECKING, BinaryIO, Literal, NoReturn
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    TypeAdapter,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic.dataclasses import dataclass
from shapely import from_wkb
from shapely.errors import GEOSException
from shapely.geometry import mapping, shape

from app.dxf_import.layers.contracts import LayerKind
from app.dxf_import.units import meters_per_dxf_unit
from app.native_query.contracts import NativeDto, Sha256
from app.native_query.live.live_inventory import QueryObject
from app.native_query.policy.source_participation import (
    SourceParticipation,
    SourceUse,
    SourceUseProof,
)
from app.native_query.preparation.face_contracts import NativeFace, TopologyRecipe
from app.native_query.preparation.preparation_identity import (
    LEGACY_PREPARED_REVISION,
    LEGACY_RULES_REVISIONS,
    PREPARED_REVISION,
    CaptureIdentity,
    digest,
    geometry_inputs,
    legacy_preparation_key,
    legacy_projection_compatible,
)
from app.native_query.preparation.preparation_identity import (
    preparation_key as preparation_key,
)
from app.native_query.search.domain_checkpoint import (
    CHECKPOINT_IO_BYTES,
    DomainCheckpointStore,
)
from app.native_query.search.hybrid_geometry import (
    HybridGeometry,
    Projection,
    uses_linear_geometry,
)
from app.operations.progress import (
    PROGRESS_REPORT_INTERVAL_S,
    ProgressReporter,
    WorkProgress,
    cancellable_lock,
    check_operation_cancelled,
    progress_items,
)
from app.projects.contracts import Project
from app.projects.resource_lifecycle import ProjectResourceLifecycle
from app.shared.error_messages import message
from app.shared.failures import DomainFailure, FailureReason
from app.shared.performance import record_hash

if TYPE_CHECKING:
    from app.native_query.measurement.live_provider import LiveNativeGeometryEngine

PreparedState = Literal["available", "missing", "corrupt", "stale", "unsupported"]
PREPARED_PROJECTION_CACHE_ITEMS = 4096
PREPARED_PROJECTION_CACHE_BYTES = 8 * 1024 * 1024


class PreparedGeometryUnavailable(DomainFailure):
    def __init__(self, state: PreparedState) -> None:
        self.state = state
        try:
            reason = FailureReason(f"PREPARED_{state.upper()}")
        except ValueError:
            reason = FailureReason.PREPARED_UNAVAILABLE
        super().__init__(reason)
        self.args = (message(reason),)


class PreparedCapture(NativeDto):
    source_sha256: Sha256
    snapshot_sha256: Sha256
    inventory_sha256: Sha256 | None
    units_code: int


class _PreparedParticipationPool(dict[SourceParticipation, SourceParticipation]):
    """Одинаковые проверенные свидетельства одного холодного разбора снимка."""

    def __init__(self) -> None:
        super().__init__()
        self._next_check = 0.0

    def check_cancelled(self) -> None:
        now = monotonic()
        if now >= self._next_check:
            check_operation_cancelled()
            self._next_check = now + PROGRESS_REPORT_INTERVAL_S


@dataclass(
    frozen=True,
    slots=True,
    config=ConfigDict(extra="forbid", ser_json_inf_nan="constants"),
)
class PreparedObject:
    """Проверенная запись без отдельного словаря и набора полей на каждый объект."""

    item: QueryObject
    geometries: tuple[dict, ...] = ()
    tolerance_m: FiniteFloat = Field(default=0, ge=0)
    reason: str = ""
    detail: str = ""
    participation: SourceParticipation | None = None

    @field_validator("participation", mode="after")
    @classmethod
    def share_validated_participation(
        cls, value: SourceParticipation | None, info: ValidationInfo
    ) -> SourceParticipation | None:
        # Каждая запись сначала проходит все проверки. Ключ содержит все поля
        # неизменяемого свидетельства; адрес каждого объекта остаётся отдельным.
        if isinstance(info.context, _PreparedParticipationPool):
            info.context.check_cancelled()
            if value is not None:
                return info.context.setdefault(value, value)
        return value


_PREPARED_OBJECT_CODEC = TypeAdapter(PreparedObject)


def _reject_nonfinite_json(value: str) -> NoReturn:
    raise ValueError(f"Неконечное число в подготовленной геометрии: {value}")


def _record_json(record: PreparedObject) -> str:
    """Сериализовать DTO готовым кодеком, сохранив отказ от NaN и бесконечности."""
    encoded = _PREPARED_OBJECT_CODEC.dump_json(record, by_alias=True)
    if b"NaN" in encoded or b"Infinity" in encoded:
        # Обычная запись не нуждается во второй коллекции Python. Редкие строки
        # с такими словами отличает стандартный JSON-кодек от числовых констант.
        json.loads(encoded, parse_constant=_reject_nonfinite_json)
    return encoded.decode("utf-8")


def _confirmed_reference(
    item: QueryObject,
    participation: SourceParticipation | None,
    source_sha256: str,
    prepared_key: str,
    reference_layers: frozenset[str],
) -> bool:
    """Справочная роль доказана текущим решением о слое, а не именем или формой."""
    return bool(
        participation is not None
        and participation.use == SourceUse.CONTEXT
        and participation.proof == SourceUseProof.CONFIRMED_LAYER
        and participation.current(source_sha256, prepared_key)
        and participation.source_layer == item.layer
        and item.layer in reference_layers
    )


class PreparedSnapshot(NativeDto):
    schema_version: Literal[
        "autocad-prepared-geometry/1", "autocad-prepared-geometry/2"
    ] = PREPARED_REVISION
    key: Sha256
    geometry_basis: dict | None = None
    capture: PreparedCapture
    factor: FiniteFloat = Field(gt=0)
    linear_layers: frozenset[str] = frozenset()
    records: tuple[PreparedObject, ...]
    faces: tuple[NativeFace, ...] = ()
    context_counts: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def source_participation_matches_basis(self) -> "PreparedSnapshot":
        self.assert_source_participation_basis()
        return self

    def assert_storage_basis(self, key: str) -> None:
        """Проверять одно основание при чтении и перед устойчивой записью."""
        if self.schema_version not in {PREPARED_REVISION, LEGACY_PREPARED_REVISION}:
            raise PreparedGeometryUnavailable("unsupported")
        if self.key != key or (
            self.schema_version == PREPARED_REVISION
            and (self.geometry_basis is None or digest(self.geometry_basis) != key)
        ):
            raise ValueError("Подменено основание подготовленной геометрии")
        if self.schema_version == PREPARED_REVISION:
            basis = self.geometry_basis
            if (
                basis is None
                or basis["capture"]
                != {**self.capture.model_dump(), "factor": self.factor}
                or sorted(self.linear_layers) != basis["linear_layers"]
            ):
                raise ValueError("Метаданные расходятся с основанием геометрии")

    def json_chunks(self) -> Iterator[str]:
        """Готовый кодек пишет записи ограниченными блоками без общей JSON-копии."""
        metadata = self.model_dump(mode="json", by_alias=True, exclude={"records"})
        yield json.dumps(metadata, separators=(",", ":"), allow_nan=False)[:-1]
        yield ',"records":['
        parts: list[str] = []
        size = 0
        for index, record in enumerate(self.records):
            if index:
                parts.append(",")
                size += 1
            value = _record_json(record)
            parts.append(value)
            size += len(value)
            if size >= CHECKPOINT_IO_BYTES:
                yield "".join(parts)
                parts.clear()
                size = 0
        if parts:
            yield "".join(parts)
        yield "]}"

    def assert_source_participation_basis(self) -> None:
        """Не допускает переноса назначений и участия границ между снимками."""
        recipe_value = (self.geometry_basis or {}).get("topology_recipe")
        recipe = (
            TopologyRecipe.model_validate(recipe_value)
            if recipe_value is not None
            else None
        )
        topology_routes = {
            route
            for face in self.faces
            for route in set(face.routes) - set(face.physical_routes)
        }
        for record in self.records:
            proof = record.participation
            if proof is None:
                continue
            if not proof.current(self.capture.snapshot_sha256, self.key):
                raise ValueError(
                    "Участие объектов не соответствует основанию геометрии"
                )
            if proof.proof in {
                SourceUseProof.PREPARED_TOPOLOGY,
                SourceUseProof.PREPARED_BOUNDARY,
            }:
                if recipe is None or proof.source_layer not in recipe.companion_layers:
                    raise ValueError("Подготовленная граница отсутствует в рецепте")
                if record.item.face_id or set(record.item.routes) & set(
                    recipe.excluded_routes
                ):
                    raise ValueError("Граница имеет несовместимое операторское решение")
                if (
                    proof.proof == SourceUseProof.PREPARED_TOPOLOGY
                    and not set(record.item.routes) <= topology_routes
                ):
                    raise ValueError("Граница не участвовала в native-области")
                if proof.proof == SourceUseProof.PREPARED_BOUNDARY and (
                    record.reason or not record.geometries
                ):
                    raise ValueError("Контекст границы не имеет проверенной геометрии")


@plain_dataclass(frozen=True, slots=True)
class _StoredProjection:
    """Проверенные координаты без постоянно удерживаемого объекта GEOS."""

    geometries: tuple[bytes, ...]
    tolerance_m: float
    reason: str
    detail: str

    def projection(self) -> Projection:
        return Projection(
            tuple(from_wkb(value) for value in self.geometries),
            self.tolerance_m,
            self.reason,
            self.detail,
        )

    @property
    def cache_weight(self) -> int:
        # Лимит координат дополняет число записей; большая область не вытесняет
        # весь расчётный снимок в постоянно удерживаемые объекты GEOS.
        return 256 + sum(len(value) for value in self.geometries)


class PreparedProjection:
    """Проверить снимок целиком, хранить GEOS только для текущих запросов расчёта."""

    def __init__(
        self, snapshot: PreparedSnapshot, progress: ProgressReporter | None = None
    ) -> None:
        records: dict[tuple[tuple[str, ...], int], _StoredProjection | None] = {}
        for record, projection in self._validated(snapshot, progress):
            key = record.item.routes, record.item.face_id
            if key in records:
                raise ValueError("Повтор объекта в подготовленной геометрии")
            records[key] = (
                _StoredProjection(
                    tuple(geometry.wkb for geometry in projection.geometries),
                    projection.tolerance_m,
                    projection.reason,
                    projection.detail,
                )
                if projection is not None
                else None
            )
        self.records: Mapping[tuple[tuple[str, ...], int], _StoredProjection | None] = (
            MappingProxyType(records)
        )
        self._cache: OrderedDict[
            tuple[tuple[str, ...], int], tuple[Projection, int]
        ] = OrderedDict()
        self._cache_bytes = 0
        self._lock = RLock()

    @staticmethod
    def _validated(
        snapshot: PreparedSnapshot, progress: ProgressReporter | None = None
    ) -> Iterator[tuple[PreparedObject, Projection | None]]:
        reference_layers = frozenset(
            layer["source_name"]
            for layer in (snapshot.geometry_basis or {}).get("layers", [])
            if layer["mapping_confirmed"] is True
            and layer["mapped_kind"] == LayerKind.IGNORE
        )
        next_check = 0.0
        for record in progress_items(
            snapshot.records,
            progress,
            "Проверяем подготовленные объекты",
            len(snapshot.records),
        ):
            now = monotonic()
            if now >= next_check:
                check_operation_cancelled()
                next_check = now + PROGRESS_REPORT_INTERVAL_S
            if (
                not record.geometries
                and not record.reason
                and not record.detail
                and record.tolerance_m == 0
                and _confirmed_reference(
                    record.item,
                    record.participation,
                    snapshot.capture.snapshot_sha256,
                    snapshot.key,
                    reference_layers,
                )
            ):
                # Адрес остаётся в полном перечне, но справочная роль не требует
                # физической проекции. Запрос геометрии ниже завершится отказом.
                yield record, None
                continue
            geometries = tuple(shape(value) for value in record.geometries)
            if (not record.reason and not geometries) or any(
                g.is_empty
                or not g.is_valid
                or g.geom_type
                not in {
                    "Polygon",
                    "MultiPolygon",
                    "LineString",
                    "MultiLineString",
                    "Point",
                }
                for g in geometries
            ):
                raise ValueError("Повреждена подготовленная геометрия")
            yield (
                record,
                Projection(
                    geometries,
                    record.tolerance_m,
                    record.reason,
                    record.detail,
                ),
            )
        check_operation_cancelled()

    @classmethod
    def validate(cls, snapshot: PreparedSnapshot) -> None:
        """Проверка перед записью не создаёт вторую постоянную коллекцию проекций."""
        addresses: set[tuple[tuple[str, ...], int]] = set()
        for record, _ in cls._validated(snapshot):
            key = record.item.routes, record.item.face_id
            if key in addresses:
                raise ValueError("Повтор объекта в подготовленной геометрии")
            addresses.add(key)

    def get(self, item: QueryObject, *, linear: bool = False) -> Projection:
        # Участие линий и областей определено и захешировано при подготовке.
        key = item.routes, item.face_id
        value = self.records[key]
        if value is None:
            raise ValueError("Справочный объект не содержит расчётную проекцию")
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return cached[0]
            projection = value.projection()
            weight = value.cache_weight
            if weight <= PREPARED_PROJECTION_CACHE_BYTES:
                self._cache[key] = projection, weight
                self._cache_bytes += weight
                while (
                    len(self._cache) > PREPARED_PROJECTION_CACHE_ITEMS
                    or self._cache_bytes > PREPARED_PROJECTION_CACHE_BYTES
                ):
                    _, (_, removed_weight) = self._cache.popitem(last=False)
                    self._cache_bytes -= removed_weight
            return projection


FileFingerprint = tuple[int, int, int, int, int]


class _PreparedSchemaHeader(BaseModel):
    """Различать неизвестную схему и ошибки полей только при отказе кодека."""

    model_config = ConfigDict(extra="ignore")
    schema_version: str | None = None


class PreparedStore:
    def __init__(
        self,
        directory: Path,
        *,
        database_path: Path | None = None,
        resource_lifecycle: ProjectResourceLifecycle | None = None,
    ) -> None:
        self.directory = directory
        self.files = DomainCheckpointStore(directory)
        self.database_path = database_path
        self.resource_lifecycle = resource_lifecycle
        self._compatible_sources: dict[str, str] = {}
        self._failures: dict[str, tuple[PreparedState, FileFingerprint | None]] = {}
        self._verified: dict[str, FileFingerprint] = {}
        # Windows может сохранить оба времени при быстрой записи того же размера.
        # SHA относится к уже проверенным байтам, а не к текущему stat.
        self._verified_content: dict[str, str] = {}
        self._failed_content: dict[str, str | None] = {}
        self._read_scopes = local()

    def _fingerprint(self, key: str) -> FileFingerprint | None:
        try:
            if os.name == "nt":
                from app.native_query.transport.windows_job_files import (
                    file_fingerprint,
                )

                return file_fingerprint(self.directory / f"{key}.json")
            stat = (self.directory / f"{key}.json").stat()
            return (
                stat.st_dev,
                stat.st_ino,
                stat.st_mtime_ns,
                stat.st_ctime_ns,
                stat.st_size,
            )
        except (FileNotFoundError, ValueError):
            return None

    def unchanged(self, key: str) -> bool:
        """Повторно использовать проверенный вход в памяти, пока сохранённый файл не
        изменён.
        """
        stored_key = self._compatible_sources.get(key, key)
        if stored_key != key and self._fingerprint(key) is not None:
            return False
        fingerprint = self._verified.get(stored_key)
        if fingerprint is None:
            return False
        if os.name == "nt":
            active = getattr(self._read_scopes, "active", {})
            if stored_key in active:
                from app.native_query.transport.windows_job_files import (
                    stream_fingerprint,
                )

                # HANDLE удерживает файл и цепочку каталогов от замены. Полный
                # путь повторно проверяется на выходе из порции, без обхода на точку.
                return fingerprint == stream_fingerprint(active[stored_key])
        if fingerprint != self._fingerprint(stored_key):
            return False
        if os.name == "nt":
            expected = self._verified_content.get(stored_key)
            return expected is not None and expected == self._content_digest(stored_key)
        return True

    @contextmanager
    def read_scope(self, key: str) -> Iterator[None]:
        """Один подтверждённый SHA и запрет записи на порцию Windows-расчёта."""
        if os.name != "nt":
            yield
            return
        from app.desktop.tickets import read_regular
        from app.native_query.transport.windows_job_files import stream_fingerprint

        stored_key = self._compatible_sources.get(key, key)
        active = getattr(self._read_scopes, "active", {})
        if stored_key in active:
            if not self.unchanged(key):
                raise PreparedGeometryUnavailable("corrupt")
            yield
            if not self.unchanged(key):
                raise PreparedGeometryUnavailable("corrupt")
            return
        with ExitStack() as guards:
            try:
                stream = guards.enter_context(
                    read_regular(self.directory / f"{stored_key}.json")
                )
                observed = stream_fingerprint(stream)
                expected = self._verified_content.get(stored_key)
                if (
                    observed != self._verified.get(stored_key)
                    or expected is None
                    or self._stream_digest(stream) != expected
                ):
                    raise ValueError("Подготовленная геометрия изменилась")
            except (OSError, ValueError) as error:
                raise PreparedGeometryUnavailable("corrupt") from error
            active[stored_key] = stream
            self._read_scopes.active = active
            try:
                yield
            finally:
                del active[stored_key]
                if not active:
                    del self._read_scopes.active
            try:
                if observed != stream_fingerprint(
                    stream
                ) or observed != self._fingerprint(stored_key):
                    raise ValueError("Подготовленная геометрия изменилась")
            except (OSError, ValueError) as error:
                raise PreparedGeometryUnavailable("corrupt") from error

    def verified_fingerprint(self, key: str) -> FileFingerprint | None:
        """Дать сводке отпечаток уже проверенных байтов без загрузки снимка."""
        if not self.unchanged(key):
            return None
        return self._verified.get(self._compatible_sources.get(key, key))

    def accept_verified(
        self,
        key: str,
        fingerprint: FileFingerprint,
        *,
        source_sha256: str | None = None,
    ) -> None:
        """Запомнить вход после полной проверки производным хранилищем.

        Вызывается только после проверки исходного SHA-конверта, записей и
        бинарного ресурса. Совпадение stat само по себе не является проверкой.
        """
        if self._fingerprint(key) != fingerprint:
            raise PreparedGeometryUnavailable("stale")
        if os.name == "nt":
            if source_sha256 is None or self._content_digest(key) != source_sha256:
                raise PreparedGeometryUnavailable("corrupt")
            self._verified_content[key] = source_sha256
        self._compatible_sources.pop(key, None)
        self._failures.pop(key, None)
        self._failed_content.pop(key, None)
        self._verified[key] = fingerprint

    def _known_state(self, key: str) -> PreparedState:
        failure = self._failures.get(key)
        if failure is not None and failure[1] == self._fingerprint(key):
            if os.name != "nt" or self._failed_content.get(key) == self._content_digest(
                key
            ):
                return failure[0]
        self._failures.pop(key, None)
        self._failed_content.pop(key, None)
        return "available"

    def record_failure(self, key: str, state: PreparedState) -> None:
        key = self._compatible_sources.get(key, key)
        self._verified.pop(key, None)
        self._verified_content.pop(key, None)
        self._failures[key] = state, self._fingerprint(key)
        if os.name == "nt":
            self._failed_content[key] = self._content_digest(key)

    @staticmethod
    def _stream_digest(stream: BinaryIO) -> str:
        """Хешировать порциями с прежней проверкой отмены операции."""
        started, byte_count = perf_counter(), 0
        try:
            digest = sha256()
            while chunk := stream.read(CHECKPOINT_IO_BYTES):
                check_operation_cancelled()
                digest.update(chunk)
                byte_count += len(chunk)
            check_operation_cancelled()
            return digest.hexdigest()
        finally:
            record_hash(byte_count, perf_counter() - started)

    def _content_digest(self, key: str) -> str | None:
        """Проверять байты с Windows HANDLE, запрещающим параллельную запись."""
        from app.desktop.tickets import read_regular

        try:
            with read_regular(self.directory / f"{key}.json") as stream:
                return self._stream_digest(stream)
        except (OSError, ValueError):
            return None

    def _read_payload(
        self, key: str
    ) -> tuple[bytes | None, FileFingerprint | None, str | None]:
        """Связать проверенный payload и SHA файла одним удержанным HANDLE."""
        if os.name != "nt":
            observed = self._fingerprint(key)
            return self.files.load_payload(key), observed, None
        from app.desktop.tickets import read_regular
        from app.native_query.transport.windows_job_files import stream_fingerprint

        try:
            with read_regular(self.directory / f"{key}.json") as stream:
                observed = stream_fingerprint(stream)
                payload = self.files.load_payload(key)
                digest = self._stream_digest(stream)
                if observed != stream_fingerprint(stream):
                    raise ValueError("Подготовленная геометрия изменилась при чтении")
                return payload, observed, digest
        except FileNotFoundError:
            return None, None, None

    def load(self, key: str) -> PreparedSnapshot | None:
        try:
            payload, observed, content_digest = self._read_payload(key)
            if payload is None:
                return None
            try:
                snapshot = PreparedSnapshot.model_validate_json(
                    payload, context=_PreparedParticipationPool()
                )
            except ValidationError as error:
                # Готовый кодек сначала проверяет весь JSON. Неизвестная схема
                # отличается от повреждённого JSON и ошибок остальных полей.
                errors = error.errors(include_input=False, include_url=False)
                if any(item["type"] == "json_invalid" for item in errors):
                    raise
                if any(
                    item["loc"] == ("schema_version",)
                    and item["type"] in {"literal_error", "missing"}
                    for item in errors
                ):
                    raise PreparedGeometryUnavailable("unsupported") from error
                try:
                    header = _PreparedSchemaHeader.model_validate_json(payload)
                except ValidationError as header_error:
                    if any(
                        item["loc"] == ("schema_version",)
                        for item in header_error.errors(
                            include_input=False, include_url=False
                        )
                    ):
                        raise PreparedGeometryUnavailable("unsupported") from error
                    raise error
                if header.schema_version not in {
                    PREPARED_REVISION,
                    LEGACY_PREPARED_REVISION,
                }:
                    raise PreparedGeometryUnavailable("unsupported") from error
                raise
            if "schema_version" not in snapshot.model_fields_set:
                raise PreparedGeometryUnavailable("unsupported")
            snapshot.assert_storage_basis(key)
            self._failures.pop(key, None)
            self._failed_content.pop(key, None)
            if observed is not None:
                self._verified[key] = observed
                if content_digest is not None:
                    self._verified_content[key] = content_digest
            return snapshot
        except PreparedGeometryUnavailable as error:
            self.record_failure(key, error.state)
            raise
        except (ValueError, OSError, KeyError, TypeError) as error:
            self.record_failure(key, "corrupt")
            raise PreparedGeometryUnavailable("corrupt") from error

    def save(self, snapshot: PreparedSnapshot) -> None:
        snapshot.assert_storage_basis(snapshot.key)
        owner = (snapshot.geometry_basis or {}).get("project_id")
        if self.resource_lifecycle is not None and isinstance(owner, str):
            with self.resource_lifecycle.use(owner):
                self.resource_lifecycle.register(owner, "prepared", snapshot.key)
                self._save(snapshot)
            return
        self._save(snapshot)

    def _save(self, snapshot: PreparedSnapshot) -> None:
        self._compatible_sources.pop(snapshot.key, None)
        snapshot.assert_storage_basis(snapshot.key)
        PreparedProjection.validate(snapshot)
        snapshot.assert_source_participation_basis()
        # Ключ определяет неизменяемый расчётный вход, на который ссылается история.
        # Явное восстановление сохраняет непрочитанные байты вместо замены свидетельств.
        try:
            existing = self.load(snapshot.key)
            if existing is not None:
                PreparedProjection.validate(existing)
        except (
            PreparedGeometryUnavailable,
            ValueError,
            KeyError,
            TypeError,
            GEOSException,
        ):
            path = self.directory / f"{snapshot.key}.json"
            path.replace(self.directory / f"{snapshot.key}.{uuid4().hex}.damaged")
            existing = None
        if existing is not None and existing != snapshot:
            raise ValueError("Конфликт содержимого подготовленной геометрии")
        if existing is None:
            written_sha256 = self.files.save_chunks(
                snapshot.key, snapshot.json_chunks()
            )
            observed = self._fingerprint(snapshot.key)
            if (
                observed is None
                or not self.files.matches_saved(snapshot.key, written_sha256)
                or observed != self._fingerprint(snapshot.key)
            ):
                self.record_failure(snapshot.key, "corrupt")
                raise PreparedGeometryUnavailable("corrupt")
            self._verified[snapshot.key] = observed
            if os.name == "nt":
                self._verified_content[snapshot.key] = written_sha256
        self._failures.pop(snapshot.key, None)
        self._failed_content.pop(snapshot.key, None)
        if snapshot.geometry_basis is not None:
            project_id = snapshot.geometry_basis["project_id"]
            self.files.save(
                self.reference_key(project_id), {"geometry_key": snapshot.key}
            )

    @staticmethod
    def reference_key(project_id: str) -> str:
        return "project-" + sha256(project_id.encode()).hexdigest()

    def state(self, project: Project, session: CaptureIdentity) -> PreparedState:
        key = preparation_key(project, session)
        if (self.directory / f"{key}.json").is_file():
            return self._known_state(key)
        legacy = self.legacy_keys(project, session)
        for candidate in legacy:
            if (self.directory / f"{candidate}.json").is_file():
                if not legacy_projection_compatible() or any(
                    layer.utility_axis_bindings for layer in project.layers
                ):
                    return "unsupported"
                return self._known_state(candidate)
        if (self.directory / f"{self.reference_key(project.id)}.json").is_file():
            return "stale"
        return "missing"

    @staticmethod
    def legacy_keys(
        project: Project,
        session: CaptureIdentity,
        linear_layers: frozenset[str] = frozenset(),
    ) -> tuple[str, ...]:
        return tuple(
            legacy_preparation_key(project, session, revision, linear_layers)
            for revision in LEGACY_RULES_REVISIONS
        )

    def load_project(
        self,
        project: Project,
        session: CaptureIdentity,
        linear_layers: frozenset[str] = frozenset(),
    ) -> PreparedSnapshot:
        if self.resource_lifecycle is not None:
            with self.resource_lifecycle.use(project.id):
                snapshot = self._load_project(project, session, linear_layers)
                self.resource_lifecycle.register(
                    project.id,
                    "prepared",
                    self._compatible_sources.get(snapshot.key, snapshot.key),
                )
                return snapshot
        return self._load_project(project, session, linear_layers)

    def _load_project(
        self,
        project: Project,
        session: CaptureIdentity,
        linear_layers: frozenset[str] = frozenset(),
    ) -> PreparedSnapshot:
        key = preparation_key(project, session, linear_layers)
        snapshot = self.load(key)
        if snapshot is not None:
            self._compatible_sources.pop(key, None)
        if snapshot is None:
            # v1 не хешировал привязки осей. Даже точный старый ключ не подтверждает
            # привязки, по которым построена физическая проекция.
            if not legacy_projection_compatible() or any(
                layer.utility_axis_bindings for layer in project.layers
            ):
                raise PreparedGeometryUnavailable(self.state(project, session))
            for candidate in self.legacy_keys(project, session, linear_layers):
                legacy = self.load(candidate)
                if legacy is None:
                    continue
                if legacy.schema_version != LEGACY_PREPARED_REVISION:
                    raise PreparedGeometryUnavailable("unsupported")
                if legacy.capture.model_dump() != {
                    name: getattr(session, name)
                    for name in PreparedCapture.model_fields
                } or legacy.factor != meters_per_dxf_unit(session.units_code):
                    raise PreparedGeometryUnavailable("corrupt")
                try:
                    PreparedProjection.validate(legacy)
                except (ValueError, KeyError, TypeError, GEOSException) as error:
                    self.record_failure(candidate, "corrupt")
                    raise PreparedGeometryUnavailable("corrupt") from error
                # Адаптируем проверенный v1 в памяти. Открытие проекта не мигрирует хранилище
                # и не резервирует всю рабочую среду. Исходные байты и проверенный отпечаток
                # сохраняют приоритет, включая повторное использование кеша движка.
                snapshot = legacy.model_copy(
                    update={
                        "schema_version": PREPARED_REVISION,
                        "key": key,
                        "geometry_basis": geometry_inputs(
                            project, session, linear_layers
                        ),
                    }
                )
                self._compatible_sources[key] = candidate
                break
        if snapshot is None:
            raise PreparedGeometryUnavailable(self.state(project, session))
        if (
            snapshot.capture.model_dump()
            != {name: getattr(session, name) for name in PreparedCapture.model_fields}
            or snapshot.factor != meters_per_dxf_unit(session.units_code)
            or snapshot.linear_layers != linear_layers
        ):
            self.record_failure(key, "corrupt")
            raise PreparedGeometryUnavailable("corrupt")
        return snapshot

    def preserve_compatible(self, snapshot: PreparedSnapshot) -> None:
        """Сохранять основание до явной записи метаданных, изменяющей ключи v1."""
        if snapshot.key not in self._compatible_sources:
            return
        if not self.unchanged(snapshot.key):
            raise PreparedGeometryUnavailable("stale")
        if self.database_path is not None:
            from app.projects.workspace_backup import backup_workspace

            backup_workspace(
                self.database_path,
                reason="prepared-geometry-v2",
                prepared_directory=self.directory,
            )
        self.save(snapshot)


def prepare_snapshot(
    engine: "LiveNativeGeometryEngine",
    project: Project,
    progress: ProgressReporter | None = None,
) -> PreparedSnapshot:
    """Только явная подготовка обращается к AutoCAD до и после захвата перечня объектов."""
    with cancellable_lock(engine._lock):
        if progress:
            progress(WorkProgress("Восстанавливаем области чертежа"))
            engine.assert_current(project, progress=progress)
        else:
            engine.assert_current(project)
        key = preparation_key(project, engine.session, engine.linear_layers)
        projection = HybridGeometry(
            engine, project, require_accuracy=True, cache_projections=False
        )
        reference_layers = frozenset(
            layer.source_name
            for layer in project.layers
            if layer.mapping_confirmed and layer.mapped_kind == LayerKind.IGNORE
        )
        records: list[PreparedObject] = []
        item: QueryObject
        source_objects = engine._source_objects
        for item in progress_items(
            source_objects,
            progress,
            "Подготавливаем расчётные объекты",
            len(source_objects),
        ):
            layer = engine._layers.get(item.layer)
            participation = engine.source_participation(item, key)
            if _confirmed_reference(
                item,
                participation,
                engine.session.snapshot_sha256,
                key,
                reference_layers,
            ):
                records.append(PreparedObject(item=item, participation=participation))
                continue
            value = projection.get(
                item,
                linear=(
                    item.curve
                    and participation.proof
                    in {
                        SourceUseProof.PREPARED_BOUNDARY,
                        SourceUseProof.PREPARED_TOPOLOGY,
                    }
                )
                or uses_linear_geometry(item, layer, engine.linear_layers),
            )
            participation = engine.source_participation(
                item,
                key,
                prepared_projection_success=not value.reason and bool(value.geometries),
            )
            # Читаемый остаток является ограничением расстояния, а не потерянным объектом.
            if (
                not item.native_linear
                and not value.reason
                and layer
                and layer.mapped_kind != "site_border"
                and all(
                    g.geom_type not in {"Polygon", "MultiPolygon"}
                    for g in value.geometries
                )
            ):
                item = item.model_copy(update={"native_linear": True})
            records.append(
                PreparedObject(
                    item=item,
                    geometries=tuple(mapping(g) for g in value.geometries),
                    tolerance_m=value.tolerance_m,
                    reason=value.reason,
                    detail=value.detail,
                    participation=participation,
                )
            )
        contexts: dict[str, int] = {}
        for inventory_item in engine.inventory.objects:
            if inventory_item.context:
                contexts[inventory_item.layer] = (
                    contexts.get(inventory_item.layer, 0) + 1
                )
        if progress:
            progress(WorkProgress("Проверяем основание геометрии"))
        engine.assert_current(project)
        if key != preparation_key(project, engine.session, engine.linear_layers):
            raise ValueError("Настройки изменились во время подготовки геометрии")
        if engine.factor is None:
            raise ValueError("Единицы исходной геометрии неизвестны")
        return PreparedSnapshot(
            key=key,
            geometry_basis=geometry_inputs(
                project, engine.session, engine.linear_layers
            ),
            capture=PreparedCapture.model_validate(
                {
                    name: getattr(engine.session, name)
                    for name in PreparedCapture.model_fields
                }
            ),
            factor=engine.factor,
            linear_layers=engine.linear_layers,
            records=tuple(records),
            faces=engine._faces,
            context_counts=contexts,
        )
