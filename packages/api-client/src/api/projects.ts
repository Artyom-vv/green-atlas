import type {
  DataPassport,
  GeometrySnapshot,
  LayerMapping,
  NativeDxfSourceAsset,
  OperationKind,
  PlantingZoneAssignment,
  PlantingZonePreview,
  Project,
  ProjectOperation,
  ProjectSummary,
  ProjectWriteOptions,
} from '../contracts';
import { API_URL, json, request } from '../transport/request';
import { withProjectWriteOptions } from '../transport/projectWrite';

export const projectsApi = {
  createProject: (name: string) =>
    request<Project>('/api/projects', json({ name })),
  listProjects: () => request<ProjectSummary[]>('/api/projects'),
  deleteProject: (projectId: string) =>
    request<void>(`/api/projects/${projectId}`, { method: 'DELETE' }),
  getProject: (projectId: string, includeGeometry = true) =>
    request<Project>(
      `/api/projects/${projectId}?include_geometry=${includeGeometry}`,
    ),
  getMapFeatures: (
    projectId: string,
    extent: [number, number, number, number],
    resolution: number,
    signal?: AbortSignal,
  ) => {
    const params = new URLSearchParams({
      min_x: String(extent[0]),
      min_y: String(extent[1]),
      max_x: String(extent[2]),
      max_y: String(extent[3]),
      resolution: String(resolution),
    });
    return request<GeometrySnapshot>(
      `/api/projects/${projectId}/map-features?${params}`,
      { signal },
    );
  },
  getDataPassport: (projectId: string) =>
    request<DataPassport>(`/api/projects/${projectId}/data-passport`),
  uploadDxf: (projectId: string, file: File) => {
    const body = new FormData();
    body.append('file', file);
    return request<Project>(`/api/projects/${projectId}/source-dxf`, {
      method: 'POST',
      body,
    });
  },
  uploadReleaseBundle: (projectId: string, file: File) => {
    const body = new FormData();
    body.append('file', file);
    return request<Project>(`/api/projects/${projectId}/release-bundle`, {
      method: 'POST',
      body,
    });
  },
  sourceDownloadUrl: (projectId: string) =>
    `${API_URL}/api/projects/${projectId}/source-dxf/download`,
  getNativeDxfSourceAsset: (projectId: string, signal?: AbortSignal) =>
    request<NativeDxfSourceAsset>(
      `/api/projects/${encodeURIComponent(projectId)}/source-dxf/asset`,
      { signal },
    ),
  nativeDxfSourceFileUrl: (fileUrl: string) => `${API_URL}${fileUrl}`,
  saveMappings: (projectId: string, mappings: LayerMapping[]) =>
    request<Project>(`/api/projects/${projectId}/layer-mappings`, {
      ...json({ mappings }),
      method: 'PUT',
    }),
  savePlantingZones: (
    projectId: string,
    zones: PlantingZoneAssignment[],
    options?: ProjectWriteOptions,
  ) =>
    request<Project>(
      `/api/projects/${projectId}/planting-zones`,
      withProjectWriteOptions(
        {
          ...json({ zones }),
          method: 'PUT',
        },
        options,
      ),
    ),
  previewPlantingZone: (projectId: string, zone: PlantingZoneAssignment) =>
    request<PlantingZonePreview>(
      `/api/projects/${projectId}/planting-zones/preview`,
      json(zone),
    ),
  startGeometryOperation: (projectId: string) =>
    request<ProjectOperation>(
      `/api/projects/${projectId}/operations/geometry`,
      json(),
    ),
  openSourceEditor: (projectId: string) =>
    request<Project>(`/api/projects/${projectId}/source-editor`, json()),
  getOperation: (projectId: string, operationId: string) =>
    request<ProjectOperation>(
      `/api/projects/${projectId}/operations/${operationId}`,
    ),
  getLatestOperation: (
    projectId: string,
    kind: OperationKind,
    signal?: AbortSignal,
  ) =>
    request<ProjectOperation | null>(
      `/api/projects/${projectId}/operations/latest?kind=${encodeURIComponent(kind)}`,
      { signal },
    ),
  cancelOperation: (projectId: string, operationId: string) =>
    request<ProjectOperation>(
      `/api/projects/${projectId}/operations/${operationId}/cancel`,
      json(),
    ),
};
