"""Общая политика повторного просмотра и исключений для новых и кешированных предложений
о слоях.
"""

from app.dxf_import.layers.categories import CATEGORIES, LayerCategory
from app.dxf_import.layers.contracts import Layer, LayerKind
from app.dxf_import.layers.recognition import (
    NATIVE_ADMISSION_ISSUE,
    NATIVE_CATALOG_CATEGORIES,
    LayerRecognition,
    LayerRoleProposal,
    NameLayerRecognition,
    is_electrical_cabinet_name,
)
from app.layer_recognition.annotation_content import annotation_only
from app.layer_recognition.features import NameHint, name_hint
from app.shared.messages import message

LOCAL_PROVIDER_NAME = "local-rules/1"


def local_layer_proposal(layer: Layer) -> LayerRoleProposal:
    """Использовать явное имя и точные native-классы; подписи не подтверждают
    физический объект по соседству.
    """
    proposal = NameLayerRecognition._proposal(layer)
    if (
        annotation_only(layer.entity_types)
        and sum(layer.entity_types.values()) == layer.object_count
    ):
        proposal.category = LayerCategory.ANNOTATION
        proposal.confidence = "high"
        proposal.evidence = ["Все объекты слоя являются подписями или размерами"]
        proposal.unresolved = (
            ["Подтверждение роли не восстанавливает пропущенную геометрию"]
            if not layer.geometry_complete
            else []
        )
    return _guard_exclusions(
        LayerRecognition(
            source_sha256=None,
            provider="local-rules",
            categories=[],
            proposals=[proposal],
        ),
        [layer],
    ).proposals[0]


def locally_resolved(layer: Layer, proposal: LayerRoleProposal) -> bool:
    # Явный выбор оператора остаётся назначением проекта, а не новым обучающим
    # ответом в кеше модели. Не заменяем им исходное предложение по признакам.
    rule = NameLayerRecognition._proposal(layer)
    explicit_boundary = (
        proposal.category
        in {LayerCategory.PROJECT_BOUNDARY, LayerCategory.SURFACE_BOUNDARY}
        and proposal.confidence == "high"
        and rule.category == proposal.category
        and rule.confidence == "high"
    )
    # Известный смешанный слой учитывается по объектам. Модель не может назначить
    # ему одну физическую роль; отсутствие запроса не подтверждает весь слой.
    physical_mixed = (
        proposal.category == rule.category == LayerCategory.MIXED_SOURCE
        and layer.object_count > 0
        and any(count > 0 for count in layer.projected_geometry_types.values())
        and not annotation_only(layer.entity_types)
    )
    # Распознавание названия границы не выбирает территорию и не подтверждает
    # замкнутость контура: эти решения остаются у подготовки и оператора.
    return (
        bool(layer.mapping_confirmed)
        or explicit_boundary
        or physical_mixed
        or not _needs_second_pass(proposal)
    )


def needs_model_refinement(layer: Layer, proposal: LayerRoleProposal) -> bool:
    """Повторный запрос нужен только там, где он может уточнить назначение.

    Первый проход сохраняется для всех слоёв. Снос уже учитывается как препятствие,
    а физический слой 0 остаётся смешанным по неизменяемой локальной проверке.
    """
    if locally_resolved(layer, proposal):
        return False
    if (
        proposal.category == LayerCategory.DEMOLITION_OBJECT
        and proposal.confidence != "low"
    ):
        return False
    forced_physical_mixed = (
        layer.object_count > 0
        and any(count > 0 for count in layer.projected_geometry_types.values())
        and not annotation_only(layer.entity_types)
        and NameLayerRecognition._proposal(layer).category == LayerCategory.MIXED_SOURCE
    )
    return not forced_physical_mixed


def _needs_second_pass(proposal: LayerRoleProposal) -> bool:
    if proposal.category is None:
        return True
    role = CATEGORIES[proposal.category][0]
    if role in {None, "site_border"}:
        return True
    if proposal.confidence == "medium" and role == "ignore":
        return True
    return (
        proposal.confidence == "low"
        and proposal.category != LayerCategory.UNSPECIFIED_NETWORK
    )


def _guard_exclusions(
    result: LayerRecognition, layers: list[Layer]
) -> LayerRecognition:
    """Уверенность модели сама по себе не превращает смешанную геометрию CAD в свободный
    грунт.
    """
    sources = {layer.id: layer for layer in layers}
    for proposal in result.proposals:
        source = sources.get(proposal.layer_id)
        if not source:
            continue
        annotations = annotation_only(source.entity_types)
        rule = NameLayerRecognition._proposal(source)
        # Новые предметы допускаются по словарю и точному составу захвата.
        # Ответ модели или старый кеш не заменяет native-проверку.
        if (
            proposal.category in NATIVE_CATALOG_CATEGORIES
            and proposal.category != rule.category
        ):
            proposal.category = None
            proposal.calculation_role = None
            proposal.confidence = "low"
            if (
                NATIVE_ADMISSION_ISSUE not in proposal.unresolved
                and len(proposal.unresolved) < 8
            ):
                proposal.unresolved.append(NATIVE_ADMISSION_ISSUE)
        if rule.category == LayerCategory.MIXED_SOURCE and not annotations:
            proposal.category = LayerCategory.MIXED_SOURCE
            proposal.calculation_role = None
            proposal.confidence = "low"
            for explanation in rule.unresolved:
                if (
                    explanation not in proposal.unresolved
                    and len(proposal.unresolved) < 8
                ):
                    proposal.unresolved.append(explanation)
        # Регуляционная линия не доказывает наличие трубы. Цвет и форма не
        # разрешают превращать такой неизвестный слой в инженерную сеть.
        name = source.source_name.rsplit("|", 1)[-1].lower().replace("ё", "е")
        if ("красные линии" in name or "red lines" in name) and (
            proposal.category is not None
            and CATEGORIES[proposal.category][0] == "utility"
            or proposal.calculation_role == "utility"
        ):
            proposal.category = None
            proposal.calculation_role = None
            proposal.confidence = "low"
            explanation = "Красные линии не определяют инженерную сеть; требуется роль регуляционной границы"
            if explanation not in proposal.unresolved and len(proposal.unresolved) < 8:
                proposal.unresolved.append(explanation)
        # Контур защитной конструкции не является осью сети. Слово «футляр»
        # без отдельного названия сети не назначает её расчётную роль. Явные
        # подтверждения оператора остаются в проекте и не подменяют кеш модели.
        if name_hint(source.source_name) == NameHint.CASING and (
            proposal.category is not None
            and CATEGORIES[proposal.category][0] == "utility"
            or proposal.calculation_role == "utility"
        ):
            proposal.category = None
            proposal.calculation_role = None
            proposal.confidence = "low"
            explanation = message("recognition_casing_role_unconfirmed")
            if explanation not in proposal.unresolved and len(proposal.unresolved) < 8:
                proposal.unresolved.append(explanation)
            proposal.review_question = message("recognition_casing_review_question")
            proposal.review_roles = [LayerKind.RESTRICTED, LayerKind.UTILITY]
        # Новая физическая категория не обходит native-допуск через модель
        # или старый кеш. Подтверждённые назначения проекта здесь не меняются.
        if (
            proposal.category == LayerCategory.PROTECTIVE_CASING
            and rule.category != LayerCategory.PROTECTIVE_CASING
        ):
            proposal.category = None
            proposal.calculation_role = None
            proposal.confidence = "low"
            explanation = message("recognition_casing_geometry_unconfirmed")
            if explanation not in proposal.unresolved and len(proposal.unresolved) < 8:
                proposal.unresolved.append(explanation)
            proposal.review_question = message("recognition_casing_review_question")
            proposal.review_roles = [LayerKind.RESTRICTED]
        # Щит не является сетью. Модель или кеш не заменяют native-допуск
        # авторского сокращения подтверждением физического состава.
        if is_electrical_cabinet_name(source.source_name) and (
            proposal.category is not None
            and CATEGORIES[proposal.category][0] == "utility"
            or proposal.calculation_role == "utility"
            or proposal.category == LayerCategory.EQUIPMENT
            and rule.category != LayerCategory.EQUIPMENT
        ):
            proposal.category = None
            proposal.calculation_role = None
            proposal.confidence = "low"
            explanation = message("recognition_cabinet_geometry_unconfirmed")
            if explanation not in proposal.unresolved and len(proposal.unresolved) < 8:
                proposal.unresolved.append(explanation)
            proposal.review_roles = [LayerKind.RESTRICTED]
        if not proposal.category:
            continue
        if (
            proposal.category == LayerCategory.ANNOTATION
            and proposal.confidence == "medium"
            and annotations
        ):
            proposal.confidence = "high"
            explanation = "Все объекты слоя являются подписями или размерами"
            if explanation not in proposal.evidence and len(proposal.evidence) < 8:
                proposal.evidence.append(explanation)
        if proposal.confidence != "high":
            continue
        if CATEGORIES[proposal.category][0] != "ignore":
            continue
        # Имя слоя отражает намерение автора, а не состав каждого объекта. Смешанная
        # и неизвестная геометрия остаётся доступной даже на слое "Подписи";
        # основание исключения — точные native-классы аннотаций.
        if proposal.category == LayerCategory.ANNOTATION and not annotations:
            proposal.confidence = "medium"
            explanation = (
                "В слое есть объекты помимо подтверждённых подписей и размеров"
            )
            if explanation not in proposal.unresolved and len(proposal.unresolved) < 8:
                proposal.unresolved.append(explanation)
            continue
        label_is_explicit = (
            rule.category == proposal.category and rule.confidence == "high"
        )
        known_annotations = (
            proposal.category == LayerCategory.ANNOTATION and annotations
        )
        if not label_is_explicit and not known_annotations:
            proposal.confidence = "medium"
            if (
                "Исключение требует проверки состава объектов"
                not in proposal.unresolved
                and len(proposal.unresolved) < 8
            ):
                proposal.unresolved.append(
                    "Исключение требует проверки состава объектов"
                )
    return result


_GENERIC_CATEGORIES = {
    None,
    LayerCategory.MIXED_SOURCE,
    LayerCategory.UNSPECIFIED_TOPOGRAPHY,
}


def _guard_category_regressions(
    result: LayerRecognition, first_pass: LayerRecognition
) -> LayerRecognition:
    """Последующий вопрос не удаляет уже известную более конкретную категорию."""
    earlier = {item.layer_id: item for item in first_pass.proposals}
    for proposal in result.proposals:
        original = earlier.get(proposal.layer_id)
        if (
            original
            and original.category not in _GENERIC_CATEGORIES
            and proposal.category in _GENERIC_CATEGORIES
        ):
            proposal.category = original.category
            proposal.calculation_role = original.calculation_role
            proposal.confidence = original.confidence
    return result
