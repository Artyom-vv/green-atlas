"""Compose independently admitted DXF drawings without erasing their identity."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from pydantic import BaseModel, ConfigDict, Field

from app.cad_bridge import CadSnapshotProvenance
from app.dxf_import.capacity import SourceGeometryCapacity
from app.dxf_import.contracts import DxfImportResult
from app.dxf_import.layer_contracts import Layer
from app.geometry.contracts import CoordinateReference, GeometrySnapshot
from app.geometry.geojson_size import coordinate_count


class ComposedDrawingProvenance(BaseModel):
    """Exact drawing identity retained by a multi-DXF map."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_bytes: int = Field(gt=0)
    dxf_version: str = Field(min_length=1)
    units: str = Field(min_length=1)
    units_assumed: bool = False
    entity_count: int = Field(ge=0)
    bounds: list[float] | None = None
    cad_snapshot: CadSnapshotProvenance | None = None


@dataclass(frozen=True)
class ImportedDrawing:
    path: str
    source: bytes
    imported: DxfImportResult


@dataclass(frozen=True)
class ComposedCadImport:
    imported: DxfImportResult
    drawings: tuple[ComposedDrawingProvenance, ...]


def _layer_key(path: str, name: str) -> str:
    return f"[{path}] {name}"


def _prefix(path: str, digest: str) -> str:
    return sha256(f"{path}\0{digest}".encode()).hexdigest()[:16]


def _merged_bounds(drawings: list[ImportedDrawing]) -> list[float] | None:
    bounds = [item.imported.bounds for item in drawings if item.imported.bounds]
    if not bounds:
        return None
    return [
        min(item[0] for item in bounds),
        min(item[1] for item in bounds),
        max(item[2] for item in bounds),
        max(item[3] for item in bounds),
    ]


def _coordinate_reference(drawings: list[ImportedDrawing]) -> CoordinateReference:
    references = [item.imported.coordinate_reference for item in drawings]
    known = [item for item in references if item.status in {"declared", "verified"}]
    if known:
        identities = {(item.crs_id, item.axis_order) for item in known}
        if len(known) != len(references) or len(identities) != 1:
            raise ValueError(
                "Нельзя совместить DXF с разными или частично неизвестными системами координат"
            )
        strongest = next(
            (item for item in known if item.status == "verified"), known[0]
        )
        return strongest.model_copy(deep=True)
    if any(item.status == "local" for item in references) and not all(
        item.status == "local" for item in references
    ):
        raise ValueError(
            "Нельзя автоматически совместить локальную и неизвестную системы координат"
        )
    return CoordinateReference(
        status=references[0].status,
        source="none",
        evidence=(
            "Композиция независимых DXF использует их общую WCS без преобразования; "
            "геодезическая система координат в файлах не подтверждена"
        ),
    )


def compose_dxf_imports(
    drawings: list[ImportedDrawing],
    *,
    capacity: SourceGeometryCapacity | None = None,
) -> ComposedCadImport:
    """Overlay complete DXF reads in shared WCS with collision-free provenance.

    This does not infer transforms, XREF relations or equivalent drawings. The
    caller must supply the explicitly admitted independent drawing set.
    """

    if not drawings:
        raise ValueError("Для композиции нужен хотя бы один DXF")
    paths = [item.path for item in drawings]
    if len(paths) != len(set(paths)):
        raise ValueError("Один DXF нельзя добавить в композицию дважды")

    capacity = capacity or SourceGeometryCapacity()
    features: list[dict] = []
    layers: list[Layer] = []
    vertical_primitives = []
    warnings: list[str] = []
    provenances: list[ComposedDrawingProvenance] = []
    total_coordinates = 0
    total_entities = 0
    versions: set[str] = set()
    units: set[str] = set()
    units_assumed = False

    for drawing in drawings:
        digest = sha256(drawing.source).hexdigest()
        prefix = _prefix(drawing.path, digest)
        imported = drawing.imported
        versions.add(imported.dxf_version)
        units.add(imported.units)
        units_assumed = units_assumed or imported.units_assumed
        total_entities += imported.entity_count
        provenances.append(
            ComposedDrawingProvenance(
                path=drawing.path,
                source_sha256=digest,
                source_bytes=len(drawing.source),
                dxf_version=imported.dxf_version,
                units=imported.units,
                units_assumed=imported.units_assumed,
                entity_count=imported.entity_count,
                bounds=imported.bounds,
                cad_snapshot=imported.cad_snapshot_provenance,
            )
        )

        layer_names: dict[str, str] = {}
        for layer in imported.layers:
            composed_name = _layer_key(drawing.path, layer.source_name)
            layer_names[layer.source_name] = composed_name
            layers.append(
                layer.model_copy(
                    deep=True,
                    update={
                        "id": f"cad-component-{prefix}-{layer.id}",
                        "source_name": composed_name,
                    },
                )
            )

        for feature_index, source_feature in enumerate(
            imported.geometry.feature_collection.get("features", [])
        ):
            feature = dict(source_feature)
            properties = dict(feature.get("properties") or {})
            original_layer = str(properties.get("source_layer", "0"))
            properties.update(
                {
                    "source_layer": layer_names.get(
                        original_layer, _layer_key(drawing.path, original_layer)
                    ),
                    "source_layer_original_name": original_layer,
                    "source_drawing_path": drawing.path,
                    "source_drawing_sha256": digest,
                }
            )
            feature["properties"] = properties
            feature["id"] = (
                f"cad-component-{prefix}-"
                f"{feature.get('id', f'feature-{feature_index + 1}')}"
            )
            feature_coordinates = coordinate_count(feature.get("geometry", {}))
            capacity.check(
                features=len(features) + 1,
                coordinates=total_coordinates + feature_coordinates,
                feature_coordinates=feature_coordinates,
                layer=properties["source_layer"],
            )
            features.append(feature)
            total_coordinates += feature_coordinates

        for primitive in imported.geometry.vertical_primitives:
            vertical_primitives.append(
                primitive.model_copy(
                    deep=True,
                    update={
                        "primitive_id": f"cad-component-{prefix}-{primitive.primitive_id}",
                        "source_layer": layer_names.get(
                            primitive.source_layer,
                            _layer_key(drawing.path, primitive.source_layer),
                        ),
                        "source_dataset": drawing.path,
                    },
                )
            )
        warnings.extend(f"{drawing.path}: {warning}" for warning in imported.warnings)

    if len(units) != 1:
        raise ValueError("Нормализованные единицы DXF не совпадают")
    coordinate_reference = _coordinate_reference(drawings)
    return ComposedCadImport(
        imported=DxfImportResult(
            layers=layers,
            geometry=GeometrySnapshot(
                feature_collection={"type": "FeatureCollection", "features": features},
                vertical_primitives=vertical_primitives,
            ),
            dxf_version=next(iter(versions)) if len(versions) == 1 else "MULTI_DXF",
            units=next(iter(units)),
            units_assumed=units_assumed,
            entity_count=total_entities,
            bounds=_merged_bounds(drawings),
            warnings=warnings,
            coordinate_reference=coordinate_reference,
        ),
        drawings=tuple(provenances),
    )
