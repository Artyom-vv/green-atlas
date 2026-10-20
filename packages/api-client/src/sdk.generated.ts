/**
 * Сгенерировано Orval из OpenAPI. Изменяйте контракт, а не этот файл.
 */
import type {
  AcceptPartialGeometryRequest,
  AssortmentInventory,
  AutomaticPlacementPreview,
  AutomaticPlacementRequest,
  BodyUploadAutocadLiveApiProjectsProjectIdSourceAutocadLivePost,
  BrushPreview,
  BrushPreviewRequest,
  BuildingScreenRequest,
  BuildingScreenTargets,
  BuildingScreenTargetsParams,
  CadSourceAsset,
  ChangeSetPreview,
  DataPassport,
  ExportArtifact,
  FillPatternRequest,
  GeometrySnapshot,
  GetAssortmentCatalogParams,
  GetLatestOperationParams,
  GetMapFeaturesParams,
  GetPlanGrowthForecastParams,
  GetPlanSceneParams,
  GetProjectParams,
  GetSceneViewParams,
  GetSpeciesCatalogParams,
  GrowthForecastResponse,
  Health200,
  LayerMappingRequest,
  LayerRecognition,
  LocationAlignment,
  LocationAlignmentRequest,
  LocationApplyRequest,
  LocationDraft,
  LocationResolveRequest,
  LocationServiceSettings,
  LocationServiceSettingsUpdate,
  LocationState,
  LocationSuggestions,
  LocationSuggestionsRequest,
  NativeAreaPreview,
  NativeAreaPreviewParams,
  NativeAreaProposalDecisionRequest,
  NativeDxfSourceAsset,
  NativeFaceDecision,
  NativeFaceReview,
  PatternPreview,
  PlacementCheck,
  PlacementCheckRequest,
  PlacementMaskPreset,
  PlacementMaskRequest,
  Plan,
  PlanChangeReceiptResult,
  PlanChangeSetApplyRequest,
  PlanChangeSetDraft,
  PlanHistoryState,
  PlanMutationResult,
  PlanObjectCreate,
  PlanObjectUpdate,
  PlanObjectsDeleteRequest,
  PlantingZoneAssignment,
  PlantingZonePreview,
  PlantingZonesRequest,
  Project,
  ProjectCreate,
  ProjectOperation,
  ProjectRead,
  ProjectSummary,
  ReadPlanChangeReceiptParams,
  RecommendationPreview,
  RecommendationRequest,
  ReleaseCreateRequest,
  ReleasePackage,
  RowPatternRequest,
  SceneRenderJob,
  SceneRenderPreparation,
  SceneRenderPreparationRequest,
  SceneRenderRequest,
  SceneSnapshot,
  SceneView,
  SourceAreaGroupCheck,
  SourceAreaGroupRequest,
  SourceDxfDownloadParams,
  SourceObjectContext,
  SourceObjectContextParams,
  SourceObjectDecisionRequest,
  SourceObjectReviewPage,
  SourceObjectReviewParams,
  SourceReadIssues,
  SpeciesRevision,
  SpeciesShortlistItem,
  SpeciesShortlistRequest,
} from '@green/api-client/schema-types';

import { request } from './transport/request';
export const getGetSpeciesCatalogUrl = (params?: GetSpeciesCatalogParams) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {
    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : String(value));
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0
    ? `/api/species?${stringifiedParams}`
    : `/api/species`;
};

export const getSpeciesCatalog = async (
  params?: GetSpeciesCatalogParams,
  options?: Parameters<typeof request>[1],
): Promise<SpeciesRevision[]> => {
  return request<SpeciesRevision[]>(getGetSpeciesCatalogUrl(params), {
    ...options,
    method: 'GET',
  });
};

export const getGetAssortmentCatalogUrl = (
  params?: GetAssortmentCatalogParams,
) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {
    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : String(value));
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0
    ? `/api/species/assortment?${stringifiedParams}`
    : `/api/species/assortment`;
};

export const getAssortmentCatalog = async (
  params?: GetAssortmentCatalogParams,
  options?: Parameters<typeof request>[1],
): Promise<AssortmentInventory> => {
  return request<AssortmentInventory>(getGetAssortmentCatalogUrl(params), {
    ...options,
    method: 'GET',
  });
};

export const getHealthUrl = () => {
  return `/api/health`;
};

export const health = async (
  options?: Parameters<typeof request>[1],
): Promise<Health200> => {
  return request<Health200>(getHealthUrl(), {
    ...options,
    method: 'GET',
  });
};

export const getCreateProjectUrl = () => {
  return `/api/projects`;
};

export const createProject = async (
  projectCreate: ProjectCreate,
  options?: Parameters<typeof request>[1],
): Promise<Project> => {
  const getHeaders = (
    h?: NonNullable<RequestInit['headers']>,
  ): Record<string, string | readonly string[]> => {
    if (!h) return {};
    if (h instanceof Headers) return Object.fromEntries(h.entries());
    if (Symbol.iterator in h) {
      return Object.fromEntries(
        Array.from(
          h as Iterable<Iterable<string>>,
          (entry) => Array.from(entry) as [string, string],
        ),
      );
    }
    const headers: Record<string, string | readonly string[]> = {};
    for (const [name, value] of Object.entries<
      string | readonly string[] | undefined
    >(h)) {
      if (value !== undefined) headers[name] = value;
    }
    return headers;
  };
  return request<Project>(getCreateProjectUrl(), {
    ...options,
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...getHeaders(options?.headers),
    },
    body: JSON.stringify(projectCreate),
  });
};

export const getListProjectsUrl = () => {
  return `/api/projects`;
};

export const listProjects = async (
  options?: Parameters<typeof request>[1],
): Promise<ProjectSummary[]> => {
  return request<ProjectSummary[]>(getListProjectsUrl(), {
    ...options,
    method: 'GET',
  });
};

export const getGetProjectUrl = (
  projectId: string,
  params?: GetProjectParams,
) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {
    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : String(value));
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0
    ? `/api/projects/${encodeURIComponent(String(projectId))}?${stringifiedParams}`
    : `/api/projects/${encodeURIComponent(String(projectId))}`;
};

export const getProject = async (
  projectId: string,
  params?: GetProjectParams,
  options?: Parameters<typeof request>[1],
): Promise<ProjectRead> => {
  return request<ProjectRead>(getGetProjectUrl(projectId, params), {
    ...options,
    method: 'GET',
  });
};

export const getDeleteProjectUrl = (projectId: string) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}`;
};

export const deleteProject = async (
  projectId: string,
  options?: Parameters<typeof request>[1],
): Promise<void> => {
  return request<void>(getDeleteProjectUrl(projectId), {
    ...options,
    method: 'DELETE',
  });
};

export const getSourceLayerReviewUrl = (projectId: string) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/source-layer-review`;
};

export const sourceLayerReview = async (
  projectId: string,
  options?: Parameters<typeof request>[1],
): Promise<LayerRecognition> => {
  return request<LayerRecognition>(getSourceLayerReviewUrl(projectId), {
    ...options,
    method: 'GET',
  });
};

export const getRetrySourceLayerReviewUrl = (projectId: string) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/source-layer-review`;
};

export const retrySourceLayerReview = async (
  projectId: string,
  options?: Parameters<typeof request>[1],
): Promise<LayerRecognition> => {
  return request<LayerRecognition>(getRetrySourceLayerReviewUrl(projectId), {
    ...options,
    method: 'POST',
  });
};

export const getSourceLayerReviewStatusUrl = (projectId: string) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/source-layer-review/status`;
};

export const sourceLayerReviewStatus = async (
  projectId: string,
  options?: Parameters<typeof request>[1],
): Promise<LayerRecognition | null> => {
  return request<LayerRecognition | null>(
    getSourceLayerReviewStatusUrl(projectId),
    {
      ...options,
      method: 'GET',
    },
  );
};

export const getSourceLayerReviewEventsUrl = (projectId: string) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/source-layer-review/events`;
};

export const sourceLayerReviewEvents = async (
  projectId: string,
  options?: Parameters<typeof request>[1],
): Promise<string> => {
  return request<string>(getSourceLayerReviewEventsUrl(projectId), {
    ...options,
    method: 'GET',
  });
};

export const getUploadAutocadLiveUrl = (projectId: string) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/source-autocad-live`;
};

export const uploadAutocadLive = async (
  projectId: string,
  bodyUploadAutocadLiveApiProjectsProjectIdSourceAutocadLivePost: BodyUploadAutocadLiveApiProjectsProjectIdSourceAutocadLivePost,
  options?: Parameters<typeof request>[1],
): Promise<Project> => {
  const formData = new FormData();
  formData.append(
    `file`,
    bodyUploadAutocadLiveApiProjectsProjectIdSourceAutocadLivePost.file,
  );
  formData.append(
    `autocad_version`,
    bodyUploadAutocadLiveApiProjectsProjectIdSourceAutocadLivePost.autocad_version,
  );
  formData.append(
    `target`,
    bodyUploadAutocadLiveApiProjectsProjectIdSourceAutocadLivePost.target,
  );

  return request<Project>(getUploadAutocadLiveUrl(projectId), {
    ...options,
    method: 'POST',
    body: formData,
  });
};

export const getNativeDxfAssetUrl = (projectId: string) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/source-dxf/asset`;
};

export const nativeDxfAsset = async (
  projectId: string,
  options?: Parameters<typeof request>[1],
): Promise<NativeDxfSourceAsset> => {
  return request<NativeDxfSourceAsset>(getNativeDxfAssetUrl(projectId), {
    ...options,
    method: 'GET',
  });
};

export const getSourceDxfDownloadUrl = (
  projectId: string,
  params?: SourceDxfDownloadParams,
) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {
    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : String(value));
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0
    ? `/api/projects/${encodeURIComponent(String(projectId))}/source-dxf/download?${stringifiedParams}`
    : `/api/projects/${encodeURIComponent(String(projectId))}/source-dxf/download`;
};

export const sourceDxfDownload = async (
  projectId: string,
  params?: SourceDxfDownloadParams,
  options?: Parameters<typeof request>[1],
): Promise<unknown> => {
  return request<unknown>(getSourceDxfDownloadUrl(projectId, params), {
    ...options,
    method: 'GET',
  });
};

export const getSaveMappingsUrl = (projectId: string) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/layer-mappings`;
};

export const saveMappings = async (
  projectId: string,
  layerMappingRequest: LayerMappingRequest,
  options?: Parameters<typeof request>[1],
): Promise<Project> => {
  const getHeaders = (
    h?: NonNullable<RequestInit['headers']>,
  ): Record<string, string | readonly string[]> => {
    if (!h) return {};
    if (h instanceof Headers) return Object.fromEntries(h.entries());
    if (Symbol.iterator in h) {
      return Object.fromEntries(
        Array.from(
          h as Iterable<Iterable<string>>,
          (entry) => Array.from(entry) as [string, string],
        ),
      );
    }
    const headers: Record<string, string | readonly string[]> = {};
    for (const [name, value] of Object.entries<
      string | readonly string[] | undefined
    >(h)) {
      if (value !== undefined) headers[name] = value;
    }
    return headers;
  };
  return request<Project>(getSaveMappingsUrl(projectId), {
    ...options,
    method: 'PUT',
    headers: {
      'Content-Type': 'application/json',
      ...getHeaders(options?.headers),
    },
    body: JSON.stringify(layerMappingRequest),
  });
};

export const getAcceptPartialGeometryUrl = (projectId: string) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/source-partial-geometry/accept`;
};

export const acceptPartialGeometry = async (
  projectId: string,
  acceptPartialGeometryRequest: AcceptPartialGeometryRequest,
  options?: Parameters<typeof request>[1],
): Promise<Project> => {
  const getHeaders = (
    h?: NonNullable<RequestInit['headers']>,
  ): Record<string, string | readonly string[]> => {
    if (!h) return {};
    if (h instanceof Headers) return Object.fromEntries(h.entries());
    if (Symbol.iterator in h) {
      return Object.fromEntries(
        Array.from(
          h as Iterable<Iterable<string>>,
          (entry) => Array.from(entry) as [string, string],
        ),
      );
    }
    const headers: Record<string, string | readonly string[]> = {};
    for (const [name, value] of Object.entries<
      string | readonly string[] | undefined
    >(h)) {
      if (value !== undefined) headers[name] = value;
    }
    return headers;
  };
  return request<Project>(getAcceptPartialGeometryUrl(projectId), {
    ...options,
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...getHeaders(options?.headers),
    },
    body: JSON.stringify(acceptPartialGeometryRequest),
  });
};

export const getDecideNativeAreaUrl = (projectId: string) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/source-native-area/decision`;
};

export const decideNativeArea = async (
  projectId: string,
  nativeAreaProposalDecisionRequest: NativeAreaProposalDecisionRequest,
  options?: Parameters<typeof request>[1],
): Promise<Project> => {
  const getHeaders = (
    h?: NonNullable<RequestInit['headers']>,
  ): Record<string, string | readonly string[]> => {
    if (!h) return {};
    if (h instanceof Headers) return Object.fromEntries(h.entries());
    if (Symbol.iterator in h) {
      return Object.fromEntries(
        Array.from(
          h as Iterable<Iterable<string>>,
          (entry) => Array.from(entry) as [string, string],
        ),
      );
    }
    const headers: Record<string, string | readonly string[]> = {};
    for (const [name, value] of Object.entries<
      string | readonly string[] | undefined
    >(h)) {
      if (value !== undefined) headers[name] = value;
    }
    return headers;
  };
  return request<Project>(getDecideNativeAreaUrl(projectId), {
    ...options,
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...getHeaders(options?.headers),
    },
    body: JSON.stringify(nativeAreaProposalDecisionRequest),
  });
};

export const getSourceObjectReviewUrl = (
  projectId: string,
  params?: SourceObjectReviewParams,
) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {
    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : String(value));
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0
    ? `/api/projects/${encodeURIComponent(String(projectId))}/source-object-review?${stringifiedParams}`
    : `/api/projects/${encodeURIComponent(String(projectId))}/source-object-review`;
};

export const sourceObjectReview = async (
  projectId: string,
  params?: SourceObjectReviewParams,
  options?: Parameters<typeof request>[1],
): Promise<SourceObjectReviewPage> => {
  return request<SourceObjectReviewPage>(
    getSourceObjectReviewUrl(projectId, params),
    {
      ...options,
      method: 'GET',
    },
  );
};

export const getSourceObjectContextUrl = (
  projectId: string,
  params: SourceObjectContextParams,
) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {
    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : String(value));
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0
    ? `/api/projects/${encodeURIComponent(String(projectId))}/source-object-context?${stringifiedParams}`
    : `/api/projects/${encodeURIComponent(String(projectId))}/source-object-context`;
};

export const sourceObjectContext = async (
  projectId: string,
  params: SourceObjectContextParams,
  options?: Parameters<typeof request>[1],
): Promise<SourceObjectContext> => {
  return request<SourceObjectContext>(
    getSourceObjectContextUrl(projectId, params),
    {
      ...options,
      method: 'GET',
    },
  );
};

export const getSourceReadIssuesUrl = (projectId: string) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/source-read-issues`;
};

export const sourceReadIssues = async (
  projectId: string,
  options?: Parameters<typeof request>[1],
): Promise<SourceReadIssues> => {
  return request<SourceReadIssues>(getSourceReadIssuesUrl(projectId), {
    ...options,
    method: 'GET',
  });
};

export const getSourceNativeFacesUrl = (projectId: string) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/source-native-faces`;
};

export const sourceNativeFaces = async (
  projectId: string,
  options?: Parameters<typeof request>[1],
): Promise<NativeFaceReview> => {
  return request<NativeFaceReview>(getSourceNativeFacesUrl(projectId), {
    ...options,
    method: 'GET',
  });
};

export const getDecideSourceNativeFaceUrl = (projectId: string) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/source-native-faces/decision`;
};

export const decideSourceNativeFace = async (
  projectId: string,
  nativeFaceDecision: NativeFaceDecision,
  options?: Parameters<typeof request>[1],
): Promise<Project> => {
  const getHeaders = (
    h?: NonNullable<RequestInit['headers']>,
  ): Record<string, string | readonly string[]> => {
    if (!h) return {};
    if (h instanceof Headers) return Object.fromEntries(h.entries());
    if (Symbol.iterator in h) {
      return Object.fromEntries(
        Array.from(
          h as Iterable<Iterable<string>>,
          (entry) => Array.from(entry) as [string, string],
        ),
      );
    }
    const headers: Record<string, string | readonly string[]> = {};
    for (const [name, value] of Object.entries<
      string | readonly string[] | undefined
    >(h)) {
      if (value !== undefined) headers[name] = value;
    }
    return headers;
  };
  return request<Project>(getDecideSourceNativeFaceUrl(projectId), {
    ...options,
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...getHeaders(options?.headers),
    },
    body: JSON.stringify(nativeFaceDecision),
  });
};

export const getReviewSourceAreaGroupUrl = (projectId: string) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/source-area-groups/check`;
};

export const reviewSourceAreaGroup = async (
  projectId: string,
  sourceAreaGroupRequest: SourceAreaGroupRequest,
  options?: Parameters<typeof request>[1],
): Promise<SourceAreaGroupCheck> => {
  const getHeaders = (
    h?: NonNullable<RequestInit['headers']>,
  ): Record<string, string | readonly string[]> => {
    if (!h) return {};
    if (h instanceof Headers) return Object.fromEntries(h.entries());
    if (Symbol.iterator in h) {
      return Object.fromEntries(
        Array.from(
          h as Iterable<Iterable<string>>,
          (entry) => Array.from(entry) as [string, string],
        ),
      );
    }
    const headers: Record<string, string | readonly string[]> = {};
    for (const [name, value] of Object.entries<
      string | readonly string[] | undefined
    >(h)) {
      if (value !== undefined) headers[name] = value;
    }
    return headers;
  };
  return request<SourceAreaGroupCheck>(getReviewSourceAreaGroupUrl(projectId), {
    ...options,
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...getHeaders(options?.headers),
    },
    body: JSON.stringify(sourceAreaGroupRequest),
  });
};

export const getAcceptSourceAreaGroupUrl = (projectId: string) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/source-area-groups`;
};

export const acceptSourceAreaGroup = async (
  projectId: string,
  sourceAreaGroupRequest: SourceAreaGroupRequest,
  options?: Parameters<typeof request>[1],
): Promise<Project> => {
  const getHeaders = (
    h?: NonNullable<RequestInit['headers']>,
  ): Record<string, string | readonly string[]> => {
    if (!h) return {};
    if (h instanceof Headers) return Object.fromEntries(h.entries());
    if (Symbol.iterator in h) {
      return Object.fromEntries(
        Array.from(
          h as Iterable<Iterable<string>>,
          (entry) => Array.from(entry) as [string, string],
        ),
      );
    }
    const headers: Record<string, string | readonly string[]> = {};
    for (const [name, value] of Object.entries<
      string | readonly string[] | undefined
    >(h)) {
      if (value !== undefined) headers[name] = value;
    }
    return headers;
  };
  return request<Project>(getAcceptSourceAreaGroupUrl(projectId), {
    ...options,
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...getHeaders(options?.headers),
    },
    body: JSON.stringify(sourceAreaGroupRequest),
  });
};

export const getRemoveSourceAreaGroupUrl = (
  projectId: string,
  groupId: string,
) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/source-area-groups/${encodeURIComponent(String(groupId))}`;
};

export const removeSourceAreaGroup = async (
  projectId: string,
  groupId: string,
  options?: Parameters<typeof request>[1],
): Promise<Project> => {
  return request<Project>(getRemoveSourceAreaGroupUrl(projectId, groupId), {
    ...options,
    method: 'DELETE',
  });
};

export const getDecideSourceObjectUrl = (projectId: string) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/source-object-review/decision`;
};

export const decideSourceObject = async (
  projectId: string,
  sourceObjectDecisionRequest: SourceObjectDecisionRequest,
  options?: Parameters<typeof request>[1],
): Promise<Project> => {
  const getHeaders = (
    h?: NonNullable<RequestInit['headers']>,
  ): Record<string, string | readonly string[]> => {
    if (!h) return {};
    if (h instanceof Headers) return Object.fromEntries(h.entries());
    if (Symbol.iterator in h) {
      return Object.fromEntries(
        Array.from(
          h as Iterable<Iterable<string>>,
          (entry) => Array.from(entry) as [string, string],
        ),
      );
    }
    const headers: Record<string, string | readonly string[]> = {};
    for (const [name, value] of Object.entries<
      string | readonly string[] | undefined
    >(h)) {
      if (value !== undefined) headers[name] = value;
    }
    return headers;
  };
  return request<Project>(getDecideSourceObjectUrl(projectId), {
    ...options,
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...getHeaders(options?.headers),
    },
    body: JSON.stringify(sourceObjectDecisionRequest),
  });
};

export const getNativeAreaPreviewUrl = (
  projectId: string,
  params: NativeAreaPreviewParams,
) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {
    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : String(value));
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0
    ? `/api/projects/${encodeURIComponent(String(projectId))}/source-native-area/preview?${stringifiedParams}`
    : `/api/projects/${encodeURIComponent(String(projectId))}/source-native-area/preview`;
};

export const nativeAreaPreview = async (
  projectId: string,
  params: NativeAreaPreviewParams,
  options?: Parameters<typeof request>[1],
): Promise<NativeAreaPreview> => {
  return request<NativeAreaPreview>(
    getNativeAreaPreviewUrl(projectId, params),
    {
      ...options,
      method: 'GET',
    },
  );
};

export const getSavePlantingZonesUrl = (projectId: string) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/planting-zones`;
};

export const savePlantingZones = async (
  projectId: string,
  plantingZonesRequest: PlantingZonesRequest,
  options?: Parameters<typeof request>[1],
): Promise<Project> => {
  const getHeaders = (
    h?: NonNullable<RequestInit['headers']>,
  ): Record<string, string | readonly string[]> => {
    if (!h) return {};
    if (h instanceof Headers) return Object.fromEntries(h.entries());
    if (Symbol.iterator in h) {
      return Object.fromEntries(
        Array.from(
          h as Iterable<Iterable<string>>,
          (entry) => Array.from(entry) as [string, string],
        ),
      );
    }
    const headers: Record<string, string | readonly string[]> = {};
    for (const [name, value] of Object.entries<
      string | readonly string[] | undefined
    >(h)) {
      if (value !== undefined) headers[name] = value;
    }
    return headers;
  };
  return request<Project>(getSavePlantingZonesUrl(projectId), {
    ...options,
    method: 'PUT',
    headers: {
      'Content-Type': 'application/json',
      ...getHeaders(options?.headers),
    },
    body: JSON.stringify(plantingZonesRequest),
  });
};

export const getPreviewPlantingZoneUrl = (projectId: string) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/planting-zones/preview`;
};

export const previewPlantingZone = async (
  projectId: string,
  plantingZoneAssignment: PlantingZoneAssignment,
  options?: Parameters<typeof request>[1],
): Promise<PlantingZonePreview> => {
  const getHeaders = (
    h?: NonNullable<RequestInit['headers']>,
  ): Record<string, string | readonly string[]> => {
    if (!h) return {};
    if (h instanceof Headers) return Object.fromEntries(h.entries());
    if (Symbol.iterator in h) {
      return Object.fromEntries(
        Array.from(
          h as Iterable<Iterable<string>>,
          (entry) => Array.from(entry) as [string, string],
        ),
      );
    }
    const headers: Record<string, string | readonly string[]> = {};
    for (const [name, value] of Object.entries<
      string | readonly string[] | undefined
    >(h)) {
      if (value !== undefined) headers[name] = value;
    }
    return headers;
  };
  return request<PlantingZonePreview>(getPreviewPlantingZoneUrl(projectId), {
    ...options,
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...getHeaders(options?.headers),
    },
    body: JSON.stringify(plantingZoneAssignment),
  });
};

export const getGetMapFeaturesUrl = (
  projectId: string,
  params: GetMapFeaturesParams,
) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {
    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : String(value));
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0
    ? `/api/projects/${encodeURIComponent(String(projectId))}/map-features?${stringifiedParams}`
    : `/api/projects/${encodeURIComponent(String(projectId))}/map-features`;
};

export const getMapFeatures = async (
  projectId: string,
  params: GetMapFeaturesParams,
  options?: Parameters<typeof request>[1],
): Promise<GeometrySnapshot> => {
  return request<GeometrySnapshot>(getGetMapFeaturesUrl(projectId, params), {
    ...options,
    method: 'GET',
  });
};

export const getGetDataPassportUrl = (projectId: string) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/data-passport`;
};

export const getDataPassport = async (
  projectId: string,
  options?: Parameters<typeof request>[1],
): Promise<DataPassport> => {
  return request<DataPassport>(getGetDataPassportUrl(projectId), {
    ...options,
    method: 'GET',
  });
};

export const getStartGeometryOperationUrl = (projectId: string) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/operations/geometry`;
};

export const startGeometryOperation = async (
  projectId: string,
  options?: Parameters<typeof request>[1],
): Promise<ProjectOperation> => {
  return request<ProjectOperation>(getStartGeometryOperationUrl(projectId), {
    ...options,
    method: 'POST',
  });
};

export const getStartCapturePreparationUrl = (projectId: string) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/operations/cad-capture`;
};

export const startCapturePreparation = async (
  projectId: string,
  options?: Parameters<typeof request>[1],
): Promise<ProjectOperation> => {
  return request<ProjectOperation>(getStartCapturePreparationUrl(projectId), {
    ...options,
    method: 'POST',
  });
};

export const getOpenSourceEditorUrl = (projectId: string) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/source-editor`;
};

export const openSourceEditor = async (
  projectId: string,
  options?: Parameters<typeof request>[1],
): Promise<Project> => {
  return request<Project>(getOpenSourceEditorUrl(projectId), {
    ...options,
    method: 'POST',
  });
};

export const getGetLatestOperationUrl = (
  projectId: string,
  params: GetLatestOperationParams,
) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {
    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : String(value));
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0
    ? `/api/projects/${encodeURIComponent(String(projectId))}/operations/latest?${stringifiedParams}`
    : `/api/projects/${encodeURIComponent(String(projectId))}/operations/latest`;
};

export const getLatestOperation = async (
  projectId: string,
  params: GetLatestOperationParams,
  options?: Parameters<typeof request>[1],
): Promise<ProjectOperation | null> => {
  return request<ProjectOperation | null>(
    getGetLatestOperationUrl(projectId, params),
    {
      ...options,
      method: 'GET',
    },
  );
};

export const getGetOperationUrl = (projectId: string, operationId: string) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/operations/${encodeURIComponent(String(operationId))}`;
};

export const getOperation = async (
  projectId: string,
  operationId: string,
  options?: Parameters<typeof request>[1],
): Promise<ProjectOperation> => {
  return request<ProjectOperation>(getGetOperationUrl(projectId, operationId), {
    ...options,
    method: 'GET',
  });
};

export const getOperationEventsUrl = (
  projectId: string,
  operationId: string,
) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/operations/${encodeURIComponent(String(operationId))}/events`;
};

export const operationEvents = async (
  projectId: string,
  operationId: string,
  options?: Parameters<typeof request>[1],
): Promise<string> => {
  return request<string>(getOperationEventsUrl(projectId, operationId), {
    ...options,
    method: 'GET',
  });
};

export const getCancelOperationUrl = (
  projectId: string,
  operationId: string,
) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/operations/${encodeURIComponent(String(operationId))}/cancel`;
};

export const cancelOperation = async (
  projectId: string,
  operationId: string,
  options?: Parameters<typeof request>[1],
): Promise<ProjectOperation> => {
  return request<ProjectOperation>(
    getCancelOperationUrl(projectId, operationId),
    {
      ...options,
      method: 'POST',
    },
  );
};

export const getCreateManualPlanUrl = (projectId: string) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/plan/manual`;
};

export const createManualPlan = async (
  projectId: string,
  options?: Parameters<typeof request>[1],
): Promise<Project> => {
  return request<Project>(getCreateManualPlanUrl(projectId), {
    ...options,
    method: 'POST',
  });
};

export const getGetPlanGrowthForecastUrl = (
  projectId: string,
  params?: GetPlanGrowthForecastParams,
) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {
    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : String(value));
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0
    ? `/api/projects/${encodeURIComponent(String(projectId))}/plan/growth-forecast?${stringifiedParams}`
    : `/api/projects/${encodeURIComponent(String(projectId))}/plan/growth-forecast`;
};

export const getPlanGrowthForecast = async (
  projectId: string,
  params?: GetPlanGrowthForecastParams,
  options?: Parameters<typeof request>[1],
): Promise<GrowthForecastResponse> => {
  return request<GrowthForecastResponse>(
    getGetPlanGrowthForecastUrl(projectId, params),
    {
      ...options,
      method: 'GET',
    },
  );
};

export const getCheckPlanPlacementUrl = (projectId: string) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/plan/placement-check`;
};

export const checkPlanPlacement = async (
  projectId: string,
  placementCheckRequest: PlacementCheckRequest,
  options?: Parameters<typeof request>[1],
): Promise<PlacementCheck> => {
  const getHeaders = (
    h?: NonNullable<RequestInit['headers']>,
  ): Record<string, string | readonly string[]> => {
    if (!h) return {};
    if (h instanceof Headers) return Object.fromEntries(h.entries());
    if (Symbol.iterator in h) {
      return Object.fromEntries(
        Array.from(
          h as Iterable<Iterable<string>>,
          (entry) => Array.from(entry) as [string, string],
        ),
      );
    }
    const headers: Record<string, string | readonly string[]> = {};
    for (const [name, value] of Object.entries<
      string | readonly string[] | undefined
    >(h)) {
      if (value !== undefined) headers[name] = value;
    }
    return headers;
  };
  return request<PlacementCheck>(getCheckPlanPlacementUrl(projectId), {
    ...options,
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...getHeaders(options?.headers),
    },
    body: JSON.stringify(placementCheckRequest),
  });
};

export const getPreviewPlanChangeSetUrl = (projectId: string) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/plan/change-sets/preview`;
};

export const previewPlanChangeSet = async (
  projectId: string,
  planChangeSetDraft: PlanChangeSetDraft,
  options?: Parameters<typeof request>[1],
): Promise<ChangeSetPreview> => {
  const getHeaders = (
    h?: NonNullable<RequestInit['headers']>,
  ): Record<string, string | readonly string[]> => {
    if (!h) return {};
    if (h instanceof Headers) return Object.fromEntries(h.entries());
    if (Symbol.iterator in h) {
      return Object.fromEntries(
        Array.from(
          h as Iterable<Iterable<string>>,
          (entry) => Array.from(entry) as [string, string],
        ),
      );
    }
    const headers: Record<string, string | readonly string[]> = {};
    for (const [name, value] of Object.entries<
      string | readonly string[] | undefined
    >(h)) {
      if (value !== undefined) headers[name] = value;
    }
    return headers;
  };
  return request<ChangeSetPreview>(getPreviewPlanChangeSetUrl(projectId), {
    ...options,
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...getHeaders(options?.headers),
    },
    body: JSON.stringify(planChangeSetDraft),
  });
};

export const getApplyPlanChangeSetUrl = (projectId: string) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/plan/change-sets/apply`;
};

export const applyPlanChangeSet = async (
  projectId: string,
  planChangeSetApplyRequest: PlanChangeSetApplyRequest,
  options?: Parameters<typeof request>[1],
): Promise<PlanMutationResult> => {
  const getHeaders = (
    h?: NonNullable<RequestInit['headers']>,
  ): Record<string, string | readonly string[]> => {
    if (!h) return {};
    if (h instanceof Headers) return Object.fromEntries(h.entries());
    if (Symbol.iterator in h) {
      return Object.fromEntries(
        Array.from(
          h as Iterable<Iterable<string>>,
          (entry) => Array.from(entry) as [string, string],
        ),
      );
    }
    const headers: Record<string, string | readonly string[]> = {};
    for (const [name, value] of Object.entries<
      string | readonly string[] | undefined
    >(h)) {
      if (value !== undefined) headers[name] = value;
    }
    return headers;
  };
  return request<PlanMutationResult>(getApplyPlanChangeSetUrl(projectId), {
    ...options,
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...getHeaders(options?.headers),
    },
    body: JSON.stringify(planChangeSetApplyRequest),
  });
};

export const getReadPlanChangeReceiptUrl = (
  projectId: string,
  previewId: string,
  params: ReadPlanChangeReceiptParams,
) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {
    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : String(value));
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0
    ? `/api/projects/${encodeURIComponent(String(projectId))}/plan/change-sets/${encodeURIComponent(String(previewId))}/receipt?${stringifiedParams}`
    : `/api/projects/${encodeURIComponent(String(projectId))}/plan/change-sets/${encodeURIComponent(String(previewId))}/receipt`;
};

export const readPlanChangeReceipt = async (
  projectId: string,
  previewId: string,
  params: ReadPlanChangeReceiptParams,
  options?: Parameters<typeof request>[1],
): Promise<PlanChangeReceiptResult> => {
  return request<PlanChangeReceiptResult>(
    getReadPlanChangeReceiptUrl(projectId, previewId, params),
    {
      ...options,
      method: 'GET',
    },
  );
};

export const getPreviewAutomaticPlanUrl = (projectId: string) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/plan/automatic/preview`;
};

export const previewAutomaticPlan = async (
  projectId: string,
  automaticPlacementRequest: AutomaticPlacementRequest,
  options?: Parameters<typeof request>[1],
): Promise<AutomaticPlacementPreview> => {
  const getHeaders = (
    h?: NonNullable<RequestInit['headers']>,
  ): Record<string, string | readonly string[]> => {
    if (!h) return {};
    if (h instanceof Headers) return Object.fromEntries(h.entries());
    if (Symbol.iterator in h) {
      return Object.fromEntries(
        Array.from(
          h as Iterable<Iterable<string>>,
          (entry) => Array.from(entry) as [string, string],
        ),
      );
    }
    const headers: Record<string, string | readonly string[]> = {};
    for (const [name, value] of Object.entries<
      string | readonly string[] | undefined
    >(h)) {
      if (value !== undefined) headers[name] = value;
    }
    return headers;
  };
  return request<AutomaticPlacementPreview>(
    getPreviewAutomaticPlanUrl(projectId),
    {
      ...options,
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...getHeaders(options?.headers),
      },
      body: JSON.stringify(automaticPlacementRequest),
    },
  );
};

export const getPreviewPlanPatternUrl = (projectId: string) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/plan/patterns/preview`;
};

export const previewPlanPattern = async (
  projectId: string,
  rowPatternRequestFillPatternRequestPlacementMaskRequest:
    RowPatternRequest | FillPatternRequest | PlacementMaskRequest,
  options?: Parameters<typeof request>[1],
): Promise<PatternPreview> => {
  const getHeaders = (
    h?: NonNullable<RequestInit['headers']>,
  ): Record<string, string | readonly string[]> => {
    if (!h) return {};
    if (h instanceof Headers) return Object.fromEntries(h.entries());
    if (Symbol.iterator in h) {
      return Object.fromEntries(
        Array.from(
          h as Iterable<Iterable<string>>,
          (entry) => Array.from(entry) as [string, string],
        ),
      );
    }
    const headers: Record<string, string | readonly string[]> = {};
    for (const [name, value] of Object.entries<
      string | readonly string[] | undefined
    >(h)) {
      if (value !== undefined) headers[name] = value;
    }
    return headers;
  };
  return request<PatternPreview>(getPreviewPlanPatternUrl(projectId), {
    ...options,
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...getHeaders(options?.headers),
    },
    body: JSON.stringify(
      rowPatternRequestFillPatternRequestPlacementMaskRequest,
    ),
  });
};

export const getGetPlanPlacementMasksUrl = (projectId: string) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/plan/placement-masks`;
};

export const getPlanPlacementMasks = async (
  projectId: string,
  options?: Parameters<typeof request>[1],
): Promise<PlacementMaskPreset[]> => {
  return request<PlacementMaskPreset[]>(
    getGetPlanPlacementMasksUrl(projectId),
    {
      ...options,
      method: 'GET',
    },
  );
};

export const getBuildingScreenTargetsUrl = (
  projectId: string,
  params: BuildingScreenTargetsParams,
) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {
    const arrayFormatParameters = ['zone_ids'];

    if (Array.isArray(value) && arrayFormatParameters.includes(key)) {
      value.forEach((v) => {
        normalizedParams.append(key, v === null ? 'null' : String(v));
      });
      return;
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0
    ? `/api/projects/${encodeURIComponent(String(projectId))}/building-screen/targets?${stringifiedParams}`
    : `/api/projects/${encodeURIComponent(String(projectId))}/building-screen/targets`;
};

export const buildingScreenTargets = async (
  projectId: string,
  params: BuildingScreenTargetsParams,
  options?: Parameters<typeof request>[1],
): Promise<BuildingScreenTargets> => {
  return request<BuildingScreenTargets>(
    getBuildingScreenTargetsUrl(projectId, params),
    {
      ...options,
      method: 'GET',
    },
  );
};

export const getPreviewBuildingScreenUrl = (projectId: string) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/building-screen/preview`;
};

export const previewBuildingScreen = async (
  projectId: string,
  buildingScreenRequest: BuildingScreenRequest,
  options?: Parameters<typeof request>[1],
): Promise<RecommendationPreview> => {
  const getHeaders = (
    h?: NonNullable<RequestInit['headers']>,
  ): Record<string, string | readonly string[]> => {
    if (!h) return {};
    if (h instanceof Headers) return Object.fromEntries(h.entries());
    if (Symbol.iterator in h) {
      return Object.fromEntries(
        Array.from(
          h as Iterable<Iterable<string>>,
          (entry) => Array.from(entry) as [string, string],
        ),
      );
    }
    const headers: Record<string, string | readonly string[]> = {};
    for (const [name, value] of Object.entries<
      string | readonly string[] | undefined
    >(h)) {
      if (value !== undefined) headers[name] = value;
    }
    return headers;
  };
  return request<RecommendationPreview>(
    getPreviewBuildingScreenUrl(projectId),
    {
      ...options,
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...getHeaders(options?.headers),
      },
      body: JSON.stringify(buildingScreenRequest),
    },
  );
};

export const getPreviewPlanRecommendationUrl = (projectId: string) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/plan/recommendations/preview`;
};

export const previewPlanRecommendation = async (
  projectId: string,
  recommendationRequest: RecommendationRequest,
  options?: Parameters<typeof request>[1],
): Promise<RecommendationPreview> => {
  const getHeaders = (
    h?: NonNullable<RequestInit['headers']>,
  ): Record<string, string | readonly string[]> => {
    if (!h) return {};
    if (h instanceof Headers) return Object.fromEntries(h.entries());
    if (Symbol.iterator in h) {
      return Object.fromEntries(
        Array.from(
          h as Iterable<Iterable<string>>,
          (entry) => Array.from(entry) as [string, string],
        ),
      );
    }
    const headers: Record<string, string | readonly string[]> = {};
    for (const [name, value] of Object.entries<
      string | readonly string[] | undefined
    >(h)) {
      if (value !== undefined) headers[name] = value;
    }
    return headers;
  };
  return request<RecommendationPreview>(
    getPreviewPlanRecommendationUrl(projectId),
    {
      ...options,
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...getHeaders(options?.headers),
      },
      body: JSON.stringify(recommendationRequest),
    },
  );
};

export const getPreviewPlanBrushUrl = (projectId: string) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/plan/brush/preview`;
};

export const previewPlanBrush = async (
  projectId: string,
  brushPreviewRequest: BrushPreviewRequest,
  options?: Parameters<typeof request>[1],
): Promise<BrushPreview> => {
  const getHeaders = (
    h?: NonNullable<RequestInit['headers']>,
  ): Record<string, string | readonly string[]> => {
    if (!h) return {};
    if (h instanceof Headers) return Object.fromEntries(h.entries());
    if (Symbol.iterator in h) {
      return Object.fromEntries(
        Array.from(
          h as Iterable<Iterable<string>>,
          (entry) => Array.from(entry) as [string, string],
        ),
      );
    }
    const headers: Record<string, string | readonly string[]> = {};
    for (const [name, value] of Object.entries<
      string | readonly string[] | undefined
    >(h)) {
      if (value !== undefined) headers[name] = value;
    }
    return headers;
  };
  return request<BrushPreview>(getPreviewPlanBrushUrl(projectId), {
    ...options,
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...getHeaders(options?.headers),
    },
    body: JSON.stringify(brushPreviewRequest),
  });
};

export const getGetPlanSceneUrl = (
  projectId: string,
  params?: GetPlanSceneParams,
) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {
    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : String(value));
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0
    ? `/api/projects/${encodeURIComponent(String(projectId))}/plan/scene?${stringifiedParams}`
    : `/api/projects/${encodeURIComponent(String(projectId))}/plan/scene`;
};

export const getPlanScene = async (
  projectId: string,
  params?: GetPlanSceneParams,
  options?: Parameters<typeof request>[1],
): Promise<SceneSnapshot> => {
  return request<SceneSnapshot>(getGetPlanSceneUrl(projectId, params), {
    ...options,
    method: 'GET',
  });
};

export const getShortlistProjectSpeciesUrl = (projectId: string) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/species/shortlist`;
};

export const shortlistProjectSpecies = async (
  projectId: string,
  speciesShortlistRequest: SpeciesShortlistRequest,
  options?: Parameters<typeof request>[1],
): Promise<SpeciesShortlistItem[]> => {
  const getHeaders = (
    h?: NonNullable<RequestInit['headers']>,
  ): Record<string, string | readonly string[]> => {
    if (!h) return {};
    if (h instanceof Headers) return Object.fromEntries(h.entries());
    if (Symbol.iterator in h) {
      return Object.fromEntries(
        Array.from(
          h as Iterable<Iterable<string>>,
          (entry) => Array.from(entry) as [string, string],
        ),
      );
    }
    const headers: Record<string, string | readonly string[]> = {};
    for (const [name, value] of Object.entries<
      string | readonly string[] | undefined
    >(h)) {
      if (value !== undefined) headers[name] = value;
    }
    return headers;
  };
  return request<SpeciesShortlistItem[]>(
    getShortlistProjectSpeciesUrl(projectId),
    {
      ...options,
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...getHeaders(options?.headers),
      },
      body: JSON.stringify(speciesShortlistRequest),
    },
  );
};

export const getAddPlanObjectUrl = (projectId: string) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/plan/objects`;
};

export const addPlanObject = async (
  projectId: string,
  planObjectCreate: PlanObjectCreate,
  options?: Parameters<typeof request>[1],
): Promise<Plan> => {
  const getHeaders = (
    h?: NonNullable<RequestInit['headers']>,
  ): Record<string, string | readonly string[]> => {
    if (!h) return {};
    if (h instanceof Headers) return Object.fromEntries(h.entries());
    if (Symbol.iterator in h) {
      return Object.fromEntries(
        Array.from(
          h as Iterable<Iterable<string>>,
          (entry) => Array.from(entry) as [string, string],
        ),
      );
    }
    const headers: Record<string, string | readonly string[]> = {};
    for (const [name, value] of Object.entries<
      string | readonly string[] | undefined
    >(h)) {
      if (value !== undefined) headers[name] = value;
    }
    return headers;
  };
  return request<Plan>(getAddPlanObjectUrl(projectId), {
    ...options,
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...getHeaders(options?.headers),
    },
    body: JSON.stringify(planObjectCreate),
  });
};

export const getUpdatePlanObjectUrl = (projectId: string, objectId: string) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/plan/objects/${encodeURIComponent(String(objectId))}`;
};

export const updatePlanObject = async (
  projectId: string,
  objectId: string,
  planObjectUpdate: PlanObjectUpdate,
  options?: Parameters<typeof request>[1],
): Promise<Plan> => {
  const getHeaders = (
    h?: NonNullable<RequestInit['headers']>,
  ): Record<string, string | readonly string[]> => {
    if (!h) return {};
    if (h instanceof Headers) return Object.fromEntries(h.entries());
    if (Symbol.iterator in h) {
      return Object.fromEntries(
        Array.from(
          h as Iterable<Iterable<string>>,
          (entry) => Array.from(entry) as [string, string],
        ),
      );
    }
    const headers: Record<string, string | readonly string[]> = {};
    for (const [name, value] of Object.entries<
      string | readonly string[] | undefined
    >(h)) {
      if (value !== undefined) headers[name] = value;
    }
    return headers;
  };
  return request<Plan>(getUpdatePlanObjectUrl(projectId, objectId), {
    ...options,
    method: 'PATCH',
    headers: {
      'Content-Type': 'application/json',
      ...getHeaders(options?.headers),
    },
    body: JSON.stringify(planObjectUpdate),
  });
};

export const getDeletePlanObjectsUrl = (projectId: string) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/plan/objects/delete`;
};

export const deletePlanObjects = async (
  projectId: string,
  planObjectsDeleteRequest: PlanObjectsDeleteRequest,
  options?: Parameters<typeof request>[1],
): Promise<Plan> => {
  const getHeaders = (
    h?: NonNullable<RequestInit['headers']>,
  ): Record<string, string | readonly string[]> => {
    if (!h) return {};
    if (h instanceof Headers) return Object.fromEntries(h.entries());
    if (Symbol.iterator in h) {
      return Object.fromEntries(
        Array.from(
          h as Iterable<Iterable<string>>,
          (entry) => Array.from(entry) as [string, string],
        ),
      );
    }
    const headers: Record<string, string | readonly string[]> = {};
    for (const [name, value] of Object.entries<
      string | readonly string[] | undefined
    >(h)) {
      if (value !== undefined) headers[name] = value;
    }
    return headers;
  };
  return request<Plan>(getDeletePlanObjectsUrl(projectId), {
    ...options,
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...getHeaders(options?.headers),
    },
    body: JSON.stringify(planObjectsDeleteRequest),
  });
};

export const getGetPlanHistoryUrl = (projectId: string) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/plan/history`;
};

export const getPlanHistory = async (
  projectId: string,
  options?: Parameters<typeof request>[1],
): Promise<PlanHistoryState> => {
  return request<PlanHistoryState>(getGetPlanHistoryUrl(projectId), {
    ...options,
    method: 'GET',
  });
};

export const getUndoPlanChangeUrl = (projectId: string) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/plan/history/undo`;
};

export const undoPlanChange = async (
  projectId: string,
  options?: Parameters<typeof request>[1],
): Promise<Project> => {
  return request<Project>(getUndoPlanChangeUrl(projectId), {
    ...options,
    method: 'POST',
  });
};

export const getRedoPlanChangeUrl = (projectId: string) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/plan/history/redo`;
};

export const redoPlanChange = async (
  projectId: string,
  options?: Parameters<typeof request>[1],
): Promise<Project> => {
  return request<Project>(getRedoPlanChangeUrl(projectId), {
    ...options,
    method: 'POST',
  });
};

export const getCreateExportUrl = (projectId: string) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/exports`;
};

export const createExport = async (
  projectId: string,
  options?: Parameters<typeof request>[1],
): Promise<ExportArtifact> => {
  return request<ExportArtifact>(getCreateExportUrl(projectId), {
    ...options,
    method: 'POST',
  });
};

export const getDownloadExportUrl = (projectId: string, artifactId: string) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/exports/${encodeURIComponent(String(artifactId))}/download`;
};

export const downloadExport = async (
  projectId: string,
  artifactId: string,
  options?: Parameters<typeof request>[1],
): Promise<unknown> => {
  return request<unknown>(getDownloadExportUrl(projectId, artifactId), {
    ...options,
    method: 'GET',
  });
};

export const getCreateReleaseUrl = (projectId: string) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/releases`;
};

export const createRelease = async (
  projectId: string,
  releaseCreateRequest: ReleaseCreateRequest,
  options?: Parameters<typeof request>[1],
): Promise<ReleasePackage> => {
  const getHeaders = (
    h?: NonNullable<RequestInit['headers']>,
  ): Record<string, string | readonly string[]> => {
    if (!h) return {};
    if (h instanceof Headers) return Object.fromEntries(h.entries());
    if (Symbol.iterator in h) {
      return Object.fromEntries(
        Array.from(
          h as Iterable<Iterable<string>>,
          (entry) => Array.from(entry) as [string, string],
        ),
      );
    }
    const headers: Record<string, string | readonly string[]> = {};
    for (const [name, value] of Object.entries<
      string | readonly string[] | undefined
    >(h)) {
      if (value !== undefined) headers[name] = value;
    }
    return headers;
  };
  return request<ReleasePackage>(getCreateReleaseUrl(projectId), {
    ...options,
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...getHeaders(options?.headers),
    },
    body: JSON.stringify(releaseCreateRequest),
  });
};

export const getGetReleaseUrl = (projectId: string, releaseId: string) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/releases/${encodeURIComponent(String(releaseId))}`;
};

export const getRelease = async (
  projectId: string,
  releaseId: string,
  options?: Parameters<typeof request>[1],
): Promise<ReleasePackage> => {
  return request<ReleasePackage>(getGetReleaseUrl(projectId, releaseId), {
    ...options,
    method: 'GET',
  });
};

export const getDownloadReleaseArtifactUrl = (
  projectId: string,
  releaseId: string,
  artifactId: string,
) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/releases/${encodeURIComponent(String(releaseId))}/artifacts/${encodeURIComponent(String(artifactId))}`;
};

export const downloadReleaseArtifact = async (
  projectId: string,
  releaseId: string,
  artifactId: string,
  options?: Parameters<typeof request>[1],
): Promise<unknown> => {
  return request<unknown>(
    getDownloadReleaseArtifactUrl(projectId, releaseId, artifactId),
    {
      ...options,
      method: 'GET',
    },
  );
};

export const getCadAssetMetadataUrl = (
  projectId: string,
  operationId: string,
) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/operations/${encodeURIComponent(String(operationId))}/cad-asset`;
};

export const cadAssetMetadata = async (
  projectId: string,
  operationId: string,
  options?: Parameters<typeof request>[1],
): Promise<CadSourceAsset> => {
  return request<CadSourceAsset>(
    getCadAssetMetadataUrl(projectId, operationId),
    {
      ...options,
      method: 'GET',
    },
  );
};

export const getCadAssetFileUrl = (projectId: string, operationId: string) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/operations/${encodeURIComponent(String(operationId))}/cad-asset/file`;
};

export const cadAssetFile = async (
  projectId: string,
  operationId: string,
  options?: Parameters<typeof request>[1],
): Promise<Blob> => {
  return request<Blob>(getCadAssetFileUrl(projectId, operationId), {
    ...options,
    method: 'GET',
  });
};

export const getPrepareSceneRenderUrl = (projectId: string) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/scene-render-preparations`;
};

export const prepareSceneRender = async (
  projectId: string,
  sceneRenderPreparationRequest: SceneRenderPreparationRequest,
  options?: Parameters<typeof request>[1],
): Promise<SceneRenderPreparation> => {
  const getHeaders = (
    h?: NonNullable<RequestInit['headers']>,
  ): Record<string, string | readonly string[]> => {
    if (!h) return {};
    if (h instanceof Headers) return Object.fromEntries(h.entries());
    if (Symbol.iterator in h) {
      return Object.fromEntries(
        Array.from(
          h as Iterable<Iterable<string>>,
          (entry) => Array.from(entry) as [string, string],
        ),
      );
    }
    const headers: Record<string, string | readonly string[]> = {};
    for (const [name, value] of Object.entries<
      string | readonly string[] | undefined
    >(h)) {
      if (value !== undefined) headers[name] = value;
    }
    return headers;
  };
  return request<SceneRenderPreparation>(getPrepareSceneRenderUrl(projectId), {
    ...options,
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...getHeaders(options?.headers),
    },
    body: JSON.stringify(sceneRenderPreparationRequest),
  });
};

export const getGetSceneRenderPreparationUrl = (
  projectId: string,
  preparationId: string,
) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/scene-render-preparations/${encodeURIComponent(String(preparationId))}`;
};

export const getSceneRenderPreparation = async (
  projectId: string,
  preparationId: string,
  options?: Parameters<typeof request>[1],
): Promise<SceneRenderPreparation> => {
  return request<SceneRenderPreparation>(
    getGetSceneRenderPreparationUrl(projectId, preparationId),
    {
      ...options,
      method: 'GET',
    },
  );
};

export const getGetSceneRenderPreparationAssetUrl = (
  projectId: string,
  preparationId: string,
  assetId: string,
) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/scene-render-preparations/${encodeURIComponent(String(preparationId))}/assets/${encodeURIComponent(String(assetId))}`;
};

export const getSceneRenderPreparationAsset = async (
  projectId: string,
  preparationId: string,
  assetId: string,
  options?: Parameters<typeof request>[1],
): Promise<void> => {
  return request<void>(
    getGetSceneRenderPreparationAssetUrl(projectId, preparationId, assetId),
    {
      ...options,
      method: 'GET',
    },
  );
};

export const getCreateSceneRenderUrl = (projectId: string) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/scene-renders`;
};

export const createSceneRender = async (
  projectId: string,
  sceneRenderRequest: SceneRenderRequest,
  options?: Parameters<typeof request>[1],
): Promise<SceneRenderJob> => {
  const getHeaders = (
    h?: NonNullable<RequestInit['headers']>,
  ): Record<string, string | readonly string[]> => {
    if (!h) return {};
    if (h instanceof Headers) return Object.fromEntries(h.entries());
    if (Symbol.iterator in h) {
      return Object.fromEntries(
        Array.from(
          h as Iterable<Iterable<string>>,
          (entry) => Array.from(entry) as [string, string],
        ),
      );
    }
    const headers: Record<string, string | readonly string[]> = {};
    for (const [name, value] of Object.entries<
      string | readonly string[] | undefined
    >(h)) {
      if (value !== undefined) headers[name] = value;
    }
    return headers;
  };
  return request<SceneRenderJob>(getCreateSceneRenderUrl(projectId), {
    ...options,
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...getHeaders(options?.headers),
    },
    body: JSON.stringify(sceneRenderRequest),
  });
};

export const getGetSceneRenderUrl = (projectId: string, jobId: string) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/scene-renders/${encodeURIComponent(String(jobId))}`;
};

export const getSceneRender = async (
  projectId: string,
  jobId: string,
  options?: Parameters<typeof request>[1],
): Promise<SceneRenderJob> => {
  return request<SceneRenderJob>(getGetSceneRenderUrl(projectId, jobId), {
    ...options,
    method: 'GET',
  });
};

export const getCancelSceneRenderUrl = (projectId: string, jobId: string) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/scene-renders/${encodeURIComponent(String(jobId))}/cancel`;
};

export const cancelSceneRender = async (
  projectId: string,
  jobId: string,
  options?: Parameters<typeof request>[1],
): Promise<SceneRenderJob> => {
  return request<SceneRenderJob>(getCancelSceneRenderUrl(projectId, jobId), {
    ...options,
    method: 'POST',
  });
};

export const getSceneRenderImageUrl = (projectId: string, jobId: string) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/scene-renders/${encodeURIComponent(String(jobId))}/image`;
};

export const sceneRenderImage = async (
  projectId: string,
  jobId: string,
  options?: Parameters<typeof request>[1],
): Promise<unknown> => {
  return request<unknown>(getSceneRenderImageUrl(projectId, jobId), {
    ...options,
    method: 'GET',
  });
};

export const getSceneRenderReceiptUrl = (projectId: string, jobId: string) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/scene-renders/${encodeURIComponent(String(jobId))}/receipt`;
};

export const sceneRenderReceipt = async (
  projectId: string,
  jobId: string,
  options?: Parameters<typeof request>[1],
): Promise<unknown> => {
  return request<unknown>(getSceneRenderReceiptUrl(projectId, jobId), {
    ...options,
    method: 'GET',
  });
};

export const getGetSceneViewUrl = (
  projectId: string,
  params?: GetSceneViewParams,
) => {
  const normalizedParams = new URLSearchParams();

  Object.entries(params || {}).forEach(([key, value]) => {
    if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : String(value));
    }
  });

  const stringifiedParams = normalizedParams.toString();

  return stringifiedParams.length > 0
    ? `/api/projects/${encodeURIComponent(String(projectId))}/scene-view?${stringifiedParams}`
    : `/api/projects/${encodeURIComponent(String(projectId))}/scene-view`;
};

export const getSceneView = async (
  projectId: string,
  params?: GetSceneViewParams,
  options?: Parameters<typeof request>[1],
): Promise<SceneView> => {
  return request<SceneView>(getGetSceneViewUrl(projectId, params), {
    ...options,
    method: 'GET',
  });
};

export const getGetSceneViewAssetUrl = (projectId: string, assetId: string) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/scene-view-assets/${encodeURIComponent(String(assetId))}`;
};

export const getSceneViewAsset = async (
  projectId: string,
  assetId: string,
  options?: Parameters<typeof request>[1],
): Promise<void> => {
  return request<void>(getGetSceneViewAssetUrl(projectId, assetId), {
    ...options,
    method: 'GET',
  });
};

export const getGetProjectLocationUrl = (projectId: string) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/location`;
};

export const getProjectLocation = async (
  projectId: string,
  options?: Parameters<typeof request>[1],
): Promise<LocationState> => {
  return request<LocationState>(getGetProjectLocationUrl(projectId), {
    ...options,
    method: 'GET',
  });
};

export const getSuggestProjectLocationUrl = (projectId: string) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/location/suggestions`;
};

export const suggestProjectLocation = async (
  projectId: string,
  locationSuggestionsRequest: LocationSuggestionsRequest,
  options?: Parameters<typeof request>[1],
): Promise<LocationSuggestions> => {
  const getHeaders = (
    h?: NonNullable<RequestInit['headers']>,
  ): Record<string, string | readonly string[]> => {
    if (!h) return {};
    if (h instanceof Headers) return Object.fromEntries(h.entries());
    if (Symbol.iterator in h) {
      return Object.fromEntries(
        Array.from(
          h as Iterable<Iterable<string>>,
          (entry) => Array.from(entry) as [string, string],
        ),
      );
    }
    const headers: Record<string, string | readonly string[]> = {};
    for (const [name, value] of Object.entries<
      string | readonly string[] | undefined
    >(h)) {
      if (value !== undefined) headers[name] = value;
    }
    return headers;
  };
  return request<LocationSuggestions>(getSuggestProjectLocationUrl(projectId), {
    ...options,
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...getHeaders(options?.headers),
    },
    body: JSON.stringify(locationSuggestionsRequest),
  });
};

export const getResolveProjectLocationUrl = (projectId: string) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/location/resolve`;
};

export const resolveProjectLocation = async (
  projectId: string,
  locationResolveRequest: LocationResolveRequest,
  options?: Parameters<typeof request>[1],
): Promise<LocationDraft> => {
  const getHeaders = (
    h?: NonNullable<RequestInit['headers']>,
  ): Record<string, string | readonly string[]> => {
    if (!h) return {};
    if (h instanceof Headers) return Object.fromEntries(h.entries());
    if (Symbol.iterator in h) {
      return Object.fromEntries(
        Array.from(
          h as Iterable<Iterable<string>>,
          (entry) => Array.from(entry) as [string, string],
        ),
      );
    }
    const headers: Record<string, string | readonly string[]> = {};
    for (const [name, value] of Object.entries<
      string | readonly string[] | undefined
    >(h)) {
      if (value !== undefined) headers[name] = value;
    }
    return headers;
  };
  return request<LocationDraft>(getResolveProjectLocationUrl(projectId), {
    ...options,
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...getHeaders(options?.headers),
    },
    body: JSON.stringify(locationResolveRequest),
  });
};

export const getAlignProjectLocationUrl = (projectId: string) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/location/alignment`;
};

export const alignProjectLocation = async (
  projectId: string,
  locationAlignmentRequest: LocationAlignmentRequest,
  options?: Parameters<typeof request>[1],
): Promise<LocationAlignment> => {
  const getHeaders = (
    h?: NonNullable<RequestInit['headers']>,
  ): Record<string, string | readonly string[]> => {
    if (!h) return {};
    if (h instanceof Headers) return Object.fromEntries(h.entries());
    if (Symbol.iterator in h) {
      return Object.fromEntries(
        Array.from(
          h as Iterable<Iterable<string>>,
          (entry) => Array.from(entry) as [string, string],
        ),
      );
    }
    const headers: Record<string, string | readonly string[]> = {};
    for (const [name, value] of Object.entries<
      string | readonly string[] | undefined
    >(h)) {
      if (value !== undefined) headers[name] = value;
    }
    return headers;
  };
  return request<LocationAlignment>(getAlignProjectLocationUrl(projectId), {
    ...options,
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...getHeaders(options?.headers),
    },
    body: JSON.stringify(locationAlignmentRequest),
  });
};

export const getApplyProjectLocationUrl = (projectId: string) => {
  return `/api/projects/${encodeURIComponent(String(projectId))}/location/apply`;
};

export const applyProjectLocation = async (
  projectId: string,
  locationApplyRequest: LocationApplyRequest,
  options?: Parameters<typeof request>[1],
): Promise<LocationState> => {
  const getHeaders = (
    h?: NonNullable<RequestInit['headers']>,
  ): Record<string, string | readonly string[]> => {
    if (!h) return {};
    if (h instanceof Headers) return Object.fromEntries(h.entries());
    if (Symbol.iterator in h) {
      return Object.fromEntries(
        Array.from(
          h as Iterable<Iterable<string>>,
          (entry) => Array.from(entry) as [string, string],
        ),
      );
    }
    const headers: Record<string, string | readonly string[]> = {};
    for (const [name, value] of Object.entries<
      string | readonly string[] | undefined
    >(h)) {
      if (value !== undefined) headers[name] = value;
    }
    return headers;
  };
  return request<LocationState>(getApplyProjectLocationUrl(projectId), {
    ...options,
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...getHeaders(options?.headers),
    },
    body: JSON.stringify(locationApplyRequest),
  });
};

export const getGetLocationServiceSettingsUrl = () => {
  return `/api/location/settings`;
};

export const getLocationServiceSettings = async (
  options?: Parameters<typeof request>[1],
): Promise<LocationServiceSettings> => {
  return request<LocationServiceSettings>(getGetLocationServiceSettingsUrl(), {
    ...options,
    method: 'GET',
  });
};

export const getUpdateLocationServiceSettingsUrl = () => {
  return `/api/location/settings`;
};

export const updateLocationServiceSettings = async (
  locationServiceSettingsUpdate: LocationServiceSettingsUpdate,
  options?: Parameters<typeof request>[1],
): Promise<LocationServiceSettings> => {
  const getHeaders = (
    h?: NonNullable<RequestInit['headers']>,
  ): Record<string, string | readonly string[]> => {
    if (!h) return {};
    if (h instanceof Headers) return Object.fromEntries(h.entries());
    if (Symbol.iterator in h) {
      return Object.fromEntries(
        Array.from(
          h as Iterable<Iterable<string>>,
          (entry) => Array.from(entry) as [string, string],
        ),
      );
    }
    const headers: Record<string, string | readonly string[]> = {};
    for (const [name, value] of Object.entries<
      string | readonly string[] | undefined
    >(h)) {
      if (value !== undefined) headers[name] = value;
    }
    return headers;
  };
  return request<LocationServiceSettings>(
    getUpdateLocationServiceSettingsUrl(),
    {
      ...options,
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
        ...getHeaders(options?.headers),
      },
      body: JSON.stringify(locationServiceSettingsUpdate),
    },
  );
};

export const getResetLocationServiceSettingsUrl = () => {
  return `/api/location/settings`;
};

export const resetLocationServiceSettings = async (
  options?: Parameters<typeof request>[1],
): Promise<LocationServiceSettings> => {
  return request<LocationServiceSettings>(
    getResetLocationServiceSettingsUrl(),
    {
      ...options,
      method: 'DELETE',
    },
  );
};
