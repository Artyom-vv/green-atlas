from math import isfinite

from ezdxf import bbox
from ezdxf.document import Drawing
from ezdxf.entities import LWPolyline

from app.cad_import.aoi_policy import AoiPolicy
from app.cad_import.boundary_contracts import (
    DrawingBoundaryCandidate,
    DrawingBoundaryCatalog,
)
from app.dxf_import.units import meters_per_dxf_unit

MAX_BOUNDARY_CANDIDATES = 500


def boundary_catalog(document: Drawing) -> DrawingBoundaryCatalog:
    candidates: list[DrawingBoundaryCandidate] = []
    total = 0
    factor = meters_per_dxf_unit(document.units)
    vertex_budget = AoiPolicy().max_boundary_vertices
    for entity in document.modelspace():
        if not isinstance(entity, LWPolyline) or not entity.closed:
            continue
        total += 1
        if len(candidates) >= MAX_BOUNDARY_CANDIDATES:
            continue
        bounds = None
        reason = None
        if factor is None:
            reason = "Единицы чертежа не заданы явно"
        elif not 3 <= len(entity) <= vertex_budget:
            reason = "Число вершин вне бюджета подготовки контура"
        else:
            try:
                extent = bbox.extents([entity], fast=False)
                if extent.has_data:
                    values = (
                        extent.extmin.x * factor,
                        extent.extmin.y * factor,
                        extent.extmax.x * factor,
                        extent.extmax.y * factor,
                    )
                    if (
                        all(isfinite(value) for value in values)
                        and values[0] < values[2]
                        and values[1] < values[3]
                    ):
                        bounds = values
                if bounds is None:
                    reason = "Границы контура не определены"
            except (ValueError, TypeError, ArithmeticError):
                reason = "Не удалось прочитать границы контура"
        candidates.append(
            DrawingBoundaryCandidate(
                handle=entity.dxf.handle,
                layer=entity.dxf.layer,
                vertex_count=len(entity),
                has_bulges=entity.has_arc,
                bounds_m=bounds,
                available_for_preview=reason is None,
                reason=reason,
            )
        )
    return DrawingBoundaryCatalog(
        candidates=candidates,
        total_candidates=total,
        truncated=total > len(candidates),
    )
