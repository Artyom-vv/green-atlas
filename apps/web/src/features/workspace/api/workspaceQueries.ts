import {
  api,
  type PlantingZoneAssignment,
  type Project,
} from '@green/api-client';
import { queryOptions } from '@tanstack/react-query';

export const workspaceProjectQuery = (projectId: string) =>
  queryOptions({
    queryKey: ['workspace-project', projectId],
    queryFn: () => api.getProject(projectId, false),
    enabled: Boolean(projectId),
  });

export const workspaceHistoryQuery = (projectId: string, enabled: boolean) =>
  queryOptions({
    queryKey: ['plan-history', projectId],
    queryFn: () => api.getPlanHistory(projectId),
    enabled: Boolean(projectId && enabled),
    staleTime: 0,
  });

export const workspaceSpeciesQuery = () =>
  queryOptions({
    queryKey: ['species'],
    queryFn: () => api.listSpecies(),
    staleTime: Number.POSITIVE_INFINITY,
  });

export const workspaceSceneQuery = (
  projectId: string,
  project: Project | undefined,
  horizon: number,
  active: boolean,
) =>
  queryOptions({
    queryKey: [
      'plan-scene',
      projectId,
      project?.plan?.version,
      project?.geometry_version,
      horizon,
    ],
    queryFn: ({ signal }) => api.getPlanScene(projectId, horizon, signal),
    enabled: Boolean(active && project?.map_ready),
    placeholderData: (previous, query) =>
      query?.queryKey[1] === projectId ? previous : undefined,
    staleTime: Number.POSITIVE_INFINITY,
  });

export const workspaceZonePreviewQuery = (
  projectId: string,
  geometryVersion: number | undefined,
  zone?: PlantingZoneAssignment,
) =>
  queryOptions({
    queryKey: ['zone-review', projectId, zone, geometryVersion],
    queryFn: () => {
      if (!zone) throw new Error('Не выбран участок для проверки');
      return api.previewPlantingZone(projectId, zone);
    },
    enabled: Boolean(zone),
    retry: false,
  });
