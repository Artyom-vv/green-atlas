import { api } from '@green/api-client';
import { queryOptions } from '@tanstack/react-query';

const SHORTLIST_STALE_MS = 30_000;
const MASKS_STALE_MS = 5 * 60_000;

export const selectionSpeciesQuery = (
  projectId: string,
  ids: string[],
  enabled: boolean,
) =>
  queryOptions({
    queryKey: ['species-shortlist', projectId, [...ids].sort().join(':')],
    queryFn: () => api.shortlistSpecies(projectId, ids),
    enabled,
    staleTime: SHORTLIST_STALE_MS,
  });

export const zoneSpeciesQuery = (
  projectId: string,
  zoneIds: string[],
  enabled: boolean,
) =>
  queryOptions({
    queryKey: [
      'species-shortlist-zones',
      projectId,
      [...zoneIds].sort().join(','),
    ],
    queryFn: () => api.shortlistSpecies(projectId, { zoneIds }),
    enabled,
    staleTime: SHORTLIST_STALE_MS,
  });

export const placementMasksQueryOptions = (
  projectId: string,
  enabled: boolean,
) =>
  queryOptions({
    queryKey: ['placement-masks', projectId],
    queryFn: () => api.listPlacementMasks(projectId),
    enabled,
    staleTime: MASKS_STALE_MS,
  });

export const buildingTargetsQuery = (
  projectId: string,
  zoneIds: string[],
  stateVersion: number | undefined,
  enabled: boolean,
) =>
  queryOptions({
    queryKey: ['building-screen-targets', projectId, zoneIds, stateVersion],
    queryFn: ({ signal }) =>
      api.getBuildingScreenTargets(projectId, zoneIds, signal),
    enabled,
  });
