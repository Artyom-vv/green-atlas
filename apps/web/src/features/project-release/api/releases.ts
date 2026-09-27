import {
  api,
  type ReleaseCreateRequest,
  type ReleasePackage,
} from '@green/api-client';

export const releaseQueryKey = (projectId: string, releaseId?: string) => [
  'project-release',
  projectId,
  releaseId,
];

function requireRelease(
  release: ReleasePackage,
  projectId: string,
  releaseId?: string,
) {
  if (
    release.project_id !== projectId ||
    !release.id ||
    (releaseId && release.id !== releaseId)
  ) {
    throw new Error('Сервер вернул другой пакет. Повторите загрузку.');
  }
  return release;
}

export async function readRelease(projectId: string, releaseId: string) {
  return requireRelease(
    await api.getRelease(projectId, releaseId),
    projectId,
    releaseId,
  );
}

export async function createRelease(
  projectId: string,
  request: ReleaseCreateRequest,
) {
  return requireRelease(await api.createRelease(projectId, request), projectId);
}

export function downloadReleaseFile(path: string): void {
  window.location.href = api.downloadUrl(path);
}
