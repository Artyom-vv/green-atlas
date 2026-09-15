"""Conservative inclusion of complete world-space geometries, without clipping."""

from collections import OrderedDict
from dataclasses import dataclass, field
from math import isfinite

from ezdxf import bbox
from ezdxf.entities import DXFEntity, Insert, Line
from ezdxf.math import BoundingBox, Matrix44, Vec3
from shapely.geometry import Polygon, box

# These types do not have reliable finite bounds in ezdxf's disassembler.
UNKNOWN_BOUND_TYPES = frozenset(
    {
        "ACAD_TABLE",
        "ACAD_PROXY_ENTITY",
        "BODY",
        "3DSOLID",
        "REGION",
        "RAY",
        "XLINE",
        "IMAGE",
        "PDFUNDERLAY",
        "DWFUNDERLAY",
        "DGNUNDERLAY",
        "TEXT",
        "MTEXT",
        "ATTRIB",
        "ATTDEF",
        "DIMENSION",
        "MLEADER",
        "MULTILEADER",
        "SHAPE",
    }
)
MAX_BOUND_DEPTH = 16
BOUND_ROUNDING_MARGIN = 0.01
MAX_CACHED_LEAF_BOUNDS = 10_000


def uncertain_dependencies(
    entity: DXFEntity, ancestry: frozenset[str] = frozenset()
) -> bool:
    if entity.dxftype() in UNKNOWN_BOUND_TYPES:
        return True
    if not isinstance(entity, Insert):
        return False
    name = entity.dxf.name
    block = entity.block()
    if block is None or name in ancestry or len(ancestry) >= MAX_BOUND_DEPTH:
        return True
    if block.block is None or block.block.is_xref:
        return True
    return any(uncertain_dependencies(child, ancestry | {name}) for child in block)


@dataclass
class AoiSelection:
    mask: Polygon
    influence_distance: float = 0
    selected: list[DXFEntity] = field(default_factory=list)
    excluded: int = 0
    unknown: int = 0
    warnings: list[str] = field(default_factory=list)
    bounds_cache: OrderedDict[str, BoundingBox] = field(default_factory=OrderedDict)
    bounds_requests: int = 0
    cache_hits: int = 0
    rectangle_rejects: int = 0
    mask_bounds: tuple[float, float, float, float] = field(init=False)

    def __post_init__(self) -> None:
        self.mask_bounds = self.mask.bounds

    def accepts(self, entity: DXFEntity, matrix: Matrix44) -> bool:
        uncertain = uncertain_dependencies(entity)
        bounds = None
        if not uncertain:
            self.bounds_requests += 1
            try:
                handle = str(entity.dxf.handle)
                bounds = self.bounds_cache.get(handle)
                if bounds is None:
                    # LINE endpoints are defined in WCS. They are its exact
                    # bounds; disassembling a path adds no geometric information.
                    bounds = (
                        BoundingBox([entity.dxf.start, entity.dxf.end])
                        if isinstance(entity, Line)
                        else bbox.extents([entity], fast=True)
                    )
                    self.bounds_cache[handle] = bounds
                    if len(self.bounds_cache) > MAX_CACHED_LEAF_BOUNDS:
                        self.bounds_cache.popitem(last=False)
                else:
                    self.cache_hits += 1
                    self.bounds_cache.move_to_end(handle)
            except Exception:
                uncertain = True
        if uncertain or bounds is None or not bounds.has_data:
            return self.unknown_extent(entity)
        if not all(isfinite(value) for point in bounds for value in point):
            return self.unknown_extent(entity)
        # Expand in original coordinates before transforming the box: scales and
        # nested inserts must scale the rounding margin along with the geometry.
        margin = Vec3(
            BOUND_ROUNDING_MARGIN, BOUND_ROUNDING_MARGIN, BOUND_ROUNDING_MARGIN
        )
        local = BoundingBox([bounds.extmin - margin, bounds.extmax + margin])
        world = BoundingBox(matrix.transform_vertices(local.cube_vertices()))
        if not world.has_data or not all(
            isfinite(value) for point in world for value in point
        ):
            return self.unknown_extent(entity)
        min_x, min_y, max_x, max_y = self.mask_bounds
        distance = self.influence_distance
        if (
            world.extmax.x < min_x - distance
            or world.extmax.y < min_y - distance
            or world.extmin.x > max_x + distance
            or world.extmin.y > max_y + distance
        ):
            self.rectangle_rejects += 1
            self.excluded += 1
            return False
        candidate = box(
            world.extmin.x,
            world.extmin.y,
            world.extmax.x,
            world.extmax.y,
        )
        # Distance to the conservative rectangle avoids an inscribed polygonal
        # buffer dropping geometry at the exact curved influence boundary.
        if self.mask.distance(candidate) <= self.influence_distance:
            return True
        self.excluded += 1
        return False

    def unknown_extent(self, entity: DXFEntity) -> bool:
        self.unknown += 1
        handle = (entity.origin_of_copy or entity).dxf.handle
        self.warnings.append(
            f"Границы {entity.dxftype()} #{handle} не подтверждены; пространственный фильтр не применяется"
        )
        return True
