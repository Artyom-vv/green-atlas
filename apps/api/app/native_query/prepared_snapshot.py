"""Versioned, durable calculation input, produced only from an AutoCAD capture.

No CAD parsing, contour repair or layer inference happens here. A failed export
stays an addressed record; it never disappears from the calculation ledger.
"""

import json
from hashlib import sha256
from pathlib import Path
from typing import Literal

from pydantic import Field, FiniteFloat
from shapely.geometry import mapping, shape

from app.native_query.contracts import NativeDto, Sha256
from app.native_query.domain_checkpoint import DomainCheckpointStore
from app.native_query.face_contracts import NativeFace
from app.native_query.hybrid_geometry import (
    HybridGeometry,
    Projection,
    uses_linear_geometry,
)
from app.native_query.live_inventory import QueryObject
from app.operations.progress import progress_items, cancellable_lock
from app.regulations.placement_config import PLACEMENT_RULES_REVISION

PREPARED_REVISION = "autocad-prepared-geometry/1"
PREPARATION_POLICY_REVISION = "category-aware-groups/2"


def preparation_key(project, session, linear_layers=frozenset()):
    source = project.source_file
    if source is None or source.content_sha256 != session.snapshot_sha256:
        raise ValueError("Подготовленная геометрия относится к другому захвату")
    # Deliberately exclude PID, paths and session ID. This is an immutable
    # capture, not a promise that a possibly edited editor is still unchanged.
    # geometry_version also changes for work-zone overlays and map refreshes.
    # Those must not require AutoCAD again. Capture + explicit decisions own
    # source geometry identity; preview/apply separately guard project versions.
    value = [
        PREPARED_REVISION,
        PREPARATION_POLICY_REVISION,
        project.id,
        PLACEMENT_RULES_REVISION,
        source.content_sha256,
        session.source_sha256,
        session.inventory_sha256,
        session.units_code,
        sorted(linear_layers),
        [
            layer.model_dump(
                mode="json",
                include={
                    "source_name",
                    "mapped_kind",
                    "mapping_confirmed",
                    "category",
                    "utility_context",
                },
            )
            for layer in sorted(project.layers, key=lambda layer: layer.source_name)
        ],
        [item.model_dump(mode="json") for item in source.native_area_proposals],
        [item.model_dump(mode="json") for item in source.object_decisions],
        [item.model_dump(mode="json") for item in source.area_groups],
        sorted(source.rejected_native_face_keys),
    ]
    return sha256(
        json.dumps(value, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()


class PreparedCapture(NativeDto):
    source_sha256: Sha256
    snapshot_sha256: Sha256
    inventory_sha256: Sha256 | None
    units_code: int


class PreparedObject(NativeDto):
    item: QueryObject
    geometries: tuple[dict, ...] = ()
    tolerance_m: FiniteFloat = Field(default=0, ge=0)
    reason: str = ""
    detail: str = ""


class PreparedSnapshot(NativeDto):
    schema_version: Literal["autocad-prepared-geometry/1"] = PREPARED_REVISION
    key: Sha256
    capture: PreparedCapture
    factor: FiniteFloat = Field(gt=0)
    linear_layers: frozenset[str] = frozenset()
    records: tuple[PreparedObject, ...]
    faces: tuple[NativeFace, ...] = ()
    context_counts: dict[str, int] = Field(default_factory=dict)


class PreparedProjection:
    """Shared source for point measurements, masks, and the completeness report."""

    def __init__(self, snapshot, progress=None):
        self.records = {}
        for record in progress_items(snapshot.records, progress, "Проверяем подготовленные объекты", len(snapshot.records)):
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
            self.records[(record.item.routes, record.item.face_id)] = Projection(
                geometries,
                record.tolerance_m,
                record.reason,
                record.detail,
            )
        if len(self.records) != len(snapshot.records):
            raise ValueError("Повтор объекта в подготовленной геометрии")

    def get(self, item, *, linear=False):
        # Linear/area participation was resolved and hashed during preparation.
        return self.records[(item.routes, item.face_id)]


class PreparedStore:
    def __init__(self, directory: Path):
        self.directory = directory
        self.files = DomainCheckpointStore(directory)

    def load(self, key):
        value = self.files.load(key)
        if value is None:
            return None
        snapshot = PreparedSnapshot.model_validate(value)
        if snapshot.key != key:
            raise ValueError("Подменён ключ подготовленной геометрии")
        return snapshot

    def save(self, snapshot):
        self.files.save(snapshot.key, snapshot.model_dump(mode="json", by_alias=True))


def prepare_snapshot(engine, project, progress=None):
    """Only preparation touches AutoCAD, before and after capturing the ledger."""
    with cancellable_lock(engine._lock):
        engine.assert_current(project)
        key = preparation_key(project, engine.session, engine.linear_layers)
        projection = HybridGeometry(engine, project, require_accuracy=True)
        records = []
        for item in progress_items(engine._objects, progress, "Подготавливаем расчётные объекты", len(engine._objects)):
            layer = engine._layers.get(item.layer)
            value = projection.get(
                item, linear=uses_linear_geometry(item, layer, engine.linear_layers)
            )
            # A readable remainder is a distance constraint, not a lost object.
            if (
                not value.reason
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
                )
            )
        contexts = {}
        for item in engine.inventory.objects:
            if item.context:
                contexts[item.layer] = contexts.get(item.layer, 0) + 1
        engine.assert_current(project)
        if key != preparation_key(project, engine.session, engine.linear_layers):
            raise ValueError("Настройки изменились во время подготовки геометрии")
        return PreparedSnapshot(
            key=key,
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
