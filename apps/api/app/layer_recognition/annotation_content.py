"""Локальное распознавание известных CAD-аннотаций по составу сущностей без зависимости
от уверенности модели.
"""

from collections.abc import Mapping

# Native-захват распознаёт наследников AcDbDimension как аннотации.
# Перечисляем публичные классы без доверия произвольному суффиксу имени.
ANNOTATION_ENTITY_TYPES = frozenset(
    {
        "TEXT",
        "MTEXT",
        "ATTRIB",
        "ATTDEF",
        "DIMENSION",
        "LEADER",
        "MLEADER",
        "AUTOCAD:AcDbMLeader",
        "AUTOCAD:AcDbAlignedDimension",
        "AUTOCAD:AcDbRotatedDimension",
        "AUTOCAD:AcDbRadialDimension",
        "AUTOCAD:AcDbDiametricDimension",
        "AUTOCAD:AcDb2LineAngularDimension",
        "AUTOCAD:AcDb3PointAngularDimension",
        "AUTOCAD:AcDbOrdinateDimension",
        "AUTOCAD:AcDbArcDimension",
        "AUTOCAD:AcDbRadialDimensionLarge",
    }
)


def annotation_only(entity_types: Mapping[str, int]) -> bool:
    return bool(entity_types) and all(
        kind in ANNOTATION_ENTITY_TYPES and count > 0
        for kind, count in entity_types.items()
    )
