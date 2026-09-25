"""Spatially diverse probes; only accepted positions consume candidate space.

A batch temporarily defers its neighbours, but does not reject them. When a
probe fails, those neighbours remain eligible. Accepted centres remove their
spacing neighbourhood in one vector operation rather than thousands of CAD
checks or user-visible failed planting records.
"""

import numpy as np


class CandidateQueue:
    def __init__(self, operations, spacing_m):
        self.points = np.asarray([(op.object.x, op.object.y) for op in operations], dtype=float)
        self.alive = np.ones(len(operations), dtype=bool)
        self.clearance = np.full(len(operations), np.inf)
        self.spacing_squared = spacing_m ** 2

    def __bool__(self):
        return bool(self.alive.any())

    def batch(self, limit):
        available = self.alive.copy()
        clearance = self.clearance.copy()
        result = []
        while available.any() and len(result) < limit:
            eligible = np.flatnonzero(available)
            index = int(eligible[np.argmax(clearance[eligible])])
            result.append(index)
            self.alive[index] = False
            distance = np.square(self.points - self.points[index]).sum(axis=1)
            clearance = np.minimum(clearance, distance)
            available &= distance >= self.spacing_squared
            available[index] = False
        return result

    def accept(self, x, y):
        distance = np.square(self.points - (x, y)).sum(axis=1)
        self.clearance = np.minimum(self.clearance, distance)
        covered = self.alive & (distance < self.spacing_squared)
        count = int(covered.sum())
        self.alive[covered] = False
        return count
