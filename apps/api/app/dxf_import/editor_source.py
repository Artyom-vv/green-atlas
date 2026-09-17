"""Compose an editable source draft without inventing calculated free space."""

from app.dxf_import.admission import require_calculation_source
from app.dxf_import.contracts import ImportEditability
from app.dxf_import.review_contracts import SourceReview, SourceReviewIssue
from app.dxf_import.utility_mapping import (
    assign_utility_context,
    utility_contexts_by_layer,
)
from app.projects.contracts import Project


def open_source_editor(project: Project) -> Project:
    require_calculation_source(project.source_file)
    if project.import_status.editability != ImportEditability.EDITABLE:
        raise ValueError(project.import_status.message)
    if project.map_ready and project.geometry is not None:
        return project
    source = project.source_geometry
    if source is None:
        raise ValueError("Полная геометрия исходника недоступна для редактора")
    issues = [SourceReviewIssue(
        code="calculation_pending",
        message="Редактор открыт. Пространственные ограничения ещё не рассчитаны; посадки сохраняются как непроверенный проект.",
        suggested_action="Уточните назначение слоёв и выполните расчёт ограничений",
    )]
    issues.extend(SourceReviewIssue(
        code="incomplete_layer", layer_id=layer.id, source_layer=layer.source_name,
        message="Часть объектов слоя сохранена в исходном DXF, но не представлена расчётной геометрией.",
        suggested_action="Проверьте типы объектов и назначение слоя; подготовьте подтверждённую расчётную геометрию",
    ) for layer in project.layers if not layer.geometry_complete)
    if project.source_file is not None:
        issues.extend(SourceReviewIssue(
            code="source_warning", message=warning,
            suggested_action="Проверьте сведения об исходных данных",
        ) for warning in project.source_file.warnings)
    roles = {layer.source_name: layer.mapped_kind for layer in project.layers}
    utilities = utility_contexts_by_layer(project.layers)
    features = []
    for feature in source.feature_collection.get("features", []):
        properties = dict(feature.get("properties", {}))
        role = roles.get(properties.get("source_layer", ""))
        if role is not None and not properties.get("source_context_only"):
            properties["kind"] = role.value
        assign_utility_context(properties, utilities)
        features.append({**feature, "properties": properties})
    # The immutable source BLOB remains in the repository. Keep only one full
    # normalized map graph; calculate may reuse it after explicit remapping.
    return project.model_copy(update={
        "geometry": source.model_copy(update={
            "feature_collection": {"type": "FeatureCollection", "features": features},
            "site_area_m2": None, "planning_area_m2": None, "allowed_area_m2": None,
        }),
        "source_geometry": None,
        "source_review": SourceReview(issues=issues),
        "map_ready": True,
        "geometry_version": project.geometry_version + 1,
        "site_area_m2": None, "planning_area_m2": None, "allowed_area_m2": None,
    })
