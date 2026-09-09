"""Bind only supported requirement meanings; never equate missing data to compliance."""
import re

from app.data_passport import build_data_passport
from app.geometry.domain import CONSTRAINT_KINDS


_SUPPORTED_SETBACK_RE = re.compile(
    r'(?:'
    r'(?:сохраняя|соблюдая|соблюдать|соблюдай|соблюдайте|сохранить|сохраните)\s+'
    r'нормативн(?:ые|ых)\s+отступ(?:ы|ов)'
    r'|(?:с|при)\s+(?:соблюдением|соблюдении|учетом|учётом)\s+'
    r'нормативн(?:ые|ых)\s+отступ(?:ы|ов)'
    r'|нормативн(?:ые|ых)\s+отступ(?:ы|ов)'
    r'(?:\s+(?:соблюдай|соблюдайте|сохраняй))?'
    r')', re.IGNORECASE)


def _normalize_constraint(source: str) -> str:
    return re.sub(r'\s+', ' ', source.casefold().replace('ё', 'е')).strip(' .,!?')


def is_supported_setback_constraint(source: str) -> bool:
    """Return whether this exact source phrase has an implemented policy."""
    return bool(_SUPPORTED_SETBACK_RE.fullmatch(_normalize_constraint(source)))


def extract_supported_setback_constraints(text: str) -> list[str]:
    """Recover the implemented setback phrase if the model omitted it."""
    fragments = []
    for match in _SUPPORTED_SETBACK_RE.finditer(text):
        prefix = text[max(0, match.start() - 4):match.start()]
        if re.search(r'\bне\s*$', prefix.casefold()):
            continue
        fragments.append(match.group(0).strip(' .,!?'))
    return list(dict.fromkeys(fragments))


def extract_unsupported_constraint_evidence(text: str) -> list[str]:
    """Keep explicit negative/numeric restrictions from being silently lost."""
    patterns = (
        r'не\s+(?:соблюдая|соблюдать|соблюдай|соблюдайте|сохраняя|сохранять|сохраняй|сохранить|сохраните)\s+'
        r'нормативн(?:ые|ых)\s+отступ(?:ы|ов)',
        r'(?:не\s+ближе|не\s+дальше|отступ(?:ы|а|ом|ов)?)\s+'
        r'\d+(?:[.,]\d+)?\s*(?:метр(?:а|ов)?|м\b)',
    )
    fragments = []
    for pattern in patterns:
        fragments.extend(match.group(0).strip(' .,!?') for match in re.finditer(pattern, text, re.IGNORECASE))
    return list(dict.fromkeys(fragments))


def compact_constraint_evidence(fragments: list[str]) -> list[str]:
    """Keep the most informative source phrase when regexes overlap."""
    unique = list(dict.fromkeys(fragment for fragment in fragments if fragment.strip()))
    normalized = [_normalize_constraint(fragment) for fragment in unique]
    return [fragment for index, fragment in enumerate(unique)
            if not any(index != other and normalized[index] != normalized[other]
                       and normalized[index] in normalized[other]
                       for other in range(len(unique)))]


def bind_placement_conditions(project, constraints, exclusions):
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
            'data_gaps': list(build_data_passport(project).gaps),
            'notice': 'Проверяются отступы от распознанных зданий и дорог. Полная нормативная проверка не выполнена.'}
