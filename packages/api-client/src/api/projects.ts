import type {
  DataPassport,
  GeometrySnapshot,
  LayerMapping,
  LayerRecognition,
  NativeAreaPreview,
  SourceObjectReviewPage,
  SourceObjectContext,
  SourceAreaGroupRequest,
  SourceAreaGroupCheck,
  SourceReadIssues,
  NativeFaceReview,
  NativeFaceDecision,
  SourceObjectDecisionRequest,
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
  getNativeFaces: (projectId: string) => request<NativeFaceReview>(`/api/projects/${projectId}/source-native-faces`),
  decideNativeFace: (projectId: string, input: NativeFaceDecision, options: ProjectWriteOptions) =>
    request<Project>(`/api/projects/${projectId}/source-native-faces/decision`, withProjectWriteOptions(json(input), options)),
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
  getLayerRecognition: (projectId: string) =>
    request<LayerRecognition>(`/api/projects/${projectId}/source-layer-review`),
  retryLayerRecognition: (projectId: string) =>
    request<LayerRecognition>(`/api/projects/${projectId}/source-layer-review`, json()),
  getSourceObjectReview: (projectId: string, layer: string, offset = 0) => {
    const params = new URLSearchParams({ layer, offset: String(offset), limit: '30' });
    return request<SourceObjectReviewPage>(`/api/projects/${projectId}/source-object-review?${params}`);
  },
  decideSourceObject: (projectId: string, input: SourceObjectDecisionRequest, options: ProjectWriteOptions) =>
    request<Project>(`/api/projects/${projectId}/source-object-review/decision`,
      withProjectWriteOptions(json(input), options)),
  getSourceObjectContext: (projectId: string, route: string, scale = 1) =>
    request<SourceObjectContext>(`/api/projects/${projectId}/source-object-context?${new URLSearchParams({route, scale:String(scale)})}`),
  getSourceReadIssues: (projectId: string) => request<SourceReadIssues>(`/api/projects/${projectId}/source-read-issues`),
  checkSourceAreaGroup: (projectId: string, input: SourceAreaGroupRequest) =>
    request<SourceAreaGroupCheck>(`/api/projects/${projectId}/source-area-groups/check`, json(input)),
  acceptSourceAreaGroup: (projectId: string, input: SourceAreaGroupRequest, options: ProjectWriteOptions) =>
    request<Project>(`/api/projects/${projectId}/source-area-groups`, withProjectWriteOptions(json(input), options)),
  removeSourceAreaGroup: (projectId: string, groupId: string, options: ProjectWriteOptions) =>
    request<Project>(`/api/projects/${projectId}/source-area-groups/${groupId}`, withProjectWriteOptions({method:'DELETE'}, options)),
  getNativeAreaPreview: (projectId: string, proposalId: string) =>
    request<NativeAreaPreview>(`/api/projects/${projectId}/source-native-area/preview?proposal_id=${encodeURIComponent(proposalId)}`),
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
  acceptPartialGeometry: (
    projectId: string,
    sourceSha256: string,
    options?: ProjectWriteOptions,
  ) =>
    request<Project>(
      `/api/projects/${projectId}/source-partial-geometry/accept`,
      withProjectWriteOptions(json({ source_sha256: sourceSha256 }), options),
    ),
  decideNativeArea: (
    projectId: string,
    input: {
      source_sha256: string;
      proposal_id: string;
      proposal_sha256: string;
      decision: 'accepted' | 'rejected';
    },
    options?: ProjectWriteOptions,
  ) =>
    request<Project>(
      `/api/projects/${projectId}/source-native-area/decision`,
      withProjectWriteOptions(json(input), options),
    ),
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
      { signal, cache: 'no-store' },
    ),
  cancelOperation: (projectId: string, operationId: string) =>
    request<ProjectOperation>(
      `/api/projects/${projectId}/operations/${operationId}/cancel`,
      json(),
    ),
};
