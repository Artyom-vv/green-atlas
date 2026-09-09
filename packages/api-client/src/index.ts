import type { components } from './schema';

export type Project = components['schemas']['Project'];
export type ProjectSummary = components['schemas']['ProjectSummary'];
export type ImportStatus = components['schemas']['ImportStatus'];
export type SourceFile = components['schemas']['SourceFile'];
export type Layer = components['schemas']['Layer'];
export type DataPassportEntry = components['schemas']['DataPassportEntry'];
export type DataPassport = components['schemas']['DataPassport'];
export type LayerKind = components['schemas']['LayerKind'];
export type LayerMapping = components['schemas']['LayerMapping'];
export type PlantingZoneAssignment = components['schemas']['PlantingZoneAssignment'];
export type PlantingZonePreview = components['schemas']['PlantingZonePreview'];
export type Plan = components['schemas']['Plan'];
export type PlanHistoryState = components['schemas']['PlanHistoryState'];
export type PlanHistoryEntry = components['schemas']['PlanHistoryEntry'];
export type PlanObject = components['schemas']['PlanObject'];
export type PlanChangeSetDraft = components['schemas']['PlanChangeSetDraft'];
export type ChangeSetPreview = Omit<components['schemas']['ChangeSetPreview'], 'id'> & { id: string };
export type PlanChangeSetApplyRequest = components['schemas']['PlanChangeSetApplyRequest'];
export type PlanMutationResult = components['schemas']['PlanMutationResult'];
export type RowPatternRequest = components['schemas']['RowPatternRequest'];
export type FillPatternRequest = components['schemas']['FillPatternRequest'];
export type PlacementMaskRequest = Omit<components['schemas']['PlacementMaskRequest'], 'screen_side'> & { screen_side?: 'perimeter' | 'roads' };
export type PlacementMaskPreset = components['schemas']['PlacementMaskPreset'];
export type PatternPreviewRequest = RowPatternRequest | FillPatternRequest | PlacementMaskRequest;
export type PatternSkippedCandidate = components['schemas']['PatternSkippedCandidate'];
export type CandidateReasonSummary = components['schemas']['CandidateReasonSummary'];
export type PatternPreview = Omit<components['schemas']['PatternPreview'], 'change_set' | 'skipped'> & { skipped: PatternSkippedCandidate[]; change_set?: ChangeSetPreview | null };
export type SpeciesRevision = components['schemas']['SpeciesRevision'];
export type GrowthEnvelopeForecast = components['schemas']['GrowthEnvelopeForecast'];
export type SpeciesShortlistItem = components['schemas']['SpeciesShortlistItem'];
export type RecommendationRequest = components['schemas']['RecommendationRequest'];
type BuildingScreenDefaults = 'arrangement' | 'species_revision_id' | 'size_class' | 'spacing_m' | 'spacing_policy';
export type BuildingScreenRequest = Omit<components['schemas']['BuildingScreenRequest'], BuildingScreenDefaults> & Partial<Pick<components['schemas']['BuildingScreenRequest'], BuildingScreenDefaults>>;
export type BuildingScreenTargets = components['schemas']['BuildingScreenTargets'];
export type PlanningBrief = components['schemas']['PlanningBrief'];
export type ProjectChatInput = components['schemas']['ProjectChatInput'];
export type ProjectChatReply = components['schemas']['ProjectChatReply'];
export type Conversation = components['schemas']['Conversation'];
export type ConversationSummary = components['schemas']['ConversationSummary'];
export type LegacyChatImport = components['schemas']['LegacyImport'];
export type ConversationMessageInput = components['schemas']['UserMessage'];
export type ConversationPrepareInput = components['schemas']['PrepareTask'];
export type ConversationProposalStatus = components['schemas']['ProposalStatus'];
export type RecommendationExplanation = components['schemas']['RecommendationExplanation'];
export type RecommendationPreview = Omit<components['schemas']['RecommendationPreview'], 'change_set' | 'explanations' | 'skipped'> & {
  change_set?: ChangeSetPreview | null;
  explanations: RecommendationExplanation[];
  skipped: PatternSkippedCandidate[];
};
export type BrushStroke = components['schemas']['BrushStroke'];
export type BrushPreviewRequest = components['schemas']['BrushPreviewRequest'];
export type BrushPreview = Omit<components['schemas']['BrushPreview'], 'change_set' | 'skipped'> & {
  change_set?: ChangeSetPreview | null;
  skipped: PatternSkippedCandidate[];
};
export type ScenePlantObject = components['schemas']['ScenePlantObject'];
export type SceneSnapshot = Omit<components['schemas']['SceneSnapshot'], 'objects' | 'data_gaps'> & { objects: ScenePlantObject[]; data_gaps: string[] };
export type ValidationIssue = components['schemas']['ValidationIssue'];
export type PlacementCheck = components['schemas']['PlacementCheck'];
export type ExportArtifact = components['schemas']['ExportArtifact'];
export type RegulatoryReleaseBasis = components['schemas']['RegulatoryReleaseBasis'];
export type ReleaseCreateRequest = components['schemas']['ReleaseCreateRequest'];
export type ReleaseArtifact = components['schemas']['ReleaseArtifact'];
export type ReleasePackage = components['schemas']['ReleasePackage'];
export type ProjectOperation = components['schemas']['ProjectOperation'];
export type OperationKind = components['schemas']['OperationKind'];
export type GeometrySnapshot = components['schemas']['GeometrySnapshot'];
export type AgentRunEvent = {
  sequence: number;
  kind: string;
  payload: Record<string, unknown>;
  created_at: string;
};
export type AgentRun = {
  state: {
    run_id: string;
    project_id: string;
    conversation_id?: string | null;
    status: 'queued' | 'running' | 'waiting_question' | 'waiting_approval' | 'waiting_job' | 'finished' | 'failed' | 'cancelled';
    intent: Record<string, unknown>;
    resolved_scope?: Record<string, unknown> | null;
    candidate_zone_ids: string[];
    snapshot_version: number;
    plan_version?: number | null;
    step: number;
    tool_calls: string[];
    tool_fingerprints: string[];
    evidence_refs: string[];
    last_result?: Record<string, unknown> | null;
    pending_question?: Record<string, string> | null;
    pending_approval?: Record<string, string> | null;
    outcome_ref?: string | null;
    failure?: Record<string, unknown> | null;
    max_steps: number;
  };
  revision: number;
  created_at: string;
  updated_at: string;
  events: AgentRunEvent[];
};

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
  const nonStateMutation = /\/conversations(?:\/|$)/.test(path) || /\/agent-runs(?:\/|$)/.test(path) || /\/operations(?:\/|$)/.test(path) || /\/plan\/(?:placement-check|change-sets\/preview|patterns\/preview|recommendations\/preview|brush\/preview)(?:\?|$)/.test(path) || /\/building-screen\/preview(?:\?|$)/.test(path) || /\/species\/shortlist(?:\?|$)/.test(path);
  const headers = new Headers(init?.headers);
  if (projectId && !['GET', 'HEAD', 'OPTIONS'].includes(method) && !nonStateMutation && !headers.has('If-Match')) {
    const version = projectVersions.get(projectId);
    if (version) headers.set('If-Match', `"${version}"`);
  }
  const response = await fetch(`${API_URL}${path}`, { ...init, headers });
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as { code?: string; message?: string; field_errors?: Record<string, string[]>; details?: Record<string, unknown>; detail?: string | { code?: string; message?: string; field_errors?: Record<string, string[]>; details?: Record<string, unknown> } } | null;
    const error = payload?.detail ?? payload;
    if (typeof error === 'string') throw new ApiClientError('HTTP_ERROR', error);
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
  planningAssistantStatus: () => request<components['schemas']['AssistantStatus']>('/api/planning-assistant/status'),
  interpretPlanningTask: (task: string, signal?: AbortSignal) => request<PlanningBrief>('/api/planning-assistant/interpret', { ...json({ task }), signal }),
  projectChat: (input: ProjectChatInput, signal?: AbortSignal) => request<ProjectChatReply>('/api/project-assistant/chat', { ...json(input), signal }),
  createAgentRun: (projectId: string, text: string, conversationId?: string, signal?: AbortSignal) => request<AgentRun>('/api/projects/' + encodeURIComponent(projectId) + '/agent-runs', { ...json({ text, conversation_id: conversationId }), signal }),
  getAgentRun: (projectId: string, runId: string) => request<AgentRun>('/api/projects/' + encodeURIComponent(projectId) + '/agent-runs/' + encodeURIComponent(runId)),
  runAgentRun: (projectId: string, runId: string, signal?: AbortSignal) => request<AgentRun>('/api/projects/' + encodeURIComponent(projectId) + '/agent-runs/' + encodeURIComponent(runId) + '/run', { ...json(), signal }),
  answerAgentRun: (projectId: string, runId: string, text: string) => request<AgentRun>('/api/projects/' + encodeURIComponent(projectId) + '/agent-runs/' + encodeURIComponent(runId) + '/answer', json({ text })),
  approveAgentRun: (projectId: string, runId: string, previewRef?: string) => request<AgentRun>('/api/projects/' + encodeURIComponent(projectId) + '/agent-runs/' + encodeURIComponent(runId) + '/approve', json(previewRef ? { preview_ref: previewRef } : {})),
  listConversations: (projectId: string) => request<ConversationSummary[]>(`/api/projects/${encodeURIComponent(projectId)}/conversations`),
  createConversation: (projectId: string, title = 'Новый диалог', requestId?: string) => request<Conversation>(`/api/projects/${encodeURIComponent(projectId)}/conversations`, json({ title, request_id: requestId })),
  getConversation: (projectId: string, conversationId: string) => request<Conversation>(`/api/projects/${encodeURIComponent(projectId)}/conversations/${encodeURIComponent(conversationId)}`),
  importConversation: (projectId: string, input: LegacyChatImport) => request<Conversation>(`/api/projects/${encodeURIComponent(projectId)}/conversations/import`, json(input)),
  appendConversationMessage: (projectId: string, conversationId: string, input: ConversationMessageInput) => request<Conversation>(`/api/projects/${encodeURIComponent(projectId)}/conversations/${encodeURIComponent(conversationId)}/messages`, json(input)),
  interpretConversationMessage: (projectId: string, conversationId: string, input: ConversationMessageInput, signal?: AbortSignal) => request<Conversation>(`/api/projects/${encodeURIComponent(projectId)}/conversations/${encodeURIComponent(conversationId)}/interpret`, { ...json(input), signal }),
  prepareConversationTask: (projectId: string, conversationId: string, input: ConversationPrepareInput, signal?: AbortSignal) => request<Conversation>(`/api/projects/${encodeURIComponent(projectId)}/conversations/${encodeURIComponent(conversationId)}/prepare`, { ...json(input), signal }),
  getConversationProposalStatus: (projectId: string, conversationId: string, recordId: string) => request<ConversationProposalStatus>(`/api/projects/${encodeURIComponent(projectId)}/conversations/${encodeURIComponent(conversationId)}/proposals/${encodeURIComponent(recordId)}/status`),
  declineConversationProposal: (projectId: string, conversationId: string, recordId: string, input: ConversationPrepareInput) => request<Conversation>(`/api/projects/${encodeURIComponent(projectId)}/conversations/${encodeURIComponent(conversationId)}/proposals/${encodeURIComponent(recordId)}/decline`, json(input)),
  confirmConversationProposal: (projectId: string, conversationId: string, recordId: string, input: components['schemas']['ConfirmProposal']) => request<PlanMutationResult>(`/api/projects/${encodeURIComponent(projectId)}/conversations/${encodeURIComponent(conversationId)}/proposals/${encodeURIComponent(recordId)}/confirm`, json(input)),
  listSpecies: (kind?: 'tree' | 'shrub') => request<SpeciesRevision[]>(`/api/species${kind ? `?kind=${kind}` : ''}`),
  createProject: (name: string) => request<Project>('/api/projects', json({ name })),
  listProjects: () => request<ProjectSummary[]>('/api/projects'),
  deleteProject: (projectId: string) => request<void>(`/api/projects/${projectId}`, { method: 'DELETE' }),
  getProject: (projectId: string, includeGeometry = true) => request<Project>(`/api/projects/${projectId}?include_geometry=${includeGeometry}`),
  getMapFeatures: (projectId: string, extent: [number, number, number, number], resolution: number, signal?: AbortSignal) => {
    const params = new URLSearchParams({ min_x: String(extent[0]), min_y: String(extent[1]), max_x: String(extent[2]), max_y: String(extent[3]), resolution: String(resolution) });
    return request<GeometrySnapshot>(`/api/projects/${projectId}/map-features?${params}`, { signal });
  },
  getDataPassport: (projectId: string) => request<DataPassport>(`/api/projects/${projectId}/data-passport`),
  uploadDxf: (projectId: string, file: File) => {
    const body = new FormData();
    body.append('file', file);
    return request<Project>(`/api/projects/${projectId}/source-dxf`, { method: 'POST', body });
  },
  uploadReleaseBundle: (projectId: string, file: File) => {
    const body = new FormData();
    body.append('file', file);
    return request<Project>(`/api/projects/${projectId}/release-bundle`, { method: 'POST', body });
  },
  sourceDownloadUrl: (projectId: string) => `${API_URL}/api/projects/${projectId}/source-dxf/download`,
  saveMappings: (projectId: string, mappings: LayerMapping[]) => request<Project>(`/api/projects/${projectId}/layer-mappings`, { ...json({ mappings }), method: 'PUT' }),
  savePlantingZones: (projectId: string, zones: PlantingZoneAssignment[]) => request<Project>(`/api/projects/${projectId}/planting-zones`, { ...json({ zones }), method: 'PUT' }),
  previewPlantingZone: (projectId: string, zone: PlantingZoneAssignment) => request<PlantingZonePreview>(`/api/projects/${projectId}/planting-zones/preview`, json(zone)),
  startGeometryOperation: (projectId: string) => request<ProjectOperation>(`/api/projects/${projectId}/operations/geometry`, json()),
  getOperation: (projectId: string, operationId: string) => request<ProjectOperation>(`/api/projects/${projectId}/operations/${operationId}`),
  getLatestOperation: (projectId: string, kind: OperationKind) => request<ProjectOperation | null>(`/api/projects/${projectId}/operations/latest?kind=${encodeURIComponent(kind)}`),
  cancelOperation: (projectId: string, operationId: string) => request<ProjectOperation>(`/api/projects/${projectId}/operations/${operationId}/cancel`, json()),
  createManualPlan: (projectId: string) => request<Project>(`/api/projects/${projectId}/plan/manual`, json()),
  addPlanObject: (projectId: string, object: { kind: 'tree' | 'shrub'; x: number; y: number; radius?: number; species_revision_id?: string; size_class?: 'sapling' | 'standard' | 'large' }) => request<Plan>(`/api/projects/${projectId}/plan/objects`, json(object)),
  checkPlacement: (projectId: string, object: { kind: 'tree' | 'shrub'; x: number; y: number; radius?: number }, signal?: AbortSignal) => request<PlacementCheck>(`/api/projects/${projectId}/plan/placement-check`, { ...json(object), signal }),
  previewPlanChanges: (projectId: string, draft: PlanChangeSetDraft, signal?: AbortSignal) => request<ChangeSetPreview>(`/api/projects/${projectId}/plan/change-sets/preview`, { ...json(draft), signal }),
  applyPlanChanges: (projectId: string, preview: ChangeSetPreview) => request<PlanMutationResult>(`/api/projects/${projectId}/plan/change-sets/apply`, json({ preview_id: preview.id, digest: preview.digest, base_plan_version: preview.base_plan_version } satisfies PlanChangeSetApplyRequest)),
  listPlacementMasks: (projectId: string) => request<PlacementMaskPreset[]>(`/api/projects/${projectId}/plan/placement-masks`),
  previewPlanPattern: (projectId: string, pattern: PatternPreviewRequest, signal?: AbortSignal) => request<PatternPreview>(`/api/projects/${projectId}/plan/patterns/preview`, { ...json(pattern), signal }),
  previewRecommendation: (projectId: string, recommendation: RecommendationRequest, signal?: AbortSignal) => request<RecommendationPreview>(`/api/projects/${projectId}/plan/recommendations/preview`, { ...json(recommendation), signal }),
  getBuildingScreenTargets: (projectId: string, zoneIds: string[], signal?: AbortSignal) => request<BuildingScreenTargets>(`/api/projects/${projectId}/building-screen/targets?${new URLSearchParams(zoneIds.map(id => ['zone_ids', id]))}`, { signal }),
  previewBuildingScreen: (projectId: string, draft: BuildingScreenRequest, signal?: AbortSignal) => request<RecommendationPreview>(`/api/projects/${projectId}/building-screen/preview`, { ...json(draft), signal }),
  previewBrush: (projectId: string, brush: BrushPreviewRequest, signal?: AbortSignal) => request<BrushPreview>(`/api/projects/${projectId}/plan/brush/preview`, { ...json(brush), signal }),
  getPlanScene: (projectId: string, horizonYear: number, signal?: AbortSignal) => request<SceneSnapshot>(`/api/projects/${projectId}/plan/scene?horizon_year=${horizonYear}`, { signal }),
  shortlistSpecies: (projectId: string, scope: string[] | { zoneIds: string[] }) => request<SpeciesShortlistItem[]>(`/api/projects/${projectId}/species/shortlist`, json(Array.isArray(scope) ? { object_ids: scope } : { zone_ids: scope.zoneIds })),
  updatePlanObject: (projectId: string, objectId: string, object: { x?: number; y?: number; radius?: number }) => request<Plan>(`/api/projects/${projectId}/plan/objects/${objectId}`, { ...json(object), method: 'PATCH' }),
  deletePlanObjects: (projectId: string, ids: string[]) => request<Plan>(`/api/projects/${projectId}/plan/objects/delete`, json({ ids })),
  getPlanHistory: (projectId: string) => request<PlanHistoryState>(`/api/projects/${projectId}/plan/history`),
  undoPlanChange: (projectId: string) => request<Project>(`/api/projects/${projectId}/plan/history/undo`, json()),
  redoPlanChange: (projectId: string) => request<Project>(`/api/projects/${projectId}/plan/history/redo`, json()),
  createExport: (projectId: string) => request<ExportArtifact>(`/api/projects/${projectId}/exports`, json()),
  createRelease: (projectId: string, release: ReleaseCreateRequest) => request<ReleasePackage>(`/api/projects/${projectId}/releases`, json(release)),
  getRelease: (projectId: string, releaseId: string) => request<ReleasePackage>(`/api/projects/${projectId}/releases/${releaseId}`),
  downloadUrl: (path: string) => `${API_URL}${path}`,
};
