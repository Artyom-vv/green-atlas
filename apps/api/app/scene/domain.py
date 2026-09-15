"""Pure scene assembly from a project snapshot and a species lookup."""

from collections.abc import Callable

from app.projects.contracts import Project
from app.scene.config import MAX_HORIZON_YEAR, MIN_HORIZON_YEAR
from app.scene.context import scene_context
from app.scene.contracts import SceneEvidence, SceneSnapshot
from app.scene.plants import scene_origin, scene_plants
from app.scene.types import EvidenceStatus, GeoreferenceStatus
from app.species.contracts import SpeciesRevision

GEOREFERENCE_STATUSES: dict[str, GeoreferenceStatus] = {
    "verified": "confirmed",
    "declared": "declared",
    "local": "local",
    "unknown": "missing",
}


def validate_horizon(horizon_year: int) -> None:
    if not MIN_HORIZON_YEAR <= horizon_year <= MAX_HORIZON_YEAR:
        raise ValueError("Горизонт сцены должен быть от 0 до 40 лет")


def build_scene(
    project: Project, horizon_year: int, *, species: Callable[[str], SpeciesRevision]
) -> SceneSnapshot:
    validate_horizon(horizon_year)
    origin_x, origin_y = scene_origin(project)
    scene_objects = scene_plants(
        project, horizon_year, origin_x, origin_y, species=species
    )
    context = scene_context(project, origin_x, origin_y)
    building_heights_status: EvidenceStatus = (
        "confirmed"
        if context.building_count > 0
        and context.confirmed_heights == context.building_count
        else "estimated"
        if context.confirmed_heights + context.estimated_heights > 0
        else "missing"
    )
    known_building_height_count = context.confirmed_heights + context.estimated_heights
    confirmed_terrain = [
        primitive
        for primitive in context.primitives
        if primitive.terrain_mapping_status == "confirmed"
        and primitive.primitive_type == "surface_mesh"
    ]
    # ``confirmed`` above means only that the DXF document explicitly
    # mapped these faces to terrain. Measurement confidence is separate
    # and must come from the source declaration, never its display name.
    terrain_is_estimated = any(
        primitive.terrain_confidence == "estimated" for primitive in confirmed_terrain
    )
    data_gaps = ["Точные модели пород", "Инсоляция"]
    if not confirmed_terrain:
        data_gaps.insert(0, "Рельеф")
    elif terrain_is_estimated:
        data_gaps.insert(0, "Инженерные отметки рельефа")
    if context.building_count == 0 or known_building_height_count == 0:
        data_gaps.append("Высоты зданий")
    elif known_building_height_count < context.building_count:
        data_gaps.append(
            f"Высоты зданий: известно {known_building_height_count} из {context.building_count} видимых объектов"
        )
    georeference_status = GEOREFERENCE_STATUSES[project.coordinate_reference.status]
    georeference_evidence = SceneEvidence(
        status=(
            "confirmed"
            if project.coordinate_reference.status == "verified"
            else "estimated"
            if project.coordinate_reference.status == "declared"
            else "missing"
        ),
        coverage=(
            "full"
            if project.coordinate_reference.status in {"verified", "declared"}
            else "none"
        ),
        source=(
            project.coordinate_reference.source
            if project.coordinate_reference.source != "none"
            else None
        ),
        note=project.coordinate_reference.evidence,
    )
    terrain_evidence = SceneEvidence(
        status="estimated"
        if terrain_is_estimated
        else "confirmed"
        if confirmed_terrain
        else "missing",
        coverage="partial" if confirmed_terrain else "none",
        source=confirmed_terrain[0].source_dataset if confirmed_terrain else None,
        note=(
            f"Поверхностей DXF с доказанным назначением рельефа: {len(confirmed_terrain)}; "
            + (
                "Copernicus GLO-90 — DSM с шагом около 90 м, поэтому поверхность пригодна для визуального контекста, но не для инженерных отметок; "
                if terrain_is_estimated
                else ""
            )
            + f"вертикальный datum: {confirmed_terrain[0].vertical_datum}, "
            f"смещение {confirmed_terrain[0].vertical_datum_offset_m} м над уровнем моря."
            if confirmed_terrain
            else "DXF XYZ сохранён как пространственный контекст, но ни одна поверхность явно не назначена рельефом."
        ),
    )
    if known_building_height_count == 0:
        building_height_evidence = SceneEvidence(
            status="missing",
            coverage="none",
            source=None,
            note="В видимых контурах зданий нет явной вертикальной экструзии или атрибута высоты в метрах.",
        )
    else:
        building_height_evidence = SceneEvidence(
            status="confirmed" if context.estimated_heights == 0 else "estimated",
            coverage="full"
            if known_building_height_count == context.building_count
            else "partial",
            source="+".join(sorted(context.height_sources)) or None,
            note=(
                f"Высота подтверждена у {context.confirmed_heights}, оценена по этажности у "
                f"{context.estimated_heights} из {context.building_count} видимых контуров; "
                "остальные остаются плоскими."
            ),
        )
    return SceneSnapshot(
        plan_version=project.plan.version if project.plan else 1,
        horizon_year=horizon_year,
        coordinate_origin=[round(origin_x, 6), round(origin_y, 6)],
        coordinate_reference=project.coordinate_reference,
        georeference_status=georeference_status,
        georeference_evidence=georeference_evidence,
        geometry_source=context.geometry_source,
        geometry_source_file_name=project.source_file.name
        if project.source_file
        else None,
        terrain_status="estimated"
        if terrain_is_estimated
        else "confirmed"
        if confirmed_terrain
        else "missing",
        terrain_elevation_m=None,
        terrain_evidence=terrain_evidence,
        building_heights_status=building_heights_status,
        building_height_evidence=building_height_evidence,
        building_feature_count=context.building_count,
        building_height_confirmed_count=context.confirmed_heights,
        note=(
            "Сцена собрана из плана и геометрии DXF. "
            + (
                "Рельеф показан по оценочной DSM-поверхности; "
                if terrain_is_estimated
                else "Рельеф не известен; "
                if not confirmed_terrain
                else "Рельеф подтверждён DXF; "
            )
            + "неподтверждённые высоты зданий не экструдируются. "
            "Размеры посадок — сценарный прогноз, а не геодезическое измерение."
        ),
        data_gaps=data_gaps,
        objects=scene_objects,
        context_features=context.features,
        vertical_primitives=context.primitives,
    )
