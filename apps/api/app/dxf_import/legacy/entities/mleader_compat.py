"""Локальная совместимость трансформации MLEADER в ezdxf 1.4.4 с нулевым dogleg;
исходные DXF-значения сохраняются.
"""

import ezdxf
from ezdxf.document import Drawing
from ezdxf.entities.mleader import LeaderData
from ezdxf.math import WCSTransform


class _ZeroDoglegLeaderData(LeaderData):
    def transform(self, wcs: WCSTransform) -> None:
        if self.dogleg_length != 0:
            super().transform(wcs)
            return
        # Ориентация сохраняет смысл и при отключённом dogleg нулевой длины.
        # Единичный вектор позволяет SDK вращать, отражать и масштабировать без
        # нормализации нуля; видимый дополнительный сегмент при этом не создаётся.
        self.dogleg_length = 1.0
        try:
            super().transform(wcs)
        finally:
            self.dogleg_length = 0.0


def prepare_multileader_transforms(document: Drawing) -> int:
    """Адаптировать только затронутые записи этого документа, включая сущности блоков."""
    if ezdxf.__version__ != "1.4.4":
        return 0
    count = 0
    for entity in document.entitydb.values():
        if entity.dxftype() != "MULTILEADER":
            continue
        for index, leader in enumerate(entity.context.leaders):
            if leader.dogleg_length != 0 or isinstance(leader, _ZeroDoglegLeaderData):
                continue
            replacement = _ZeroDoglegLeaderData()
            replacement.__dict__.update(vars(leader))
            entity.context.leaders[index] = replacement
            count += 1
    return count
