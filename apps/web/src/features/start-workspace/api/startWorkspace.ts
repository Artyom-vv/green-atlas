import {
  api,
  type PlantingZoneAssignment,
  type Project,
  type ProjectWriteOptions,
} from '@green/api-client';

function requireProject(project: Project, projectId: string) {
  if (project.id !== projectId)
    throw new Error('Сервер вернул другой проект. Повторите чтение состояния.');
  return project;
}

export const startWorkspaceApi = {
  saveZones: async (
    projectId: string,
    zones: PlantingZoneAssignment[],
    options?: ProjectWriteOptions,
  ) =>
    requireProject(
      await api.savePlantingZones(projectId, zones, options),
      projectId,
    ),
  createPlan: async (projectId: string, options?: ProjectWriteOptions) =>
    requireProject(await api.createManualPlan(projectId, options), projectId),
  readProject: async (projectId: string) =>
    requireProject(await api.getProject(projectId, false), projectId),
};
