"""Document-local workaround for ezdxf 1.4.4 zero-length dogleg transforms.

Upstream LeaderData.transform normalizes a zero vector when dogleg_length == 0,
including for a valid disabled dogleg. An INSERT containing it cannot expand.
Keep the SDK's transform, applying it to a unit direction and restoring the
exact zero length. No global monkeypatch, source bytes or DXF values are changed
by preparation. Remove this adapter when the regression passes on a fixed SDK.
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
        # Orientation has meaning even for a disabled, zero-length dogleg.
        # A unit vector allows SDK rotation/reflection/scaling without asking
        # it to normalize zero. This does not add a visible landing segment.
        self.dogleg_length = 1.0
        try:
            super().transform(wcs)
        finally:
            self.dogleg_length = 0.0


def prepare_multileader_transforms(document: Drawing) -> int:
    """Adapt affected records in this document only, including block entities."""
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
