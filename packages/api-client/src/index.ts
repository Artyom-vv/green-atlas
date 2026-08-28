import type { components } from './schema';

export type Project = components['schemas']['Project'];
export type ProjectSummary = components['schemas']['ProjectSummary'];
export type SourceFile = components['schemas']['SourceFile'];
export type Layer = components['schemas']['Layer'];
export type LayerKind = components['schemas']['LayerKind'];
export type LayerMapping = components['schemas']['LayerMapping'];
export type PlantingZoneAssignment = components['schemas']['PlantingZoneAssignment'];
export type Plan = components['schemas']['Plan'];
export type PlanHistoryState = components['schemas']['PlanHistoryState'];
export type PlanObject = components['schemas']['PlanObject'];
export type PlanChangeSetDraft = components['schemas']['PlanChangeSetDraft'];
export type ChangeSetPreview = Omit<components['schemas']['ChangeSetPreview'], 'id'> & { id: string };
export type PlanChangeSetApplyRequest = components['schemas']['PlanChangeSetApplyRequest'];
export type PlanMutationResult = components['schemas']['PlanMutationResult'];
export type RowPatternRequest = components['schemas']['RowPatternRequest'];
export type FillPatternRequest = components['schemas']['FillPatternRequest'];
export type PatternPreviewRequest = RowPatternRequest | FillPatternRequest;
export type PatternSkippedCandidate = components['schemas']['PatternSkippedCandidate'];
export type PatternPreview = Omit<components['schemas']['PatternPreview'], 'change_set' | 'skipped'> & { skipped: PatternSkippedCandidate[]; change_set?: ChangeSetPreview | null };
export type SpeciesRevision = components['schemas']['SpeciesRevision'];
export type SpeciesShortlistItem = components['schemas']['SpeciesShortlistItem'];
export type ValidationIssue = components['schemas']['ValidationIssue'];
export type PlacementCheck = components['schemas']['PlacementCheck'];
export type ExportArtifact = components['schemas']['ExportArtifact'];
export type ProjectOperation = components['schemas']['ProjectOperation'];
export type OperationKind = components['schemas']['OperationKind'];
export type GeometrySnapshot = components['schemas']['GeometrySnapshot'];

const API_URL = import.meta.env?.VITE_API_URL ?? 'http://127.0.0.1:8000';

export class ApiClientError extends Error {
  constructor(public code: string, message: string, public fieldErrors: Record<string, string[]> = {}, public details: Record<string, unknown> = {}) {
    super(message);
  }
}

const projectVersions = new Map<string, number>();

function rememberProjectVersions(payload: unknown): void {
  const items = Array.isArray(payload) ? payload : [payload];
  for (const item of items) {
    if (!item || typeof item !== 'object') continue;
    const candidate = item as { id?: unknown; state_version?: unknown };
    if (typeof candidate.id === 'string' && Number.isInteger(candidate.state_version) && Number(candidate.state_version) > 0) {
      projectVersions.set(candidate.id, Number(candidate.state_version));
    }
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const projectMatch = path.match(/^\/api\/projects\/([^/?]+)/);
  const projectId = projectMatch?.[1] ? decodeURIComponent(projectMatch[1]) : undefined;
  const method = (init?.method ?? 'GET').toUpperCase();
  // Placement preview is intentionally POST because it carries a candidate
  // geometry, but it is still a read-only calculation. Treating pointer
  // hover as a project mutation makes a delayed preview compete with an
  // actual placement and can surface a false version conflict.
  const nonStateMutation = /\/operations(?:\/|$)/.test(path) || /\/plan\/(?:placement-check|change-sets\/preview|patterns\/preview)(?:\?|$)/.test(path) || /\/species\/shortlist(?:\?|$)/.test(path);
  const headers = new Headers(init?.headers);
  if (projectId && !['GET', 'HEAD', 'OPTIONS'].includes(method) && !nonStateMutation && !headers.has('If-Match')) {
    const version = projectVersions.get(projectId);
    if (version) headers.set('If-Match', `"${version}"`);
  }
  const response = await fetch(`${API_URL}${path}`, { ...init, headers });
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as { code?: string; message?: string; field_errors?: Record<string, string[]>; details?: Record<string, unknown>; detail?: { code?: string; message?: string; field_errors?: Record<string, string[]>; details?: Record<string, unknown> } } | null;
    const error = payload?.detail ?? payload;
    throw new ApiClientError(error?.code ?? 'HTTP_ERROR', error?.message ?? `Ошибка запроса: ${response.status}`, error?.field_errors, error?.details);
  }
  if (response.status === 204) return undefined as T;
  const payload = await response.json() as T;
  const responseVersion = Number(response.headers.get('X-Project-State-Version'));
  const isDirectProjectRead = method === 'GET' && /^\/api\/projects\/[^/?]+(?:\?.*)?$/.test(path);
  const isProjectStateMutation = method !== 'GET' && !nonStateMutation;
  if (projectId && (isDirectProjectRead || isProjectStateMutation) && Number.isInteger(responseVersion)) projectVersions.set(projectId, responseVersion);
  if (!projectId || isDirectProjectRead || isProjectStateMutation) rememberProjectVersions(payload);
  return payload;
}

const json = (body?: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: body === undefined ? undefined : JSON.stringify(body),
});

export const api = {
  listSpecies: (kind?: 'tree' | 'shrub') => request<SpeciesRevision[]>(`/api/species${kind ? `?kind=${kind}` : ''}`),
  createProject: (name: string) => request<Project>('/api/projects', json({ name })),
  listProjects: () => request<ProjectSummary[]>('/api/projects'),
  deleteProject: (projectId: string) => request<void>(`/api/projects/${projectId}`, { method: 'DELETE' }),
  getProject: (projectId: string, includeGeometry = true) => request<Project>(`/api/projects/${projectId}?include_geometry=${includeGeometry}`),
  getMapFeatures: (projectId: string, extent: [number, number, number, number], resolution: number, signal?: AbortSignal) => {
    const params = new URLSearchParams({ min_x: String(extent[0]), min_y: String(extent[1]), max_x: String(extent[2]), max_y: String(extent[3]), resolution: String(resolution) });
    return request<GeometrySnapshot>(`/api/projects/${projectId}/map-features?${params}`, { signal });
  },
  uploadDxf: (projectId: string, file: File) => {
    const body = new FormData();
    body.append('file', file);
    return request<Project>(`/api/projects/${projectId}/source-dxf`, { method: 'POST', body });
  },
  sourceDownloadUrl: (projectId: string) => `${API_URL}/api/projects/${projectId}/source-dxf/download`,
  saveMappings: (projectId: string, mappings: LayerMapping[]) => request<Project>(`/api/projects/${projectId}/layer-mappings`, { ...json({ mappings }), method: 'PUT' }),
  savePlantingZones: (projectId: string, zones: PlantingZoneAssignment[]) => request<Project>(`/api/projects/${projectId}/planting-zones`, { ...json({ zones }), method: 'PUT' }),
  startGeometryOperation: (projectId: string) => request<ProjectOperation>(`/api/projects/${projectId}/operations/geometry`, json()),
  getOperation: (projectId: string, operationId: string) => request<ProjectOperation>(`/api/projects/${projectId}/operations/${operationId}`),
  getLatestOperation: (projectId: string, kind: OperationKind) => request<ProjectOperation | null>(`/api/projects/${projectId}/operations/latest?kind=${encodeURIComponent(kind)}`),
  cancelOperation: (projectId: string, operationId: string) => request<ProjectOperation>(`/api/projects/${projectId}/operations/${operationId}/cancel`, json()),
  createManualPlan: (projectId: string) => request<Project>(`/api/projects/${projectId}/plan/manual`, json()),
  addPlanObject: (projectId: string, object: { kind: 'tree' | 'shrub'; x: number; y: number; radius?: number }) => request<Plan>(`/api/projects/${projectId}/plan/objects`, json(object)),
  checkPlacement: (projectId: string, object: { kind: 'tree' | 'shrub'; x: number; y: number; radius?: number }, signal?: AbortSignal) => request<PlacementCheck>(`/api/projects/${projectId}/plan/placement-check`, { ...json(object), signal }),
  previewPlanChanges: (projectId: string, draft: PlanChangeSetDraft) => request<ChangeSetPreview>(`/api/projects/${projectId}/plan/change-sets/preview`, json(draft)),
  applyPlanChanges: (projectId: string, preview: ChangeSetPreview) => request<PlanMutationResult>(`/api/projects/${projectId}/plan/change-sets/apply`, json({ preview_id: preview.id, digest: preview.digest, base_plan_version: preview.base_plan_version } satisfies PlanChangeSetApplyRequest)),
  previewPlanPattern: (projectId: string, pattern: PatternPreviewRequest, signal?: AbortSignal) => request<PatternPreview>(`/api/projects/${projectId}/plan/patterns/preview`, { ...json(pattern), signal }),
  shortlistSpecies: (projectId: string, objectIds: string[]) => request<SpeciesShortlistItem[]>(`/api/projects/${projectId}/species/shortlist`, json({ object_ids: objectIds })),
  updatePlanObject: (projectId: string, objectId: string, object: { x?: number; y?: number; radius?: number }) => request<Plan>(`/api/projects/${projectId}/plan/objects/${objectId}`, { ...json(object), method: 'PATCH' }),
  deletePlanObjects: (projectId: string, ids: string[]) => request<Plan>(`/api/projects/${projectId}/plan/objects/delete`, json({ ids })),
  getPlanHistory: (projectId: string) => request<PlanHistoryState>(`/api/projects/${projectId}/plan/history`),
  undoPlanChange: (projectId: string) => request<Project>(`/api/projects/${projectId}/plan/history/undo`, json()),
  redoPlanChange: (projectId: string) => request<Project>(`/api/projects/${projectId}/plan/history/redo`, json()),
  createExport: (projectId: string) => request<ExportArtifact>(`/api/projects/${projectId}/exports`, json()),
  downloadUrl: (path: string) => `${API_URL}${path}`,
};
