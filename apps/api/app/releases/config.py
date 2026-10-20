"""Версии и форматы выпуска: воспроизводимый ZIP и параметры обзорного SVG.

Эти значения определяют представление артефактов, а не расстояния проверки
или биологические прогнозы. Изменение версии влияет на идентичность выпуска.
"""

RULE_SET_REVISION = "green-atlas-spatial-draft@2026-08-28.1"
SPECIES_CATALOG_REVISION = "green-atlas-species@2026-08-28.1"
RELEASE_DEFAULT_STEM = "green-atlas"
FIXED_ZIP_TIME = (2026, 8, 28, 0, 0, 0)
ZIP_COMPRESSION_LEVEL = 9
ZIP_FILE_ATTRIBUTES = 0o100644 << 16

SVG_EMPTY_ORIGIN = 0.0
SVG_EMPTY_EXTENT = 10.0
SVG_MIN_EXTENT = 1.0
SVG_MIN_PADDING = 2.0
SVG_PADDING_RATIO = 0.02
SVG_VIEWBOX_FORMAT = ".3f"
SVG_COORDINATE_FORMAT = ".6f"
SVG_OUTLINE_COLOR = "#667085"
SVG_TREE_COLOR = "#16794c"
SVG_SHRUB_COLOR = "#568f42"
SVG_STROKE_WIDTH = "0.12"
SVG_CANOPY_OPACITY = "0.16"
SVG_CENTER_RADIUS = "0.18"
