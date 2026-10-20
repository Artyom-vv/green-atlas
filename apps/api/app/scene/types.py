"""Общий словарь происхождения и готовности данных сцены для расчётных проекций и
публичных DTO.
"""

from typing import Literal

EvidenceStatus = Literal["confirmed", "estimated", "missing"]
GrowthStage = Literal["planting", "young", "developing", "mature"]
BaseElevationSource = Literal["dxf_elevation", "copernicus_dem_glo90"]
HeightSource = Literal["dxf_extrusion", "dxf_attribute", "osm_height", "osm_levels"]
GeoreferenceStatus = Literal["confirmed", "declared", "local", "missing"]
GeometrySource = Literal["prepared_geometry", "source_geometry", "missing"]
