"""Bind a map action to complete user messages and an authoritative zone.

The language model does not choose a camera target. This closed action uses the
same project references as zone editing and checks every source turn in full.
Only a target answer may amend the first action; other requirements remain
visible and prevent dispatch.
"""

import re

from app.agent_runtime.contracts import ControlIntent
from app.agent_runtime.history_budget import join_source_history
from app.agent_runtime.semantic_guardrails import _tokens
from app.agent_runtime.target_sources import resolve_zone_reference


_SHOW = frozenset("покажи покажите показать приблизь приблизьте приблизить открой откройте открыть".split())
_TARGET_WORDS = frozenset("участок участка участку участке зону зона зоны зоне область области id".split())
_COMMAND_WORDS = _TARGET_WORDS | _SHOW | frozenset("на карте пожалуйста".split())
_QUOTED = re.compile(r'"([^"\n]+)"|«([^»\n]+)»|“([^”\n]+)”|\x27([^\x27\n]+)\x27')
_MAP = re.compile(r"\bна\s+карте\b", re.IGNORECASE)


def _is_control_request(text: str) -> bool:
    # Quoted names can contain verbs, negation and words like «выбранный».
    # Recognise the requested action outside those labels, then require the
    # reference resolver to account for the entire quoted value as well.
    source = _QUOTED.sub(" ", text)
    return bool(set(_tokens(source)) & _SHOW and _MAP.search(source))


def bind_control_source(text: str, project, *, source_turns: list[str] | None = None) -> ControlIntent | None:
    turns = list(source_turns) if source_turns else [text]
    source = join_source_history(turns)
    if text != source:
        raise ValueError("Исходное поручение не соответствует сохранённым сообщениям")
    if not turns or not _is_control_request(turns[0]):
        return None

    target_id = None
    unsupported = []
    for index, turn in enumerate(turns):
        try:
            reference = resolve_zone_reference(turn, project)
            remainder = reference.remaining_text
            tokens = _tokens(remainder)
            if index == 0:
                if len([token for token in tokens if token in _SHOW]) != 1 or len(_MAP.findall(remainder)) != 1:
                    raise ValueError("Укажите одно действие: показать один участок на карте")
                allowed = _COMMAND_WORDS
            else:
                # The answer changes only a target slot. Repeating the whole
                # action or adding another request needs a separate task.
                allowed = _TARGET_WORDS | {"пожалуйста"}
                if reference.target_id is None:
                    raise ValueError("В ответе укажите точный ID, номер или уникальное название одного участка")
            unknown = [token for token in tokens if token not in allowed]
            # An unmatched quoted value must not disappear merely because it
            # consists of otherwise accepted command/target words.
            if unknown or _QUOTED.search(remainder):
                raise ValueError("Для этих условий показа участка пока нет проверенного действия: "
                                 + (", ".join(dict.fromkeys(unknown)) if unknown else remainder.strip()))
            if reference.target_id is not None:
                target_id = reference.target_id
        except ValueError as error:
            unsupported.append(str(error))

    return ControlIntent(zone_id=target_id, unsupported_requirements=list(dict.fromkeys(unsupported))[:20])
