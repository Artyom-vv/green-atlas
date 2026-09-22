import type {
  CadTransferReview,
  CadTransferDecision,
  CadTransferStatus,
  CadDirectory,
  CadFingerprint,
  CadIntakeRequest,
  CadPreviewRequest,
  CadPrepareRequest,
  CadRoot,
  CadSourceAsset,
  CadUploadPackage,
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
  getCadTransferReview: (id: string, signal?: AbortSignal) =>
    request<CadTransferReview>(
      `/api/cad-bridge/approvals/${encodeURIComponent(id)}`,
      { signal },
    ),
  decideCadTransfer: (id: string, decision: CadTransferDecision) =>
    request<CadTransferStatus>(
      `/api/cad-bridge/approvals/${encodeURIComponent(id)}`,
      json(decision),
    ),
  uploadCadPackage: (files: File[]) => {
    const body = new FormData();
    files.forEach((file) => body.append('files', file));
    return request<CadUploadPackage>('/api/cad/uploads', {
      method: 'POST',
      body,
    });
  },
  startCadPrepare: (
    projectId: string,
    prepared: CadPrepareRequest,
    options: ProjectWriteOptions,
  ) =>
    request<ProjectOperation>(
      `/api/projects/${encodeURIComponent(projectId)}/cad-prepare`,
      withProjectWriteOptions(json(prepared), options),
    ),
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
