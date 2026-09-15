import type {
  CadDirectory,
  CadFingerprint,
  CadIntakeRequest,
  CadPreviewRequest,
  CadRoot,
  CadSourceAsset,
  ProjectOperation,
  ProjectWriteOptions,
} from '../contracts';
import { withProjectWriteOptions } from '../transport/projectWrite';
import { API_URL, json, request } from '../transport/request';

const rootPath = (rootId: string) =>
  `/api/cad/roots/${encodeURIComponent(rootId)}`;
const pathQuery = (path: string) => new URLSearchParams({ path });
const assetPath = (projectId: string, operationId: string) =>
  `/api/projects/${encodeURIComponent(projectId)}/operations/${encodeURIComponent(operationId)}/cad-asset`;

export const cadApi = {
  getCadSourceAsset: (
    projectId: string,
    operationId: string,
    signal?: AbortSignal,
  ) => request<CadSourceAsset>(assetPath(projectId, operationId), { signal }),
  cadSourceFileUrl: (projectId: string, operationId: string) =>
    `${API_URL}${assetPath(projectId, operationId)}/file`,
  startCadPreview: (
    projectId: string,
    preview: CadPreviewRequest,
    options: ProjectWriteOptions,
  ) =>
    request<ProjectOperation>(
      `/api/projects/${encodeURIComponent(projectId)}/operations/cad-preview`,
      withProjectWriteOptions(json(preview), options),
    ),
  listCadRoots: () => request<CadRoot[]>('/api/cad/roots'),
  listCadDirectory: (rootId: string, path: string, signal?: AbortSignal) =>
    request<CadDirectory>(`${rootPath(rootId)}/entries?${pathQuery(path)}`, {
      signal,
    }),
  fingerprintCadDrawing: (rootId: string, path: string) =>
    request<CadFingerprint>(
      `${rootPath(rootId)}/fingerprint?${pathQuery(path)}`,
    ),
  startCadIntake: (
    projectId: string,
    intake: CadIntakeRequest,
    options: ProjectWriteOptions,
  ) =>
    request<ProjectOperation>(
      `/api/projects/${encodeURIComponent(projectId)}/operations/cad-intake`,
      withProjectWriteOptions(json(intake), options),
    ),
};
