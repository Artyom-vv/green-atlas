"""Small, typed semantic guardrails for high-risk intent slots.

The local model enriches an intent, but it is not the authority for whether a
request adds or removes plan objects.  This module keeps that authority in a
single auditable boundary.  It uses a closed domain vocabulary and token
prefixes, not a growing collection of prompt-specific rewrites or regexes.
"""

from dataclasses import dataclass
from typing import Any, Literal

Operation = Literal["place", "edit", "delete", "inspect", "zones", "release"]
PlantKind = Literal["tree", "shrub", "mixed"]


@dataclass(frozen=True)
class SemanticSignals:
    operations: tuple[Operation, ...] = ()
    plant_kinds: tuple[Literal["tree", "shrub"], ...] = ()
    arrangements: tuple[str, ...] = ()
    delegations: tuple[Literal["scope", "species"], ...] = ()

    @property
    def plant_kind(self) -> PlantKind | None:
        if len(self.plant_kinds) > 1:
            return "mixed"
        return self.plant_kinds[0] if self.plant_kinds else None


_OPERATION_STEMS: tuple[tuple[Operation, tuple[str, ...]], ...] = (
    ("place", ("посад", "размест", "заполн", "озелен", "рассад", "засе", "добав")),
    ("delete", ("удал", "убер", "очист", "снять посад")),
    ("edit", ("измен", "перемест", "передвин", "сдвин", "замен", "редакт", "закреп", "откреп")),
    ("inspect", ("покаж", "провер", "найд", "посмотр", "скольк", "проанализ")),
    ("zones", ("участ", "зон", "границ")),
    ("release", ("выпуск", "экспорт", "опубли")),
)
_PLANT_STEMS: tuple[tuple[Literal["tree", "shrub"], tuple[str, ...]], ...] = (
    ("tree", ("дерев", "ель", "сосен", "липа", "дуб")),
    ("shrub", ("кустар", "сирен", "барбар", "жив")),
)
def _tokens(text: str) -> tuple[str, ...]:
    current: list[str] = []
    tokens: list[str] = []
    for char in text.casefold():
        if char.isalpha() or char.isdigit():
            current.append(char)
        elif current:
            tokens.append("".join(current))
            current = []
    if current:
        tokens.append("".join(current))
    return tuple(tokens)


def explicit_zone_numbers(text: str) -> tuple[int, ...]:
    """Read only numbers attached to a visible zone noun in the source text."""
    tokens = _tokens(text)
    numbers: list[int] = []
    for index, token in enumerate(tokens):
        if not token.isdigit() or index == 0:
            continue
        previous = tokens[index - 1]
        if (previous.startswith("участ") or previous.startswith("зон")
                or previous.startswith("област")):
            numbers.append(int(token))
    return tuple(dict.fromkeys(numbers))


def _contains_stem(tokens: tuple[str, ...], stem: str) -> bool:
    phrase = stem.split()
    if len(phrase) == 1:
        return any(token.startswith(stem) for token in tokens)
    return any(
        all(index + offset < len(tokens) and tokens[index + offset].startswith(word)
            for offset, word in enumerate(phrase))
        for index in range(len(tokens))
    )


def infer_semantic_signals(text: str) -> SemanticSignals:
    tokens = _tokens(text)
    action_operations = tuple(
        operation for operation, stems in _OPERATION_STEMS
        if operation != "zones" and any(_contains_stem(tokens, stem) for stem in stems)
    )
    zone_operation = ("zones",) if not action_operations and any(
        _contains_stem(tokens, stem) for stem in ("участ", "зон", "границ")
    ) else ()
    plant_kinds = tuple(
        kind for kind, stems in _PLANT_STEMS
        if any(_contains_stem(tokens, stem) for stem in stems)
    )
    building_anchor = any(_contains_stem(tokens, stem)
                          for stem in ("вдоль здан", "контур здан", "у здан"))
    grove_shape = any(_contains_stem(tokens, stem) for stem in ("групп", "куртин"))
    road_anchor = any(_contains_stem(tokens, stem)
                      for stem in ("вдоль улиц", "вдоль дорог", "вдоль проезд", "у дорог"))
    area_anchor = any(_contains_stem(tokens, stem)
                      for stem in ("по площад", "на площад", "по территор"))
    if building_anchor:
        arrangements = ("building_groves" if grove_shape else "building_contour",)
    elif road_anchor:
        arrangements = ("road_edges",)
    elif area_anchor:
        arrangements = ("area",)
    else:
        arrangements = ()
    choose = _contains_stem(tokens, "выбер")
    self_choice = _contains_stem(tokens, "сам")
    scope_delegated = (
        (choose and self_choice and _contains_stem(tokens, "участ"))
        or _contains_stem(tokens, "любой подход")
        or _contains_stem(tokens, "подходящий участ")
    )
    species_delegated = (
        (choose and self_choice and any(_contains_stem(tokens, stem)
                                       for stem in ("пород", "состав", "растен")))
        or _contains_stem(tokens, "породу выбер")
        or _contains_stem(tokens, "состав выбер")
    )
    delegations = tuple(
        slot for slot, enabled in (("scope", scope_delegated), ("species", species_delegated))
        if enabled
    )
    return SemanticSignals(
        operations=action_operations or zone_operation,
        plant_kinds=plant_kinds,
        arrangements=arrangements,
        delegations=delegations,
    )


def reconcile_draft(text: str, draft: Any) -> tuple[Any, list[str]]:
    """Reconcile only explicit high-signal conflicts; refuse ambiguity.

    A single unambiguous operation in the user's text is authoritative.  A
    mixed tree/shrub mention is likewise authoritative because silently
    dropping one requested kind changes the work.  Multiple conflicting
    actions are rejected instead of guessed.
    """
    signals = infer_semantic_signals(text)
    corrections: list[str] = []
    if len(signals.operations) > 1:
        raise ValueError("В запросе смешаны разные действия. Укажите одно: добавить, изменить, удалить или проверить.")

    operation = draft.operation
    if len(signals.operations) == 1:
        explicit_operation = signals.operations[0]
        if operation != explicit_operation:
            corrections.append(f"operation:{operation}->{explicit_operation}")
            operation = explicit_operation

    plant_kind = draft.plant_kind
    explicit_kind = signals.plant_kind
    if explicit_kind is not None and plant_kind != explicit_kind:
        corrections.append(f"plant_kind:{plant_kind or 'unspecified'}->{explicit_kind}")
        plant_kind = explicit_kind

    arrangement = draft.arrangement
    if len(signals.arrangements) == 1 and arrangement != signals.arrangements[0]:
        corrections.append(f"arrangement:{arrangement or 'unspecified'}->{signals.arrangements[0]}")
        arrangement = signals.arrangements[0]

    scope_mode = draft.scope_mode
    if ("scope" in signals.delegations and not draft.zone_labels
            and not draft.object_ids and scope_mode != "delegated"):
        corrections.append(f"scope_mode:{scope_mode}->delegated")
        scope_mode = "delegated"

    return draft.model_copy(update={
        "operation": operation,
        "plant_kind": plant_kind,
        "arrangement": arrangement,
        "scope_mode": scope_mode,
    }), corrections
