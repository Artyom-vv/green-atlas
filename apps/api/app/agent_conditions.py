"""Bind only supported requirement meanings; never equate missing data to compliance."""
import re
from typing import Literal

from app.data_passport import build_data_passport
from app.geometry.domain import CONSTRAINT_KINDS

PositionRuleId = Literal["pp743-3.6.3-building", "pp743-3.6.3-road-edge"]


_SETBACK_VERB = r'(?:сохраняя|сохранять|сохраняй|сохраняйте|соблюдая|соблюдать|соблюдай|соблюдайте|сохранить|сохраните)'
_SETBACK_NOUN = r'(?:нормативн(?:ые|ых)\s+)?отступ(?:ы|ов)'
_SUPPORTED_SETBACK_RE = re.compile(
    rf'(?<!\w)(?:{_SETBACK_VERB}\s+{_SETBACK_NOUN}'
    rf'|(?:с|при)\s+(?:соблюдением|соблюдении|учетом|учётом)\s+{_SETBACK_NOUN}'
    rf'|{_SETBACK_NOUN}\s+{_SETBACK_VERB}'
    r'|нормативн(?:ые|ых)\s+отступ(?:ы|ов))(?!\w)', re.IGNORECASE)
_NEGATIVE_SETBACK_RE = re.compile(
    rf'(?<!\w)(?:не\s+(?:(?:надо|нужно|нужно\s+будет)\s+)?{_SETBACK_VERB}\s+{_SETBACK_NOUN}'
    rf'|{_SETBACK_NOUN}\s+не\s+(?:(?:надо|нужно)\s+)?{_SETBACK_VERB}'
    rf'|без\s+(?:соблюдения|учета|учёта)\s+{_SETBACK_NOUN})(?!\w)', re.IGNORECASE)
_NUMERIC_CONSTRAINT_RE = re.compile(
    r'(?<!\w)(?:не\s+ближе|не\s+дальше|отступ(?:ы|а|ом|ов)?)\s+'
    r'\d+(?:[.,]\d+)?\s*(?:метр(?:а|ов)?|м\b)', re.IGNORECASE)
_SETBACK_MENTION_RE = re.compile(r'\bотступ(?:ы|а|ом|ов)?\b', re.IGNORECASE)
_CLAUSE_SEPARATOR_RE = re.compile(r'[;!?\n]|(?<!\d)[.,]|[.,](?!\d)')


def _normalize_constraint(source: str) -> str:
    return re.sub(r'\s+', ' ', source.casefold().replace('ё', 'е')).strip(' .,!?')


def is_supported_setback_constraint(source: str) -> bool:
    """Return whether this exact source phrase has an implemented policy."""
    return bool(_SUPPORTED_SETBACK_RE.fullmatch(_normalize_constraint(source)))


def extract_supported_setback_constraints(text: str) -> list[str]:
    """Recover the implemented setback phrase if the model omitted it."""
    unsupported = _unsupported_constraint_spans(text)
    fragments = [match.group(0) for match in _SUPPORTED_SETBACK_RE.finditer(text)
                 if not any(start < match.end() and match.start() < end for start, end in unsupported)]
    return list(dict.fromkeys(fragments))


def extract_unsupported_constraint_evidence(text: str) -> list[str]:
    """Keep explicit negative/numeric restrictions from being silently lost."""
    return list(dict.fromkeys(text[start:end].strip(' .,!?') for start, end in _unsupported_constraint_spans(text)))


def _unsupported_constraint_spans(text: str) -> list[tuple[int, int]]:
    """Preserve modifiers attached to a setback phrase, never its safe substring.

    Exact positive forms may be embedded in a task. A negation, numeric value,
    exception or unrecognised tail remains an unsupported condition. A separate
    numeric clause retains its own source fragment for the legacy interpreter.
    """
    spans = [match.span() for pattern in (_NEGATIVE_SETBACK_RE, _NUMERIC_CONSTRAINT_RE)
             for match in pattern.finditer(text)]
    positives = list(_SUPPORTED_SETBACK_RE.finditer(text))
    separators = list(_CLAUSE_SEPARATOR_RE.finditer(text))
    for noun in _SETBACK_MENTION_RE.finditer(text):
        if any(start <= noun.start() < end for start, end in spans):
            continue
        start = max((item.end() for item in separators if item.end() <= noun.start()), default=0)
        end = min((item.start() for item in separators if item.start() >= noun.end()), default=len(text))
        positive = next((item for item in positives if item.start() <= noun.start() and item.end() >= noun.end()), None)
        if positive is None:
            spans.append((start, end))
            continue
        prefix, tail = text[start:positive.start()].strip(), text[positive.end():end].strip()
        # Bare normative nouns are supported as a clause; a preceding command
        # such as "increase" or "ignore" must not disappear in extraction.
        bare_with_prefix = re.fullmatch(r'нормативн(?:ые|ых)\s+отступ(?:ы|ов)', positive.group(0), re.IGNORECASE) and prefix
        separate_numeric = re.fullmatch(r'и\s+(.+)', tail, re.IGNORECASE)
        numeric_tail = separate_numeric and _NUMERIC_CONSTRAINT_RE.fullmatch(separate_numeric.group(1))
        if bare_with_prefix or (tail and not numeric_tail):
            spans.append((start if bare_with_prefix else positive.start(), end))
    return spans


def compact_constraint_evidence(fragments: list[str]) -> list[str]:
    """Keep the most informative source phrase when regexes overlap."""
    unique = list(dict.fromkeys(fragment for fragment in fragments if fragment.strip()))
    normalized = [_normalize_constraint(fragment) for fragment in unique]
    return [fragment for index, fragment in enumerate(unique)
            if not any(index != other and normalized[index] != normalized[other]
                       and normalized[index] in normalized[other]
                       for other in range(len(unique)))]


def bind_placement_conditions(project, constraints, exclusions, *, passport=None):
    if exclusions:
        raise ValueError('Необходимо определить на карте исключённые области перед расчётом')
    bindings = []
    for source in constraints or []:
        normalized = _normalize_constraint(source)
        # Closed vocabulary for an already implemented policy, not a fuzzy
        # permission to drop arbitrary numeric, negative or additional terms.
        if not is_supported_setback_constraint(source):
            raise ValueError(f'Условие пока не связано с проверкой: «{source}»')
        bindings.append({'source': source, 'policy': 'existing_position_checker',
                         'rule_ids': [rule[0] for rule in CONSTRAINT_KINDS.values()]})
    if not bindings:
        return None
    features = project.geometry.feature_collection.get('features', []) if project.geometry else []
    present = {feature.get('properties', {}).get('kind') for feature in features if feature.get('geometry')}
    rules = [{'rule_id': rule[0], 'obstacle_kind': kind,
              'status': 'geometry_available' if kind in present else 'no_geometry'}
             for kind, rule in CONSTRAINT_KINDS.items()]
    return {'bindings': bindings, 'rules': rules, 'coverage': 'partial',
            'full_compliance_verified': False,
            'data_gaps': list((passport or build_data_passport(project)).gaps),
            'notice': 'Проверяются отступы от распознанных зданий и дорог. Полная нормативная проверка не выполнена.'}
