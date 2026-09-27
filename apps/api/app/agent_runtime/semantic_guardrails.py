"""Small, typed semantic guardrails for high-risk intent slots.

The local model enriches an intent, but it is not the authority for whether a
request adds or removes plan objects.  This module keeps that authority in a
single auditable boundary. It matches action verbs and direct domain objects
using a closed vocabulary; nominal references do not authorize mutations.
"""

from dataclasses import dataclass
from typing import Any, Literal
import re

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


# Action words are matched as verbs, not noun prefixes: "посадок" and
# "изменений" describe an object of inspection, not another command.
_OPERATION_VERBS: tuple[tuple[Operation, frozenset[str]], ...] = (
    ("place", frozenset("посади посадите посадить сажай сажайте сажать размести разместите разместить "
                        "размещай размещайте размещать заполни заполните заполнить заполняй заполнять "
                        "озелени озелените озеленить озеленяй озеленять рассади рассадите рассадить "
                        "засей засейте засеять добавь добавьте добавить добавляй добавлять plant add".split())),
    ("delete", frozenset("удали удалите удалить удаляй удаляйте удалять убери уберите убрать убирать "
                         "очисти очистите очистить очищай очищать delete remove".split())),
    ("edit", frozenset("измени измените изменить изменяй изменять перемести переместите переместить "
                       "перемещай перемещать передвинь передвиньте передвинуть сдвинь сдвиньте сдвинуть "
                       "замени замените заменить заменяй заменять поменяй поменяйте поменять "
                       "переименуй переименуйте переименовать "
                       "редактируй редактируйте редактировать закрепи закрепите закрепить "
                       "закрепляй закрепляйте закреплять открепляй открепляйте откреплять "
                       "открепи открепите открепить разблокируй разблокировать зафиксируй зафиксировать "
                       "edit move replace lock unlock".split())),
    ("inspect", frozenset("покажи покажите показать проверь проверьте проверить проверяй проверять "
                          "найди найдите найти посмотри посмотрите посмотреть проанализируй проанализируйте "
                          "проанализировать изучи изучите изучить оцени оцените оценить inspect check show find".split())),
    ("release", frozenset("экспортируй экспортируйте экспортировать опубликуй опубликуйте опубликовать "
                          "выпусти выпустите выпустить export publish".split())),
)
_PREPARE_VERBS = frozenset("подготовь подготовьте подготовить сделай сделайте сделать создай создайте создать".split())
_SELECT_VERBS = frozenset("подбери подберите подобрать выбирай выбери выберите выбрать select choose".split())
_TARGET_PREPOSITIONS = frozenset("на в во из для у около внутри вдоль по с со к от через".split())
_ACTION_SEPARATORS = frozenset("и затем потом".split())
_NEGATION_MODIFIERS = frozenset("пожалуйста надо нужно следует необходимо требуется только стоит".split())
_ZONE_TARGET_STEMS = ("участ", "зон", "границ", "област")
_PLANT_TARGET_STEMS = ("растен", "сажен", "посад", "дерев", "кустар", "пород", "сос", "ел", "ель", "лип", "дуб", "сирен", "барбар")
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


def explicit_zone_numbers(text: str, *, source_turns: list[str] | None = None) -> tuple[int, ...]:
    """Read only numbers attached to a visible zone noun in the source text."""
    turns = source_turns or [text]
    if len(turns) > 1:
        return next((numbers for turn in reversed(turns) if (numbers := explicit_zone_numbers(turn))), ())
    text = turns[0]
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


def explicit_target_count(text: str, *, source_turns: list[str] | None = None) -> int | None:
    """Bind integer quantities to plant nouns, excluding zone and time numbers.

    In a resumed run the latest quantity amendment replaces earlier counts.
    Multiple component quantities are refused unless an explicit total agrees.
    """
    turns = source_turns or [text]
    if len(turns) > 1:
        for turn in reversed(turns):
            count = explicit_target_count(turn)
            if count is not None:
                return count
        return None
    text = turns[0]
    tokens = _tokens(text)
    matches: list[tuple[int, str]] = []
    for index, token in enumerate(tokens):
        if not token.isdigit():
            continue
        following = tokens[index + 1] if index + 1 < len(tokens) else ""
        previous = tokens[max(0, index - 5):index]
        if previous and previous[-1].startswith(("участ", "зон", "област")):
            continue
        if following.startswith(("растен", "сажен")) or following in {"посадок", "посадки", "посадку"}:
            matches.append((int(token), "total"))
        elif following.startswith(("дерев", "кустар", "дуб", "лип", "сосен", "елей")):
            matches.append((int(token), "component"))
        elif any(word.startswith("количеств") for word in previous) and previous[-1:] in [("на",), ("до",)]:
            matches.append((int(token), "total"))
    if not matches:
        return None
    totals = [count for count, kind in matches if kind == "total"]
    components = [count for count, kind in matches if kind == "component"]
    if len(matches) == 1:
        count = matches[0][0]
    elif len(totals) == 1 and sum(components) == totals[0]:
        count = totals[0]
    else:
        raise ValueError("В запросе несколько количеств растений. Укажите одно общее количество для этого задания.")
    if not 1 <= count <= 5000:
        raise ValueError("Количество растений должно быть целым числом от 1 до 5000.")
    return count


def preserving_quantity_amendment(text: str) -> int | None:
    """Recognize only a quantity edit followed by an explicit keep-selection clause.

    Extra instructions fall back to the normal compiler; this bounded grammar
    cannot swallow a simultaneous request to change a zone or species.
    """
    tokens = _tokens(text)
    keep = [index for index, token in enumerate(tokens) if token.startswith("сохран")]
    if len(keep) != 1:
        return None
    quantity, preservation = tokens[:keep[0]], tokens[keep[0] + 1:]
    quantity_stems = ("измен", "уменьш", "увелич", "количеств", "растен")
    preserved_stems = ("участ", "схем", "пород")
    if not quantity or any(not (token.isdigit() or token in {"на", "до"} or token.startswith(quantity_stems)) for token in quantity):
        return None
    if any(not (token == "и" or token.startswith(preserved_stems)) for token in preservation):
        return None
    if not all(any(token.startswith(stem) for token in preservation) for stem in preserved_stems):
        return None
    return explicit_target_count(text)


def explicit_edit_action(text: str, *, source_turns: list[str] | None = None) -> str | None:
    if source_turns:
        return next((action for turn in reversed(source_turns) if (action := explicit_edit_action(turn))), None)
    tokens = _tokens(text)
    unlock = any(_contains_stem(tokens, stem) for stem in ("откреп", "разблок", "unlock", "сними закреп", "снять закреп"))
    actions = {
        "unlock": unlock,
        "lock": not unlock and any(_contains_stem(tokens, stem) for stem in ("закреп", "зафикс", "lock")),
        "move": any(_contains_stem(tokens, stem) for stem in ("перемест", "передвин", "сдвин", "move")),
        "species": any(_contains_stem(tokens, stem) for stem in ("замен", "помен", "replace"))
                   and any(_contains_stem(tokens, stem) for stem in ("пород", "species")),
    }
    selected = [action for action, present in actions.items() if present]
    if len(selected) > 1:
        raise ValueError("Укажите одно изменение: перемещение, замену породы, закрепление или снятие закрепления.")
    return selected[0] if selected else None


def explicit_move_vector(text: str, *, source_turns: list[str] | None = None) -> dict[str, float]:
    """Only numeric displacements explicitly bound to drawing axes X/Y."""
    turns = source_turns or [text]
    if len(turns) > 1:
        return next((vector for turn in reversed(turns) if (vector := explicit_move_vector(turn))), {})
    text = turns[0]
    vector = {}
    number = r"([+-]?\d+(?:[.,]\d+)?)"
    patterns = ((rf"\b([xy])\b\s*(?:на|=|:)?\s*{number}", False),
                (rf"{number}\s*(?:метр(?:а|ов)?|м)\s+(?:по\s+)?(?:оси\s+)?([xy])\b", True))
    for pattern, reversed_groups in patterns:
        for match in re.finditer(pattern, text.replace("−", "-"), re.IGNORECASE):
            axis, value = reversed(match.groups()) if reversed_groups else match.groups()
            key = "move_dx_m" if axis.casefold() == "x" else "move_dy_m"
            distance = float(value.replace(",", "."))
            if key in vector and vector[key] != distance:
                raise ValueError("Для одной оси заданы разные смещения. Укажите одно значение по X и Y.")
            vector[key] = distance
    return vector


def _contains_stem(tokens: tuple[str, ...], stem: str) -> bool:
    phrase = stem.split()
    if len(phrase) == 1:
        return any(token.startswith(stem) for token in tokens)
    return any(
        all(index + offset < len(tokens) and tokens[index + offset].startswith(word)
            for offset, word in enumerate(phrase))
        for index in range(len(tokens))
    )


def _action_target(tokens: tuple[str, ...], index: int, verbs: set[str]) -> str | None:
    """Bind the first direct domain object, stopping at another verb or qualifier.

    In "на участке 2 удали дерево" the zone is a location. In "удали участок 2
    с деревьями" the zone itself is the object, even though plants also appear.
    """
    def direct_object(words: tuple[str, ...]) -> str | None:
        for token in words:
            if token in verbs or token in _TARGET_PREPOSITIONS or token in _ACTION_SEPARATORS:
                break
            if token.startswith(_ZONE_TARGET_STEMS):
                return "zone"
            if token.startswith(_PLANT_TARGET_STEMS):
                return "plant"
            if token.startswith(("план", "истори", "отчёт", "отчет")):
                return "document"
        return None

    target = direct_object(tokens[index + 1:])
    if target is not None:
        return target
    # Russian allows the direct object before its verb: "участок 2 удали".
    # Reading the clause from its start retains the distinction from a
    # prepositional location such as "на участке 2 удали".
    start = 0
    for previous, token in enumerate(tokens[:index]):
        if token in verbs or token in _ACTION_SEPARATORS:
            start = previous + 1
    return direct_object(tokens[start:index])


def _is_negated_action(tokens: tuple[str, ...], index: int) -> bool:
    for token in reversed(tokens[:index]):
        if token not in _NEGATION_MODIFIERS:
            if token in {"не", "нельзя", "запрещено"}:
                return True
            break
    following = tokens[index + 1:]
    return bool(following and (
        following[0] in {"нельзя", "запрещено"}
        or (len(following) > 1 and following[0] == "не" and following[1] in _NEGATION_MODIFIERS)
    ))


def _action_operations(tokens: tuple[str, ...]) -> tuple[Operation, ...]:
    by_verb = {verb: operation for operation, verbs in _OPERATION_VERBS for verb in verbs}
    verbs = set(by_verb) | _PREPARE_VERBS | _SELECT_VERBS | {"сними", "снять", "снимите"}
    found: set[Operation] = set()
    for index, token in enumerate(tokens):
        if token in verbs and _is_negated_action(tokens, index):
            raise ValueError("В запросе есть отрицание действия. Укажите явно, какое действие нужно выполнить.")
        operation = by_verb.get(token)
        target = _action_target(tokens, index, verbs) if token in verbs else None
        if operation is not None:
            if target == "zone" and (operation in {"delete", "edit"} or token in {"добавь", "добавьте", "добавить", "add"}):
                operation = "zones"
            found.add(operation)
        elif token in {"сними", "снимите", "снять"}:
            following = tokens[index + 1:]
            if following and following[0].startswith("закреп"):
                found.add("edit")
            elif target == "plant":
                found.add("delete")
        elif token in _PREPARE_VERBS:
            if target == "plant":
                found.add("place")
            elif target == "zone":
                found.add("zones")
    # Choosing resources accompanies a plan operation; on its own it is a
    # read-only search. Merely mentioning a zone never implies editing zones.
    if not found and ("сколько" in tokens or any(token in _SELECT_VERBS for token in tokens)):
        found.add("inspect")
    return tuple(operation for operation in ("place", "delete", "edit", "inspect", "zones", "release") if operation in found)


def infer_semantic_signals(text: str, *, source_turns: list[str] | None = None) -> SemanticSignals:
    # History boundaries are server-owned data. User text, including a literal
    # display marker, must always be evaluated as one complete instruction.
    turns = source_turns or [text]
    if len(turns) > 1:
        signals = [infer_semantic_signals(turn) for turn in turns]
        # A question answer edits the brief, not the existing plants. Its
        # wording «измени количество» must not turn place into edit.
        if any("delete" in signal.operations for signal in signals[1:]) and "delete" not in signals[0].operations:
            raise ValueError("Удаление — отдельное действие. Создайте для него новое задание.")
        return SemanticSignals(
            operations=signals[0].operations,
            plant_kinds=next((signal.plant_kinds for signal in reversed(signals) if signal.plant_kinds), ()),
            arrangements=next((signal.arrangements for signal in reversed(signals) if signal.arrangements), ()),
            delegations=tuple(dict.fromkeys(slot for signal in signals for slot in signal.delegations)),
        )
    text = turns[0]
    tokens = _tokens(text)
    action_operations = _action_operations(tokens)
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
        operations=action_operations,
        plant_kinds=plant_kinds,
        arrangements=arrangements,
        delegations=delegations,
    )


def reconcile_draft(text: str, draft: Any, *, source_turns: list[str] | None = None) -> tuple[Any, list[str]]:
    """Reconcile only explicit high-signal conflicts; refuse ambiguity.

    A single unambiguous operation in the user's text is authoritative.  A
    mixed tree/shrub mention is likewise authoritative because silently
    dropping one requested kind changes the work.  Multiple conflicting
    actions are rejected instead of guessed.
    """
    signals = infer_semantic_signals(text, source_turns=source_turns)
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

    target_count = draft.target_count
    source_count = explicit_target_count(text, source_turns=source_turns)
    if source_count is not None and target_count != source_count:
        corrections.append(f"target_count:{target_count or 'unspecified'}->{source_count}")
        target_count = source_count

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
        "target_count": target_count,
    }), corrections
