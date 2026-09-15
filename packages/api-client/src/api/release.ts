import type {
  ExportArtifact,
  ReleaseCreateRequest,
  ReleasePackage,
} from '../contracts';
import { API_URL, json, request } from '../transport/request';

export const releaseApi = {
  createExport: (projectId: string) =>
    request<ExportArtifact>(`/api/projects/${projectId}/exports`, json()),
  createRelease: (projectId: string, release: ReleaseCreateRequest) =>
    request<ReleasePackage>(
      `/api/projects/${projectId}/releases`,
      json(release),
    ),
  getRelease: (projectId: string, releaseId: string) =>
    request<ReleasePackage>(`/api/projects/${projectId}/releases/${releaseId}`),
  downloadUrl: (path: string) => `${API_URL}${path}`,
};
