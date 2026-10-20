"""Допуск исходника и назначений слоёв; производный предпросмотр сохраняет запрет
расчёта на всех путях импорта.
"""

from typing import Any

from shapely.errors import GEOSException
from shapely.geometry import shape

from app.dxf_import.contracts import (
    ImportEditability,
    ImportMode,
    ImportStatus,
    SourceFile,
)
from app.dxf_import.layers.contracts import BoundaryCandidateStatus, LayerKind
from app.dxf_import.source.preview_contracts import CAD_PREVIEW_MESSAGE
from app.projects.contracts import Project
from app.shared.failures import DomainFailure, FailureReason


def cad_preview_status() -> ImportStatus:
    return ImportStatus(
        mode=ImportMode.CAD_PREVIEW,
        editability=ImportEditability.READ_ONLY,
        message=CAD_PREVIEW_MESSAGE,
    )


def require_calculation_source(source: SourceFile | None) -> None:
    if source is not None and source.preview_provenance is not None:
        raise DomainFailure(FailureReason.CAD_PREVIEW_READ_ONLY)


def partial_geometry_accepted(source: SourceFile | None) -> bool:
    if source is None:
        return False
    provenance = source.prepared_provenance
    return bool(
        source.accept_partial_geometry
        or (provenance and provenance.opening_review.accept_partial_geometry)
    )


def require_confirmed_layer_mapping(project: Project) -> None:
    pending = [
        layer.source_name
        for layer in project.layers
        if layer.mapping_review_required and not layer.mapping_confirmed
    ]
    if pending:
        raise DomainFailure(
            FailureReason.MAPPING_CONFIRMATION_REQUIRED, {"layer_names": pending}
        )


def pending_native_layer_mapping(project: Project) -> bool:
    """Определяет маршрут проверки, не подтверждая готовность физического снимка."""
    return bool(
        project.import_status.mode == ImportMode.AUTOCAD_LIVE
        and project.source_file is not None
        and project.source_file.native_session is not None
        and any(
            layer.mapping_review_required and not layer.mapping_confirmed
            for layer in project.layers
        )
    )


def require_confirmed_site_boundary(project: Project) -> frozenset[str]:
    """Проверяет выбор границы по метаданным; её площадь проверяет prepared-движок."""
    selected = [
        layer for layer in project.layers if layer.mapped_kind == LayerKind.SITE_BORDER
    ]
    pending = [layer.source_name for layer in selected if not layer.mapping_confirmed]
    if pending:
        raise DomainFailure(
            FailureReason.MAPPING_CONFIRMATION_REQUIRED, {"layer_names": pending}
        )
    invalid = [
        layer.source_name
        for layer in selected
        if layer.boundary_candidate is not None
        and layer.boundary_candidate.status != BoundaryCandidateStatus.USABLE
    ]
    if not selected or invalid:
        raise DomainFailure(
            FailureReason.LIVE_BOUNDARY_UNUSABLE, {"layer_names": invalid}
        )
    return frozenset(layer.source_name for layer in selected)


def require_usable_site_boundary(
    project: Project, *, source_features: list[dict[str, Any]] | None = None
) -> None:
    """Одна роль слоя не превращает live CAD-линию в расчётную территорию."""
    if project.import_status.mode != ImportMode.AUTOCAD_LIVE:
        return
    selected = [
        layer for layer in project.layers if layer.mapped_kind == LayerKind.SITE_BORDER
    ]
    if not selected:
        return  # Ручная рабочая зона допускается и без слоя внешней границы территории.
    invalid = [
        layer
        for layer in selected
        if (
            layer.boundary_candidate is not None
            and layer.boundary_candidate.status != BoundaryCandidateStatus.USABLE
        )
    ]
    if not invalid:
        source = project.source_geometry or project.geometry
        if source_features is not None:
            features = source_features
        else:
            features = source.feature_collection.get("features", []) if source else []
        expected = {layer.source_name for layer in selected}
        native_features: dict[str, list[dict[str, Any]]] = {
            name: [] for name in expected
        }
        for feature in features:
            props = feature.get("properties", {})
            name = props.get("source_layer")
            if (
                name in expected
                and props.get("source_native_geometry")
                and props.get("source_geometry_content_sha256")
            ):
                native_features[name].append(feature)
        native_surfaces = {
            name
            for name, items in native_features.items()
            if any(
                item.get("geometry", {}).get("type") in {"Polygon", "MultiPolygon"}
                for item in items
            )
        }
        invalid = [
            layer for layer in selected if layer.source_name not in native_surfaces
        ]
        # Безымянный слой может содержать область AutoCAD. Одна назначенная роль
        # не превращает исходные линии в расчётную поверхность.
        if not invalid:
            for layer in selected:
                if layer.boundary_candidate is not None:
                    continue
                usable = False
                for feature in native_features[layer.source_name]:
                    if feature.get("geometry", {}).get("type") not in {
                        "Polygon",
                        "MultiPolygon",
                    }:
                        continue
                    try:
                        area = shape(feature["geometry"])
                        usable = (
                            not area.is_empty
                            and area.is_valid
                            and area.area > 0
                            and area.buffer(-1.5).area > 0
                        )
                    except (GEOSException, KeyError, TypeError, ValueError):
                        pass
                    if usable:
                        break
                if not usable:
                    invalid.append(layer)
    if invalid:
        raise DomainFailure(
            FailureReason.LIVE_BOUNDARY_UNUSABLE,
            {"layer_names": [layer.source_name for layer in invalid]},
        )
