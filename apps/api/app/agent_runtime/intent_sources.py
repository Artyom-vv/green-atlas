"""Prevent source-backed task arguments being reclassified as conditions."""
import re

from app.agent_runtime.semantic_guardrails import _OPERATION_VERBS, _tokens, explicit_target_count, explicit_move_vector, explicit_edit_action
from app.species.catalog import CATALOG
from app.agent_conditions import extract_supported_setback_constraints


_SPECIES_NOUNS = {
    "tilia-cordata": r"лип(?:а|ы|е|у|ой|ою|ам|ами|ах)?",
    "acer-platanoides": r"клен(?:а|у|ом|е|ы|ов|ам|ами|ах)?",
    "quercus-robur": r"дуб(?:а|у|ом|е|ы|ов|ам|ами|ах)?",
    "betula-pendula": r"берез(?:а|ы|е|у|ой|ою|ам|ами|ах)?",
    "sorbus-aucuparia": r"рябин(?:а|ы|е|у|ой|ою|ам|ами|ах)?",
    "ulmus-laevis": r"вяз(?:а|у|ом|е|ы|ов|ам|ами|ах)?",
    "pinus-sylvestris": r"(?:сосн(?:а|ы|е|у|ой|ою|ам|ами|ах)?|сосен)",
    "picea-abies": r"ел(?:ь|и|ью|ей|ям|ями|ях)",
    "cornus-alba": r"дерен(?:а|у|ом|е|ы|ов|ам|ами|ах)?",
    "spiraea-japonica": r"спире(?:я|и|е|ю|ей|ям|ями|ях)",
}


def source_species_exclusions(text: str, *, zone_labels: list[str], object_ids: list[str]) -> list[str]:
    """The include-only species argument cannot express source exclusions."""
    normalized = text.casefold().replace("ё", "е")
    for reference in sorted(set([*zone_labels, *object_ids]), key=len, reverse=True):
        normalized = re.sub(rf'(?<![\w-]){re.escape(reference.casefold().replace("ё", "е"))}(?![\w-])', ' ', normalized)
    alternatives = [*_SPECIES_NOUNS.values(), *(re.escape(item.id.casefold()) for item in CATALOG),
                    *(re.escape(item.scientific_name.casefold()) for item in CATALOG)]
    species_pattern = re.compile(r'(?<![\w-])(?:' + '|'.join(alternatives) + r')(?![\w-])')
    unsupported = []
    for clause in re.split(r'[;!?\n]|(?<!\d)[.,]|[.,](?!\d)', normalized):
        for match in species_pattern.finditer(clause):
            prefix, suffix = clause[:match.start()], clause[match.end():]
            if (re.search(r'\b(?:кроме|исключая|без|за\s+исключением|не)\s+(?:[\w-]+\s+){0,3}$', prefix)
                    or re.match(r'\s+(?:не\s+|исключи\w*\b)', suffix)):
                unsupported.append(clause.strip(' ,.:'))
                break
    return list(dict.fromkeys(unsupported))


def source_species_ids(text: str, *, proposed: list[str], zone_labels: list[str], object_ids: list[str], source_turns: list[str] | None = None) -> list[str]:
    """Catalog choices need a source ID, scientific name or closed noun form."""
    from app.agent_runtime.semantic_guardrails import infer_semantic_signals
    turns = source_turns or [text]
    for turn in reversed(turns):
        normalized = turn.casefold().replace("ё", "е")
        for reference in sorted(set([*zone_labels, *object_ids]), key=len, reverse=True):
            normalized = re.sub(rf'(?<![\w-]){re.escape(reference.casefold().replace("ё", "е"))}(?![\w-])', ' ', normalized)
        selected = []
        for species in CATALOG:
            alternatives = [re.escape(species.id.casefold()), re.escape(species.scientific_name.casefold())]
            if species.species_id in _SPECIES_NOUNS:
                alternatives.append(_SPECIES_NOUNS[species.species_id])
            if re.search(r'(?<![\w-])(?:' + '|'.join(alternatives) + r')(?![\w-])', normalized):
                selected.append(species.id)
        selected.extend(identity for identity in proposed if re.search(rf'(?<![\w-]){re.escape(identity.casefold())}(?![\w-])', normalized))
        if selected:
            return list(dict.fromkeys(selected))
        if "species" in infer_semantic_signals(turn).delegations:
            return []
    return []


def is_task_argument_reference(fragment: str, *, text: str, operation: str, zone_labels: list[str], object_ids: list[str], source_turns: list[str] | None = None) -> bool:
    """Recognize a closed task-argument phrase, never a safe substring of a condition.

    Unknown modifiers, negations, policy nouns and extra numeric conditions
    remain outside this vocabulary and must retain their requirement meaning.
    """
    normalized = lambda value: re.sub(r"\s+", " ", value.casefold().replace("ё", "е")).strip(" .,;:!?")
    source = normalized(fragment)
    if not source or source not in normalized(text):
        return False
    references = [normalized(item) for item in [*zone_labels, *object_ids] if item]
    if source in references:
        return True
    remaining = source
    for reference in references:
        remaining = re.sub(rf'(?<![\w-]){re.escape(reference)}(?![\w-])', ' ', remaining)
    vector = (explicit_move_vector(text, source_turns=source_turns)
              if operation == "edit" and explicit_edit_action(text, source_turns=source_turns) == "move" else {})
    matched_axes = set()
    def remove_axis(match):
        key = "move_dx_m" if match[1].casefold() == "x" else "move_dy_m"
        if vector.get(key) != float(match[2].replace(",", ".")):
            return match[0]
        matched_axes.add(key)
        return " "
    remaining = re.sub(r'\b([xy])\s*(?:на|=|:)?\s*([+-]?\d+(?:[.,]\d+)?)\s*(?:метр(?:а|ов)?|м\b)?', remove_axis, remaining)
    tokens = _tokens(remaining)
    verbs = dict(_OPERATION_VERBS).get(operation, frozenset())
    # Scope names and axis values alone are already typed references; an
    # otherwise empty phrase is not a new condition either.
    if not tokens:
        return True
    # A model may quote the parameter clause separately from the action.
    # Only source-matched axes plus these labels form a typed displacement;
    # no remaining numbers, negation, or requirement words are discarded.
    if matched_axes and all(token in {"смещение", "смещением", "по", "оси", "осям", "на", "и"} for token in tokens):
        return True
    if not any(token in verbs for token in tokens):
        return False
    count = explicit_target_count(text, source_turns=source_turns)
    words = set("все всех весь всею на в во из по и для у внутри участке участка участки участков участок "
                "зоне зоны зон зону зона дерево дерева деревья деревьев кустарник кустарника кустарники кустарников "
                "растения растений растение посадки посадок посадку посадка объект объекты объектов "
                "смещение смещением оси метра метров м пожалуйста".split())
    return all(token in verbs or token in words or (token.isdigit() and count is not None and int(token) == count)
               for token in tokens)


def has_only_bound_placement_source(text: str, *, zone_labels: list[str], object_ids: list[str], source_turns: list[str]) -> bool:
    """Prove a complete simple brief contains no unbound requirement.

    This is deliberately narrower than intent compilation. It allows dropping
    a model-authored requirement only after accounting for the entire source,
    never from the wording of the model's explanation. Any extra source word,
    numeric condition, exception or malformed reference prevents that proof.
    """
    if len(source_turns) != 1:
        return False
    from app.agent_runtime.semantic_guardrails import infer_semantic_signals
    if infer_semantic_signals(text).operations != ("place",) or explicit_target_count(text) is None:
        return False
    source = text
    for reference in sorted(set([*zone_labels, *object_ids]), key=len, reverse=True):
        source = re.sub(rf'(?<![\w-]){re.escape(reference)}(?![\w-])', ' ', source, flags=re.IGNORECASE)
    setbacks = extract_supported_setback_constraints(source)
    if not setbacks:
        return False
    for phrase in setbacks:
        source = re.sub(re.escape(phrase), ' ', source, flags=re.IGNORECASE)
    # These source clauses already produce typed arrangement/delegation slots.
    # Removing whole bounded phrases leaves the ordinary placement arguments;
    # an attached qualifier remains visible to the closed vocabulary check.
    source = re.sub(r'\b(?:группами\s+вдоль\s+зданий|вдоль\s+зданий|вдоль\s+дороги|по\s+площади)\b', ' ', source, flags=re.IGNORECASE)
    source = re.sub(r'\b(?:породу|состав)\s+выбери\s+сам\b', ' ', source, flags=re.IGNORECASE)
    return is_task_argument_reference(source, text=source, operation="place", zone_labels=[], object_ids=[])
