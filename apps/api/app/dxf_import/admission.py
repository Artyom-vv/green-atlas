"""A derived drawing retains its read-only gate across all import paths."""

from shapely.errors import GEOSException
from shapely.geometry import shape

from app.dxf_import.contracts import (
    ImportEditability,
    ImportMode,
    ImportStatus,
    SourceFile,
)
from app.dxf_import.layer_contracts import BoundaryCandidateStatus, LayerKind
from app.dxf_import.preview_contracts import CAD_PREVIEW_MESSAGE
from app.projects.contracts import Project


def cad_preview_status() -> ImportStatus:
    return ImportStatus(
        mode=ImportMode.CAD_PREVIEW,
        editability=ImportEditability.READ_ONLY,
        message=CAD_PREVIEW_MESSAGE,
    )


def require_calculation_source(source: SourceFile | None) -> None:
    if source is not None and source.preview_provenance is not None:
        raise ValueError(CAD_PREVIEW_MESSAGE)


def partial_geometry_accepted(source: SourceFile | None) -> bool:
    if source is None:
        return False
    provenance = source.prepared_provenance
    return bool(source.accept_partial_geometry or (
        provenance and provenance.opening_review.accept_partial_geometry
    ))


def require_confirmed_layer_mapping(project: Project) -> None:
    pending = [
        layer.source_name
        for layer in project.layers
        if layer.mapping_review_required
        and not layer.mapping_confirmed
    ]
    if pending:
        raise ValueError(
            "Подтвердите предложенные роли слоёв: " + ", ".join(pending)
        )


def require_usable_site_boundary(project: Project) -> None:
    """A live CAD line cannot become the calculated territory by role alone."""
    if project.import_status.mode != ImportMode.AUTOCAD_LIVE:
        return
    selected = [
        layer for layer in project.layers
        if layer.mapped_kind == LayerKind.SITE_BORDER
    ]
    if not selected:
        return  # A manually drawn work zone may be used without a site layer.
    invalid = [layer for layer in selected if (
        layer.boundary_candidate is not None
        and layer.boundary_candidate.status != BoundaryCandidateStatus.USABLE
    )]
    if not invalid:
        source = project.source_geometry or project.geometry
        features = source.feature_collection.get("features", []) if source else []
        expected = {layer.source_name for layer in selected}
        native_features = {name: [] for name in expected}
        for feature in features:
            props = feature.get("properties", {})
            name = props.get("source_layer")
            if (name in expected and props.get("source_native_geometry")
                and props.get("source_geometry_content_sha256")):
                native_features[name].append(feature)
        native_surfaces = {
            name for name, items in native_features.items()
            if any(item.get("geometry", {}).get("type") in {"Polygon", "MultiPolygon"}
                   for item in items)
        }
        invalid = [layer for layer in selected if layer.source_name not in native_surfaces]
        # An unnamed layer may still contain an AutoCAD area. A role alone
        # cannot turn its linework into a surface.
        if not invalid:
            for layer in selected:
                if layer.boundary_candidate is not None:
                    continue
                usable = False
                for feature in native_features[layer.source_name]:
                    if feature.get("geometry", {}).get("type") not in {"Polygon", "MultiPolygon"}:
                        continue
                    try:
                        area = shape(feature["geometry"])
                        usable = (not area.is_empty and area.is_valid
                                  and area.area > 0 and area.buffer(-1.5).area > 0)
                    except (GEOSException, KeyError, TypeError, ValueError):
                        pass
                    if usable:
                        break
                if not usable:
                    invalid.append(layer)
    if invalid:
        names = ", ".join(layer.source_name for layer in invalid)
        raise ValueError(
            "Граница территории не образует пригодную площадь: " + names
            + ". Выберите замкнутый контур в разделе «Территория»."
        )
