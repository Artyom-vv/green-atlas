import { api } from '@green/api-client';

export const listProjects = () => api.listProjects();
export const removeProject = (projectId: string) =>
  api.deleteProject(projectId);
