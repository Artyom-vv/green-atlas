"""Политика доверия к исходным данным перед массовой посадкой.

Задаём порядок классов паспорта, минимальные пространственные основания и
уверенность в их назначении. Это рабочая политика полноты, а не нормативные
отступы или доказательство отсутствия физических объектов. Проект, геометрия
и сборка DTO остаются у своих владельцев.
"""

from typing import Final

from app.dxf_import.evidence_contracts import (
    DataPassportConfidence,
    DataPassportEntryStatus,
    DataPassportKind,
)

# Порядок классов задаёт порядок паспорта, замечаний и использованных слоёв.
DATA_CLASSES: Final[tuple[DataPassportKind, ...]] = (
    "site_border",
    "building",
    "road",
    "utility",
    "existing_green",
    "lawn",
    "water",
    "restricted",
)
# Отсутствие водного слоя само по себе не делает любую посадку на суше
# неподтверждённой; эти классы служат минимальными основаниями массовой посадки.
CRITICAL_CLASSES: Final[frozenset[DataPassportKind]] = frozenset(
    {"site_border", "building", "road", "utility", "existing_green", "restricted"}
)
ENTRY_CONFIDENCE: Final[dict[DataPassportEntryStatus, DataPassportConfidence]] = {
    "verified": "high",
    "partial": "medium",
    "missing": "low",
    "excluded": "low",
}
