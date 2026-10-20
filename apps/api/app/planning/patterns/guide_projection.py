"""Направляющие схем из проверенного отображения без полной коллекции CAD."""

from app.planning.patterns.contracts import PlacementMaskRequest
from app.planning.rules import mask_guide_geometries
from app.projects.concurrency import ProjectVersionConflict
from app.projects.contracts import Project
from app.projects.ports import ProjectDisplayGeometryReader, ProjectReader


def read_mask_guides(
    repository: ProjectReader,
    project: Project,
    request: PlacementMaskRequest,
) -> list[dict]:
    """Прочитать нужные виды, сохраняя координаты и исходный порядок объектов.

    Направляющие не обрезаются: начало отсчёта шага определяется полной кривой.
    Старый генератор проверяет геометрию всех дорог и зданий до пересечения
    с контекстом зоны; неподдерживаемая направляющая сохраняет прежний отказ.
    """
    if request.mask_id not in {"road_edges", "building_screen", "building_contour"}:
        return []
    if (
        project.source_file is None
        or project.source_file.native_session is None
        or not isinstance(repository, ProjectDisplayGeometryReader)
        or project.geometry is None
    ):
        return mask_guide_geometries(project, request)
    with repository.open_display_geometry(project.id) as opened:
        if opened is None:
            # Inline и прежние reader сохраняют совместимый полный путь.
            return mask_guide_geometries(repository.get(project.id), request)
        view, resource = opened
        current = repository.get(project.id, lightweight=True)
        if (
            current.state_version != project.state_version
            or view.geometry_version != project.geometry_version
        ):
            raise ProjectVersionConflict(
                project.id, project.state_version, current.state_version
            )
        if request.mask_id == "road_edges":
            kinds = {"road"}
        else:
            kinds = {"building", "road"}
        selected_features = [
            resource.feature(index) for index in resource.iter_kind_indexes(kinds)
        ]
        projected = project.model_copy(
            update={
                "geometry": project.geometry.model_copy(
                    update={
                        "feature_collection": {
                            "type": "FeatureCollection",
                            "features": selected_features,
                        }
                    }
                )
            }
        )
        return mask_guide_geometries(projected, request)
