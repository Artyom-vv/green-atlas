"""Budgets for one exact workpackage, separate from native conversion."""

from dataclasses import dataclass
from math import isfinite

from app.cad_import.policy import ConversionPolicy
from app.dxf_import.limits import MAX_DXF_CONTENT_BYTES


@dataclass(frozen=True)
class AoiPolicy(ConversionPolicy):
    max_output_bytes: int = MAX_DXF_CONTENT_BYTES
    max_converted_source_bytes: int = 512 * 1024 * 1024
    max_visited_entities: int = 1_000_000
    max_selected_entities: int = 100_000
    boundary_sagitta_m: float = 0.01
    max_boundary_vertices: int = 20_000

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isfinite(self.boundary_sagitta_m):
            raise ValueError("AOI boundary tolerance must be finite")
        if (
            min(
                self.max_converted_source_bytes,
                self.max_visited_entities,
                self.max_selected_entities,
                self.boundary_sagitta_m,
                self.max_boundary_vertices,
            )
            <= 0
        ):
            raise ValueError("AOI budgets must be positive")
