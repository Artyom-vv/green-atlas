import {
  api,
  ApiClientError,
  type PlantingZoneAssignment,
  type Project,
} from '@green/api-client';
import { queryOptions } from '@tanstack/react-query';
import { savedProjectQuery } from '@/entities/project/api/savedProjectQuery';
import { projectSourceIdentity } from '@/entities/project/model/useProjectWrite';

export const workspaceProjectQuery = (projectId: string) =>
  savedProjectQuery(projectId);

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

const sceneRequestRefused = (error: unknown) =>
  error instanceof ApiClientError &&
  (error.status === 409 || error.status === 422);

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
      projectSourceIdentity(project),
    ],
    queryFn: async ({ signal }) => {
      const view = await api.getSceneView(
        projectId,
        { horizon_year: horizon },
        { signal },
      );
      return {
        ...view.scene,
        displayEnvironment: view.environment,
      } as import('@green/api-client').SceneSnapshot & {
        displayEnvironment: import('@green/api-client').SceneRenderPreparation;
      };
    },
    // A refused version stays visible with its explicit retry action. Toggling
    // modes, focus and reconnect must not repeat the same expensive failure.
    enabled: (query) =>
      Boolean(
        active && project?.map_ready && !sceneRequestRefused(query.state.error),
      ),
    retry: (failureCount, error) =>
      failureCount < 1 && !sceneRequestRefused(error),
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
