import * as sdk from '../sdk.generated';
import type {
  LayerMapping,
  LayerRecognition,
  LayerRecognitionSubscriptionOptions,
  NativeFaceDecision,
  OperationKind,
  OperationSubscriptionOptions,
  PlantingZoneAssignment,
  Project,
  ProjectOperation,
  ProjectWriteOptions,
  SourceAreaGroupRequest,
  SourceObjectDecisionRequest,
} from '../contracts';
import type { WireSchema } from '../contracts/wire';
import { API_URL, json } from '../transport/request';
import {
  awaitMappingWrite,
  withProjectWriteOptions,
} from '../transport/projectWrite';
import { readResource } from '../transport/readResource';
import { subscribeEvents } from '../transport/events';
import { ApiClientError } from '../transport/errors';
import { EVENT_STREAM_FAILURE_MESSAGE } from '../transport/policy';

const readProject = (
  projectId: string,
  includeGeometry = true,
  signal?: AbortSignal,
) =>
  readResource(
    (options) =>
      sdk.getProject(projectId, { include_geometry: includeGeometry }, options),
    'project',
    signal,
  );

export const projectsApi = {
  getNativeFaces: sdk.sourceNativeFaces,
  decideNativeFace: (
    projectId: string,
    input: NativeFaceDecision,
    options: ProjectWriteOptions,
  ) =>
    sdk.decideSourceNativeFace(
      projectId,
      input,
      withProjectWriteOptions({}, options),
    ),
  createProject: (name: string) => sdk.createProject({ name }),
  listProjects: (signal?: AbortSignal) =>
    readResource(sdk.listProjects, 'library', signal),
  deleteProject: sdk.deleteProject,
  getProjectRead: readProject,
  getProject: (projectId: string, includeGeometry = true): Promise<Project> =>
    readProject(projectId, includeGeometry),
  getMapFeatures: (
    projectId: string,
    extent: [number, number, number, number],
    resolution: number,
    signal?: AbortSignal,
  ) =>
    readResource(
      (options) =>
        sdk.getMapFeatures(
          projectId,
          {
            min_x: extent[0],
            min_y: extent[1],
            max_x: extent[2],
            max_y: extent[3],
            resolution,
          },
          options,
        ),
      'map',
      signal,
    ),
  getDataPassport: sdk.getDataPassport,
  getLayerRecognition: (projectId: string, signal?: AbortSignal) =>
    readResource(
      (options) => sdk.sourceLayerReview(projectId, options),
      'recognition',
      signal,
    ),
  getLayerRecognitionStatus: (projectId: string, signal?: AbortSignal) =>
    readResource(
      (options) => sdk.sourceLayerReviewStatus(projectId, options),
      'recognition',
      signal,
    ),
  subscribeLayerRecognition: (
    projectId: string,
    options: LayerRecognitionSubscriptionOptions,
  ) =>
    subscribeEvents<unknown>(sdk.getSourceLayerReviewEventsUrl(projectId), {
      event: 'recognition',
      signal: options.signal,
      onData: (result) => options.onResult(recognitionEvent(result)),
      onError: options.onError,
      isTerminal: (result) => {
        const accepted = recognitionEvent(result);
        return accepted === null || accepted.status !== 'running';
      },
    }),
  retryLayerRecognition: (projectId: string) =>
    sdk.retrySourceLayerReview(projectId, json()),
  getSourceObjectReview: (projectId: string, layer: string, offset = 0) =>
    sdk.sourceObjectReview(projectId, { layer, offset }),
  decideSourceObject: (
    projectId: string,
    input: SourceObjectDecisionRequest,
    options: ProjectWriteOptions,
  ) =>
    sdk.decideSourceObject(
      projectId,
      input,
      withProjectWriteOptions({}, options),
    ),
  getSourceObjectContext: (projectId: string, route: string, scale = 1) =>
    sdk.sourceObjectContext(projectId, { route, scale }),
  getSourceReadIssues: sdk.sourceReadIssues,
  checkSourceAreaGroup: sdk.reviewSourceAreaGroup,
  acceptSourceAreaGroup: (
    projectId: string,
    input: SourceAreaGroupRequest,
    options: ProjectWriteOptions,
  ) =>
    sdk.acceptSourceAreaGroup(
      projectId,
      input,
      withProjectWriteOptions({}, options),
    ),
  removeSourceAreaGroup: (
    projectId: string,
    groupId: string,
    options: ProjectWriteOptions,
  ) =>
    sdk.removeSourceAreaGroup(
      projectId,
      groupId,
      withProjectWriteOptions({}, options),
    ),
  getNativeAreaPreview: (projectId: string, proposalId: string) =>
    sdk.nativeAreaPreview(projectId, { proposal_id: proposalId }),
  sourceDownloadUrl: (projectId: string) =>
    `${API_URL}${sdk.getSourceDxfDownloadUrl(projectId)}`,
  getNativeDxfSourceAsset: (projectId: string, signal?: AbortSignal) =>
    sdk.nativeDxfAsset(projectId, { signal }),
  nativeDxfSourceFileUrl: (fileUrl: string) => `${API_URL}${fileUrl}`,
  saveMappings: (projectId: string, mappings: LayerMapping[]) =>
    awaitMappingWrite((options) =>
      sdk.saveMappings(projectId, { mappings }, options),
    ),
  acceptPartialGeometry: (
    projectId: string,
    sourceSha256: string,
    options?: ProjectWriteOptions,
  ) =>
    sdk.acceptPartialGeometry(
      projectId,
      { source_sha256: sourceSha256 },
      withProjectWriteOptions({}, options),
    ),
  decideNativeArea: (
    projectId: string,
    input: WireSchema<'NativeAreaProposalDecisionRequest'>,
    options?: ProjectWriteOptions,
  ) =>
    sdk.decideNativeArea(
      projectId,
      input,
      withProjectWriteOptions({}, options),
    ),
  savePlantingZones: (
    projectId: string,
    zones: PlantingZoneAssignment[],
    options?: ProjectWriteOptions,
  ) =>
    sdk.savePlantingZones(
      projectId,
      { zones },
      withProjectWriteOptions({}, options),
    ),
  previewPlantingZone: sdk.previewPlantingZone,
  startGeometryOperation: (projectId: string) =>
    sdk.startGeometryOperation(projectId, json()),
  startCapturePreparation: (projectId: string) =>
    sdk.startCapturePreparation(projectId, json()),
  openSourceEditor: (projectId: string) =>
    sdk.openSourceEditor(projectId, json()),
  getOperation: (
    projectId: string,
    operationId: string,
    signal?: AbortSignal,
  ) =>
    readResource(
      (options) => sdk.getOperation(projectId, operationId, options),
      'operation',
      signal,
    ),
  subscribeOperation: (
    projectId: string,
    operationId: string,
    options: OperationSubscriptionOptions,
  ) =>
    subscribeEvents<unknown>(
      sdk.getOperationEventsUrl(projectId, operationId),
      {
        event: 'operation',
        signal: options.signal,
        onData: (result) => options.onResult(operationEvent(result)),
        onError: options.onError,
        isTerminal: (result) =>
          !['queued', 'running', 'cancelling'].includes(
            operationEvent(result).status,
          ),
      },
    ),
  getLatestOperation: (
    projectId: string,
    kind: OperationKind,
    signal?: AbortSignal,
  ) =>
    readResource(
      (options) => sdk.getLatestOperation(projectId, { kind }, options),
      'operation',
      signal,
    ),
  cancelOperation: (projectId: string, operationId: string) =>
    sdk.cancelOperation(projectId, operationId, json()),
};

/** Поток допускает только снимок задачи: неполный объект не может стать сохранёнными назначениями. */
function recognitionEvent(value: unknown): LayerRecognition | null {
  if (value === null) return null;
  const object = (item: unknown): item is Record<string, unknown> =>
    typeof item === 'object' && item !== null;
  const strings = (item: unknown) =>
    Array.isArray(item) && item.every((entry) => typeof entry === 'string');
  const valid =
    object(value) &&
    (typeof value.source_sha256 === 'string' || value.source_sha256 === null) &&
    typeof value.provider === 'string' &&
    ['running', 'completed', 'failed', 'unconfigured'].includes(
      String(value.status),
    ) &&
    [
      'processed_count',
      'total_count',
      'stage_processed_count',
      'stage_total_count',
    ].every(
      (field) => Number.isInteger(value[field]) && Number(value[field]) >= 0,
    ) &&
    (value.active_layer_ids === undefined || strings(value.active_layer_ids)) &&
    Array.isArray(value.categories) &&
    value.categories.every(
      (item) =>
        object(item) &&
        typeof item.category === 'string' &&
        (item.kind === null || typeof item.kind === 'string') &&
        typeof item.label === 'string',
    ) &&
    Array.isArray(value.proposals) &&
    value.proposals.every(
      (item) =>
        object(item) &&
        typeof item.layer_id === 'string' &&
        (item.category === null || typeof item.category === 'string') &&
        ['high', 'medium', 'low'].includes(String(item.confidence)) &&
        strings(item.evidence) &&
        strings(item.unresolved),
    );
  if (!valid)
    throw new ApiClientError('INVALID_RESPONSE', EVENT_STREAM_FAILURE_MESSAGE);
  return value as LayerRecognition;
}

/** Событие не может подменить идентичность, основание или счётчики квитанции операции. */
function operationEvent(value: unknown): ProjectOperation {
  const object = (item: unknown): item is Record<string, unknown> =>
    typeof item === 'object' && item !== null;
  const valid =
    object(value) &&
    typeof value.id === 'string' &&
    typeof value.project_id === 'string' &&
    [
      'prepare_cad_capture',
      'calculate_geometry',
      'inspect_cad_package',
      'prepare_cad_preview',
      'prepare_cad_project',
    ].includes(String(value.kind)) &&
    [
      'queued',
      'running',
      'cancelling',
      'completed',
      'failed',
      'cancelled',
      'interrupted',
    ].includes(String(value.status)) &&
    Number.isSafeInteger(value.project_state_version) &&
    Number(value.project_state_version) >= 1 &&
    Number.isInteger(value.progress) &&
    Number(value.progress) >= 0 &&
    Number(value.progress) <= 100 &&
    ['determinate', 'indeterminate'].includes(String(value.progress_mode)) &&
    typeof value.stage === 'string' &&
    ['processed_items', 'total_items'].every(
      (field) =>
        !(field in value) ||
        value[field] == null ||
        (Number.isInteger(value[field]) && Number(value[field]) >= 0),
    );
  if (!valid)
    throw new ApiClientError('INVALID_RESPONSE', EVENT_STREAM_FAILURE_MESSAGE);
  return value as ProjectOperation;
}
