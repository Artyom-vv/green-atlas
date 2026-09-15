"""Use an explicit authored contour; never promote an extent to a site."""

from dataclasses import dataclass

from ezdxf.document import Drawing
from ezdxf.entities import LWPolyline
from shapely.geometry import Polygon

from app.cad_import.aoi_arcs import boundary_vertices
from app.cad_import.aoi_contracts import AoiBoundaryEvidence
from app.cad_import.aoi_policy import AoiPolicy
from app.cad_import.contracts import CadConversionError
from app.dxf_import.units import meters_per_dxf_unit

COORDINATE_ROUNDING_MARGIN_M = 0.000001


@dataclass(frozen=True)
class AoiBoundary:
    entity: LWPolyline
    polygon: Polygon
    scale_to_source: float
    area_m2: float
    approximation: AoiBoundaryEvidence


def load_boundary(
    document: Drawing, handle: str, source_units: int, policy: AoiPolicy | None = None
) -> AoiBoundary:
    policy = policy or AoiPolicy()
    entity = document.entitydb.get(handle)
    if (
        not isinstance(entity, LWPolyline)
        or not entity.closed
        or entity.dxf.owner != document.modelspace().block_record_handle
    ):
        raise CadConversionError(
            "Для рабочей территории нужна выбранная замкнутая LWPOLYLINE в пространстве модели"
        )
    boundary_factor = meters_per_dxf_unit(document.units)
    source_factor = meters_per_dxf_unit(source_units)
    if boundary_factor is None or source_factor is None:
        raise CadConversionError("Единицы исходника и границы должны быть заданы явно")
    vertices, curved = boundary_vertices(
        entity, boundary_factor, policy.boundary_sagitta_m, policy.max_boundary_vertices
    )
    if (
        len(vertices) < 3
        or max(v.z for v in vertices) - min(v.z for v in vertices) > 1e-8
    ):
        raise CadConversionError("Граница должна быть плоским горизонтальным контуром")
    scale = boundary_factor / source_factor
    polygon = Polygon([(v.x * scale, v.y * scale) for v in vertices])
    if not polygon.is_valid or polygon.is_empty or polygon.area <= 0:
        raise CadConversionError("Выбранная граница не образует корректный участок")
    return AoiBoundary(
        entity,
        polygon,
        scale,
        polygon.area * source_factor**2,
        AoiBoundaryEvidence(
            method="bulge_arc_sagitta" if curved else "straight_segments",
            sagitta_tolerance_m=policy.boundary_sagitta_m if curved else 0,
            outward_margin_m=(policy.boundary_sagitta_m if curved else 0)
            + COORDINATE_ROUNDING_MARGIN_M,
            original_vertices=len(entity),
            mask_vertices=len(vertices),
            vertex_budget=policy.max_boundary_vertices,
        ),
    )
