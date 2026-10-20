import { api, type ChangeSetPreview } from '@green/api-client';
import type { PlacementCandidate } from '../model/singlePlacement';

export function checkPlacementCandidate(
  candidate: PlacementCandidate,
  signal: AbortSignal,
) {
  return api.checkPlacement(candidate.projectId, candidate.check, signal);
}

/** Preparation can be cancelled before an applied operation exists. */
export function previewPlacementCandidate(
  candidate: PlacementCandidate,
  signal: AbortSignal,
) {
  return api.previewPlanChanges(
    candidate.projectId,
    {
      base_plan_version: candidate.expectedPlanVersion,
      source: 'manual',
      policy: 'all_or_nothing',
      label:
        candidate.object.kind === 'tree'
          ? 'Добавление дерева'
          : 'Добавление кустарника',
      operations: [
        {
          type: 'add',
          object: {
            ...candidate.object,
            size_class: candidate.object.size_class ?? 'standard',
            spacing_policy: candidate.object.spacing_policy ?? 'balanced',
            locked: candidate.object.locked ?? false,
          },
        },
      ],
    },
    signal,
    { expectedStateVersion: candidate.expectedStateVersion },
  );
}

/** Once sent, an apply is neither cancelled nor repeated to discover its result. */
export const applyPlacementCandidate = (
  projectId: string,
  preview: ChangeSetPreview,
) => api.applyPlanChanges(projectId, preview);

export const readPlacementReceipt = (
  projectId: string,
  preview: ChangeSetPreview,
  stateVersion: number,
) => api.getPlanChangeReceipt(projectId, preview, stateVersion);

export const readPlacementProject = (projectId: string) =>
  api.getProjectRead(projectId, false);
