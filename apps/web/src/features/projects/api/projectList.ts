/** Библиотека проектов: чтение каталога и удаление подтверждённой ревизии. */
import { api, type ProjectSummary } from '@green/api-client';

export const listProjects = (signal?: AbortSignal) => api.listProjects(signal);
export const removeProject = (project: ProjectSummary) =>
  api.deleteProject(project.id, {
    headers: { 'If-Match': `"${project.state_version}"` },
  });
