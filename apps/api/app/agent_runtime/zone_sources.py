"""Resolve zone slots from complete, server-recorded user messages.

Every message is checked in full. A later slot replaces only that slot; it
cannot drop a qualifier or invent an action. Geometry is a project reference.
"""
import re

from app.agent_runtime.semantic_guardrails import _tokens
from app.agent_runtime.target_sources import (QUOTED as _QUOTED, mention as _mention,
    remove_mentions as _remove_mentions, resolve_zone_target as _target, resolve_zone_reference)
from app.agent_runtime.zone_contracts import ZoneGeometryReference, ZoneIntent, ZoneSourceAmendment
from shapely.geometry import shape
from app.planting_zone_changes import _digest
from app.agent_runtime.semantic_guardrails import _action_target, _is_negated_action


_ACTIONS = {
    "create": frozenset("создай создайте создать добавь добавьте добавить нарисуй нарисуйте нарисовать".split()),
    "update": frozenset("переименуй переименуйте переименовать измени измените изменить исправь исправьте исправить редактируй редактировать".split()),
    "delete": frozenset("удали удалите удалить удаляй удаляйте удалять убери уберите убрать".split()),
}
_NAMING = frozenset("назови назовите назвать".split())
_ALLOWED_WORDS = frozenset((
    "участок участка участку участке зону зона зоны зоне область области контур контура контуру контуре "
    "границу границы название названием именем новый новую новое нового новой новым "
    "из по в во на с со и его этот эту текущий пожалуйста один одну 1 id"
).split()) | _NAMING | frozenset(word for words in _ACTIONS.values() for word in words)
_GENERIC_LABELS = frozenset("участок зона область контур".split())
_FEATURE_KINDS = frozenset({"allowed", "site_border", "planting_area"})


def _source_action(text: str) -> str:
    tokens = _tokens(_QUOTED.sub(" ", text))
    verbs = set().union(*_ACTIONS.values()) | _NAMING
    actions = []
    for index, token in enumerate(tokens):
        if token in verbs and _is_negated_action(tokens, index):
            raise ValueError("Отрицание не разрешает изменение участка. Уточните действие.")
        for operation, words in _ACTIONS.items():
            if token in words:
                if _action_target(tokens, index, verbs) in {"plant", "document"}:
                    raise ValueError("Действие относится к другой сущности, а не к участку")
                actions.append(operation)
    if len(actions) != 1:
        raise ValueError("Укажите одно действие с участком: создать, изменить или удалить")
    return actions[0]


def _source_name(text: str, operation: str) -> tuple[str | None, str]:
    candidates = []
    prefixes = {"названием", "именем", "назови", "назовите", "назвать"}
    prefixes |= {"участок", "зону", "область"} if operation == "create" else {"в", "на"}
    if operation == "delete":
        return None, text
    for match in _QUOTED.finditer(text):
        previous = _tokens(text[:match.start()])
        if previous and previous[-1] in prefixes:
            candidates.append((next(value for value in match.groups() if value is not None), match.span()))
    if len(candidates) > 1:
        raise ValueError("Укажите одно новое название участка")
    if not candidates:
        return None, text
    label, (start, end) = candidates[0]
    return label, text[:start] + " " + text[end:]


def zone_geometry_references(project) -> list[dict]:
    """Expose stable full-resolution contours, never viewport simplifications."""
    features = (project.geometry.feature_collection.get("features", []) if project.geometry else [])
    counts = {}
    for feature in features:
        identity = str(feature.get("id", ""))
        counts[identity] = counts.get(identity, 0) + 1
    result = []
    for feature in features:
        identity = str(feature.get("id", ""))
        properties = feature.get("properties", {})
        if not identity or counts[identity] != 1 or properties.get("kind") not in _FEATURE_KINDS:
            continue
        try:
            geometry = shape(feature["geometry"])
            if geometry.is_empty or not geometry.is_valid or geometry.geom_type not in {"Polygon", "MultiPolygon"}:
                continue
            reference = ZoneGeometryReference(project_id=project.id, state_version=project.state_version,
                geometry_version=project.geometry_version, feature_id=identity, geometry_digest=_digest(feature["geometry"]))
        except (TypeError, ValueError, KeyError):
            continue
        result.append({"reference": reference.model_dump(mode="json"),
                       "label": properties.get("label") if isinstance(properties.get("label"), str) else None,
                       "kind": properties["kind"]})
    return result





def _contour(text, project):
    descriptors = zone_geometry_references(project)
    named_ids = [item for item in descriptors if _mention(text, item["reference"]["feature_id"])]
    named_labels = [item for item in descriptors if item.get("label")
        and item["label"].casefold() not in _GENERIC_LABELS and _mention(text, item["label"])]
    # An explicit ID disambiguates its own duplicated display name. It cannot
    # erase another contour named in the same clause, even if that name also
    # happens to consist entirely of ordinary grammar words.
    if named_ids:
        id_values = {item["reference"]["feature_id"] for item in named_ids}
        id_labels = {item["label"].casefold() for item in named_ids if item.get("label")}
        matched = named_ids + [item for item in named_labels
            if item["reference"]["feature_id"] not in id_values and item["label"].casefold() not in id_labels]
    else:
        matched = named_labels
    if not matched:
        return None, []
    if len(matched) != 1:
        raise ValueError("Название контура неоднозначно. Укажите точный ID геометрии")
    item = matched[0]
    return ZoneGeometryReference(**item["reference"]), [value for value in
        (item["reference"]["feature_id"], item.get("label")) if value]


def _check_remainder(text, anchors, allowed=_ALLOWED_WORDS):
    unsupported = [token for token in _tokens(_remove_mentions(text, anchors)) if token not in allowed]
    if unsupported:
        raise ValueError("Для этих уточнений участка пока нет проверенного действия: "
                         + ", ".join(dict.fromkeys(unsupported)))


def _geometry_clauses(text):
    """Relations inside a quoted identifier are part of that identifier."""
    quoted = [match.span() for match in _QUOTED.finditer(text)]
    clauses, start = [], 0
    for match in re.finditer(r"\b(?:из|по)\b", text, flags=re.IGNORECASE):
        if any(left <= match.start() < right for left, right in quoted):
            continue
        clauses.append(text[start:match.start()])
        start = match.end()
    clauses.append(text[start:])
    return clauses


def _complete_turn(text, project, operation):
    label, remainder = _source_name(text, operation)
    clauses = _geometry_clauses(remainder)
    target_reference = resolve_zone_reference(clauses[0], project) if operation != "create" else None
    target = target_reference.target_id if target_reference else None
    reference, geometry_anchors = _contour(" ".join(clauses[1:]), project)
    checked = target_reference.remaining_text + remainder[len(clauses[0]):] if target_reference else remainder
    _check_remainder(checked, geometry_anchors)
    slots = {"target_zone_id": target, "label": label, "geometry_reference": reference}
    if operation == "delete" and reference is not None:
        raise ValueError("Удаление не принимает новое название или геометрию")
    # A declared effect is mandatory even while its value is missing. This
    # prevents a name+contour request becoming a rename-only preview (or a
    # rename without a name becoming a geometry-only update).
    words = set(_tokens(_QUOTED.sub(" ", text)))
    required = set()
    rename = bool(words & {"переименуй", "переименуйте", "переименовать"})
    if rename or words & {"название", "названием", "именем", *_NAMING}:
        required.add("label")
    geometry_words = {"контур", "контура", "контуру", "контуре", "границу", "границы"}
    if len(clauses) > 1 or (operation == "update" and not rename and words & geometry_words):
        required.add("geometry_reference")
    return {key: value for key, value in slots.items() if value is not None}, required


def _short_turn(text, project):
    # A bare quoted name is an unambiguous answer to the name slot. The whole
    # answer must fit: a condition following the quotation remains a condition.
    stripped = text.strip().rstrip(".!?").strip()
    label_text = re.sub(r"^(?:название|новое\s+название|назови|назовите|назвать)\s*[:=]?\s*", "", stripped, flags=re.IGNORECASE)
    label_match = _QUOTED.fullmatch(label_text)
    if label_match:
        return {"label": next(value for value in label_match.groups() if value is not None)}
    # A contour must be identified as such; a target zone sharing its label
    # never implicitly changes geometry.
    if re.match(r"\s*(?:(?:из|по)\s+)?контур(?:а|у)?\b", text, flags=re.IGNORECASE):
        reference, anchors = _contour(text, project)
        _check_remainder(text, anchors, frozenset("из по контур контура контуру id".split()))
        if reference is None:
            raise ValueError("Укажите точный ID или однозначное название контура проекта")
        return {"geometry_reference": reference}
    target = resolve_zone_reference(text, project)
    _check_remainder(target.remaining_text, [], frozenset("участок участка участке зону зона зоны зоне область области id этот эту".split()))
    if target.target_id is None:
        raise ValueError("Укажите участок, контур проекта или новое название в кавычках")
    return {"target_zone_id": target.target_id}


def resolve_zone_sources(turns: list[str], project) -> tuple[ZoneIntent, list[ZoneSourceAmendment]]:
    """Compile a slot ledger; the model cannot invent or discard source facts."""
    operation = None
    slots = {}
    required = set()
    amendments = []
    action_words = set().union(*_ACTIONS.values())
    for index, source in enumerate(turns):
        words = _tokens(_QUOTED.sub(" ", source))
        if index == 0 or any(word in action_words for word in words):
            current_operation = _source_action(source)
            if operation is not None and current_operation != operation:
                raise ValueError("Другое действие с участком требует нового задания")
            operation = current_operation
            supplied, declared = _complete_turn(source, project, operation)
            required.update(declared)
        else:
            supplied = _short_turn(source, project)
        if operation == "create" and "target_zone_id" in supplied:
            raise ValueError("Для создания укажите контур проекта; ID нового участка задаёт сервер")
        if operation == "delete" and set(supplied) - {"target_zone_id"}:
            raise ValueError("Удаление не принимает новое название или геометрию")
        for slot, value in supplied.items():
            slots[slot] = value
            amendments.append(ZoneSourceAmendment(turn_index=index, slot=slot, value=value))
    if operation != "create" and not slots.get("target_zone_id"):
        raise ValueError("Укажите точный ID или однозначное название одного участка")
    if operation == "create" and not slots.get("geometry_reference"):
        raise ValueError("Укажите контур проекта для создания участка")
    if "geometry_reference" in required and not slots.get("geometry_reference"):
        raise ValueError("Укажите контур проекта: поручение также требует изменения геометрии")
    if "label" in required and not slots.get("label"):
        raise ValueError("Укажите новое название в кавычках: поручение требует переименования")
    if operation == "update" and not (slots.get("label") or slots.get("geometry_reference")):
        raise ValueError("Укажите новое название в кавычках или контур проекта")
    return ZoneIntent(operation=operation, **slots), amendments
