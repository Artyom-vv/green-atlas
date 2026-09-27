"""Resolve one authoritative zone reference without erasing command text."""
from dataclasses import dataclass
import re

from app.agent_interpreter import project_context
from app.agent_runtime.semantic_guardrails import _OPERATION_VERBS, _PREPARE_VERBS, _SELECT_VERBS, _tokens


QUOTED = re.compile(r'"([^"\n]+)"|«([^»\n]+)»|“([^”\n]+)”|\x27([^\x27\n]+)\x27')
_GENERIC_LABELS = frozenset("участок зона область контур".split())
_ACTION_WORDS = frozenset(word for _, words in _OPERATION_VERBS for word in words) | _PREPARE_VERBS | _SELECT_VERBS
_ID_PREFIX = re.compile(r"\b(?:id|ид|идентификатор)\s*[:=]?\s*$", re.IGNORECASE)
_ZONE_PREFIX = re.compile(r"\b(?:участ\w*|зон\w*|област\w*)\s*[:=]?\s*$", re.IGNORECASE)
_NUMBER = re.compile(r"\b(?:участ\w*|зон\w*|област\w*)\s*№?\s*(\d+)(?![\w-])", re.IGNORECASE)


def _matches(text: str, value: str):
    return re.finditer(rf"(?<![\w-]){re.escape(value)}(?![\w-])", text, re.IGNORECASE) if value else ()


def mention(text: str, value: str) -> bool:
    return next(iter(_matches(text, value)), None) is not None


def remove_mentions(text: str, values) -> str:
    """Legacy literal removal; use reference.remaining_text for source checks."""
    for value in sorted(set(values), key=len, reverse=True):
        text = re.sub(rf"(?<![\w-]){re.escape(value)}(?![\w-])", " ", text, flags=re.IGNORECASE)
    return text


@dataclass(frozen=True)
class ZoneTargetReference:
    target_id: str | None
    anchors: tuple[str, ...]
    remaining_text: str
    spans: tuple[tuple[int, int], ...]


def _mask(text, spans):
    chars = list(text)
    for start, end in spans:
        chars[start:end] = " " * (end - start)
    return "".join(chars)


def _plain_reference(text, start, end, value):
    """Command-shaped names require an explicit target position."""
    prefix = text[:start]
    noun = _ZONE_PREFIX.search(prefix)
    identity = _ID_PREFIX.search(prefix)
    if identity or noun:
        # Compatibility anchors retain their local reference context too;
        # they must not erase the same word used elsewhere as a command.
        return (identity or noun).start(), end
    if text.strip(" \t\r\n.,;:!?") == value:
        return start, end
    if set(_tokens(value)) & _ACTION_WORDS:
        return None
    previous = _tokens(prefix)
    following = _tokens(text[end:])
    if ((previous and previous[-1] in _ACTION_WORDS | {"и", "или"})
            or (not prefix.strip() and following and following[0] in _ACTION_WORDS)):
        return start, end
    return None


def resolve_zone_reference(text: str, project) -> ZoneTargetReference:
    """Resolve actual occurrences, retaining all unknown/unsupported source.

    Quoted names are atomic: their numbers and verbs are never independent
    references. An exact ID disambiguates a shared label only when that label
    includes the same zone; a different explicit reference remains a conflict.
    """
    descriptors = project_context(project)["zones"]
    raw_labels = {zone.id: zone.label for zone in project.planting_zones}
    labels: dict[str, set[str]] = {}
    identities: dict[str, set[str]] = {}
    for item in descriptors:
        identities.setdefault(item["id"].casefold(), set()).add(item["id"])
        for label in {item["label"], raw_labels.get(item["id"], "")}:
            if label and label.casefold() not in _GENERIC_LABELS:
                labels.setdefault(label.casefold(), set()).add(item["id"])
    # Each occurrence keeps its candidate set. Taking their intersection may
    # disambiguate an alias; it cannot ignore an incompatible second target.
    occurrences = []
    quoted_spans = []
    for quoted in QUOTED.finditer(text):
        quoted_spans.append(quoted.span())
        value = next(value for value in quoted.groups() if value is not None)
        key = value.casefold()
        ids = identities.get(key, set()) if not value.isdigit() or _ID_PREFIX.search(text[:quoted.start()]) else set()
        candidates = ids or labels.get(key, set())
        if candidates:
            occurrences.append((quoted.span(), candidates, bool(ids)))
    plain = _mask(text, quoted_spans)
    matches = []
    for value in identities.keys() | labels.keys():
        for match in _matches(plain, value):
            span = _plain_reference(text, match.start(), match.end(), match.group())
            if span is None:
                continue
            ids = identities.get(value, set()) if not value.isdigit() or _ID_PREFIX.search(text[:match.start()]) else set()
            candidates = ids or labels.get(value, set())
            if candidates:
                matches.append((span, candidates, bool(ids)))
    # A whole display name/UUID wins over references inside that same span.
    for span, candidates, is_id in matches:
        if not any(other != span and other[0] <= span[0] and span[1] <= other[1] for other, _, _ in matches):
            occurrences.append((span, candidates, is_id))
    outside_names = _mask(plain, [span for span, _, _ in occurrences])
    for number in _NUMBER.finditer(outside_names):
        candidates = {item["id"] for item in descriptors if item.get("number") == int(number.group(1))}
        if not candidates:
            raise ValueError("Номер участка отсутствует в проекте. Укажите точный ID или название.")
        occurrences.append((number.span(), candidates, False))
    if not occurrences:
        return ZoneTargetReference(None, (), text, ())
    explicit_ids = set().union(*(candidates for _, candidates, is_id in occurrences if is_id))
    if len(explicit_ids) > 1:
        raise ValueError("Укажите точный ID или однозначное название одного участка")
    candidates = set(explicit_ids) if explicit_ids else set(occurrences[0][1])
    for _, referenced, _ in occurrences:
        candidates.intersection_update(referenced)
    if len(candidates) != 1:
        raise ValueError("Укажите точный ID или однозначное название одного участка")
    spans = tuple(sorted({span for span, _, _ in occurrences}))
    return ZoneTargetReference(next(iter(candidates)), tuple(text[start:end] for start, end in spans),
                               _mask(text, spans), spans)


def resolve_zone_target(text: str, project) -> tuple[str | None, list[str]]:
    reference = resolve_zone_reference(text, project)
    return reference.target_id, list(reference.anchors)
