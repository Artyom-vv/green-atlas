import { api } from '@green/api-client';
import type { PlacementCandidate } from '../model/singlePlacement';

export function checkPlacementCandidate(
  candidate: PlacementCandidate,
  signal: AbortSignal,
) {
  return api.checkPlacement(candidate.projectId, candidate.check, signal);
}

/** Writes use the captured state version and intentionally accept no abort signal. */
export function addPlacementCandidate(candidate: PlacementCandidate) {
  return api.addPlanObject(candidate.projectId, candidate.object, {
    expectedStateVersion: candidate.expectedStateVersion,
  });
}
