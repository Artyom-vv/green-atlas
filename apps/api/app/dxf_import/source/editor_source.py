"""Подготовка редактируемого исходника с адресными замечаниями без выдумывания
расчётного свободного пространства.
"""

from collections.abc import Callable, Iterable
from typing import Any

from app.dxf_import.contracts import ImportEditability, ImportMode
from app.dxf_import.layers.utility_mapping import (
    assign_utility_context,
    utility_contexts_by_layer,
)
from app.dxf_import.source.admission import require_calculation_source
from app.dxf_import.source.review_contracts import SourceReview, SourceReviewIssue
from app.projects.contracts import Project


def is_native_source_editor(project: Project) -> bool:
    """Карта native-исходника, чьи расчётные ограничения ещё не подготовлены."""
    return bool(
        project.import_status.mode == ImportMode.AUTOCAD_LIVE
        and project.source_file is not None
        and project.source_file.native_session is not None
        and project.source_review is not None
        and project.geometry is not None
        and project.source_geometry is None
    )


def apply_source_roles(project: Project, features: Iterable[dict[str, Any]]) -> None:
    """Наложить текущие назначения на принадлежащие читателю объекты CAD.

    Исходная геометрия и атрибуты остаются неизменяемыми в хранилище. Роль и
    характеристики сети принадлежат назначению слоя текущей ревизии проекта.
    """
    make_source_role_applier(project)(features)


def make_source_role_applier(
    project: Project,
) -> Callable[[Iterable[dict[str, Any]]], None]:
    """Подготовить назначения одной ревизии для выборочного чтения записей.

    Дисковый reader выдаёт множество небольших пачек. Каталог слоёв и контексты
    сети принадлежат снимку запроса и не пересобираются для каждого объекта.
    """
    roles = {layer.source_name: layer.mapped_kind for layer in project.layers}
    utilities = utility_contexts_by_layer(project.layers)

    def apply(features: Iterable[dict[str, Any]]) -> None:
        for feature in features:
            properties = feature.setdefault("properties", {})
            role = roles.get(properties.get("source_layer", ""))
            if role is not None and not properties.get("source_context_only"):
                properties["kind"] = role.value
            assign_utility_context(properties, utilities)

    return apply


def source_editor_review(project: Project) -> SourceReview:
    """Адресные замечания редактора не требуют повторного чтения геометрии."""
    issues = [
        SourceReviewIssue(
            code="calculation_pending",
            message="Редактор открыт. Пространственные ограничения ещё не рассчитаны; посадки сохраняются как непроверенный проект.",
            suggested_action="Уточните назначение слоёв и выполните расчёт ограничений",
        )
    ]
    issues.extend(
        SourceReviewIssue(
            code="incomplete_layer",
            layer_id=layer.id,
            source_layer=layer.source_name,
            message="Часть объектов слоя сохранена в исходном DXF, но не представлена расчётной геометрией.",
            suggested_action="Проверьте типы объектов и назначение слоя; подготовьте подтверждённую расчётную геометрию",
        )
        for layer in project.layers
        if not layer.geometry_complete
    )
    if project.source_file is not None:
        issues.extend(
            SourceReviewIssue(
                code="source_warning",
                message=warning,
                suggested_action="Проверьте сведения об исходных данных",
            )
            for warning in project.source_file.warnings
        )
    return SourceReview(issues=issues)


def open_source_editor(project: Project, *, consume_owned: bool = False) -> Project:
    """Перенести исходник в карту, сохраняя изоляцию внешнего владельца по умолчанию.

    Только новый файловый импорт передаёт исключительное владение своими
    объектами: их роли можно обновить без второй коллекции словарей метаданных.
    """
    require_calculation_source(project.source_file)
    if project.import_status.editability != ImportEditability.EDITABLE:
        raise ValueError(project.import_status.message)
    if project.map_ready and project.geometry is not None:
        return project
    source = project.source_geometry
    if source is None:
        raise ValueError("Полная геометрия исходника недоступна для редактора")
    features = source.feature_collection.get("features", [])
    if not consume_owned:
        features = [
            {**feature, "properties": dict(feature.get("properties", {}))}
            for feature in features
        ]
    apply_source_roles(project, features)
    # Неизменяемый BLOB исходника остаётся в репозитории. Сохраняем один полный
    # граф карты; расчёт может использовать его после явного переназначения.
    return project.model_copy(
        update={
            "geometry": source.model_copy(
                update={
                    "feature_collection": {
                        "type": "FeatureCollection",
                        "features": features,
                    },
                    "site_area_m2": None,
                    "planning_area_m2": None,
                    "allowed_area_m2": None,
                }
            ),
            "source_geometry": None,
            "source_review": source_editor_review(project),
            "map_ready": True,
            "geometry_version": project.geometry_version + 1,
            "site_area_m2": None,
            "planning_area_m2": None,
            "allowed_area_m2": None,
        }
    )
