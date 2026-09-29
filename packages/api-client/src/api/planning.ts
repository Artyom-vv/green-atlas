import {
  normalizeBrushPreview,
  normalizeChangeSetPreview,
  normalizePatternPreview,
  normalizeRecommendationPreview,
} from '../adapters/previews';
import type {
  AutomaticPlacementPreview,
  AutomaticPlacementRequest,
  BrushPreviewRequest,
  BuildingScreenRequest,
  BuildingScreenTargets,
  ChangeSetPreview,
  PatternPreviewRequest,
  PlacementCheck,
  PlacementCheckRequest,
  PlacementMaskPreset,
  Plan,
  PlanChangeSetApplyRequest,
  PlanChangeSetDraft,
  PlanHistoryState,
  PlanMutationResult,
  PlanObjectCreate,
  PlanObjectUpdate,
  Project,
  ProjectWriteOptions,
  RecommendationRequest,
  SpeciesRevision,
  SpeciesShortlistItem,
} from '../contracts';
import type { WireSchema } from '../contracts/wire';
import { json, request } from '../transport/request';
import { withProjectWriteOptions } from '../transport/projectWrite';

export const planningApi = {
  listSpecies: (kind?: SpeciesRevision['kind']) =>
    request<SpeciesRevision[]>(`/api/species${kind ? `?kind=${kind}` : ''}`),
  getAssortment: () =>
    request<WireSchema<'AssortmentInventory'>>('/api/species/assortment'),
  createManualPlan: (projectId: string, options?: ProjectWriteOptions) =>
    request<Project>(
      `/api/projects/${projectId}/plan/manual`,
      withProjectWriteOptions(json(), options),
    ),
  addPlanObject: (
    projectId: string,
    object: PlanObjectCreate,
    options?: ProjectWriteOptions,
  ) =>
    request<Plan>(
      `/api/projects/${projectId}/plan/objects`,
      withProjectWriteOptions(json(object), options),
    ),
  checkPlacement: (
    projectId: string,
    object: PlacementCheckRequest,
    signal?: AbortSignal,
  ) =>
    request<PlacementCheck>(`/api/projects/${projectId}/plan/placement-check`, {
      ...json(object),
      signal,
    }),
  previewPlanChanges: (
    projectId: string,
    draft: PlanChangeSetDraft,
    signal?: AbortSignal,
  ) =>
    request<WireSchema<'ChangeSetPreview'>>(
      `/api/projects/${projectId}/plan/change-sets/preview`,
      { ...json(draft), signal },
    ).then(normalizeChangeSetPreview),
  applyPlanChanges: (projectId: string, preview: ChangeSetPreview) =>
    request<PlanMutationResult>(
      `/api/projects/${projectId}/plan/change-sets/apply`,
      json({
        preview_id: preview.id,
        digest: preview.digest,
        base_plan_version: preview.base_plan_version,
      } satisfies PlanChangeSetApplyRequest),
    ),
  listPlacementMasks: (projectId: string) =>
    request<PlacementMaskPreset[]>(
      `/api/projects/${projectId}/plan/placement-masks`,
    ),
  previewPlanPattern: (
    projectId: string,
    pattern: PatternPreviewRequest,
    signal?: AbortSignal,
  ) =>
    request<WireSchema<'PatternPreview'>>(
      `/api/projects/${projectId}/plan/patterns/preview`,
      { ...json(pattern), signal },
    ).then(normalizePatternPreview),
  previewRecommendation: (
    projectId: string,
    recommendation: RecommendationRequest,
    signal?: AbortSignal,
  ) =>
    request<WireSchema<'RecommendationPreview'>>(
      `/api/projects/${projectId}/plan/recommendations/preview`,
      { ...json(recommendation), signal },
    ).then(normalizeRecommendationPreview),
  previewAutomaticPlacement: (
    projectId: string,
    draft: AutomaticPlacementRequest,
    signal?: AbortSignal,
  ): Promise<AutomaticPlacementPreview> =>
    request<WireSchema<'AutomaticPlacementPreview'>>(
      `/api/projects/${projectId}/plan/automatic/preview`,
      { ...json(draft), signal },
    ).then((result) => ({
      ...result,
      change_set: result.change_set
        ? normalizeChangeSetPreview(result.change_set)
        : result.change_set,
    })),
  getBuildingScreenTargets: (
    projectId: string,
    zoneIds: string[],
    signal?: AbortSignal,
  ) =>
    request<BuildingScreenTargets>(
      `/api/projects/${projectId}/building-screen/targets?${new URLSearchParams(zoneIds.map((id) => ['zone_ids', id]))}`,
      { signal },
    ),
  previewBuildingScreen: (
    projectId: string,
    draft: BuildingScreenRequest,
    signal?: AbortSignal,
  ) =>
    request<WireSchema<'RecommendationPreview'>>(
      `/api/projects/${projectId}/building-screen/preview`,
      { ...json(draft), signal },
    ).then(normalizeRecommendationPreview),
  previewBrush: (
    projectId: string,
    brush: BrushPreviewRequest,
    signal?: AbortSignal,
  ) =>
    request<WireSchema<'BrushPreview'>>(
      `/api/projects/${projectId}/plan/brush/preview`,
      { ...json(brush), signal },
    ).then(normalizeBrushPreview),
  shortlistSpecies: (
    projectId: string,
    scope: string[] | { zoneIds: string[] },
  ) =>
    request<SpeciesShortlistItem[]>(
      `/api/projects/${projectId}/species/shortlist`,
      json(
        Array.isArray(scope)
          ? { object_ids: scope }
          : { zone_ids: scope.zoneIds },
      ),
    ),
  updatePlanObject: (
    projectId: string,
    objectId: string,
    object: PlanObjectUpdate,
  ) =>
    request<Plan>(`/api/projects/${projectId}/plan/objects/${objectId}`, {
      ...json(object),
      method: 'PATCH',
    }),
  deletePlanObjects: (
    projectId: string,
    ids: string[],
    options?: ProjectWriteOptions,
  ) =>
    request<Plan>(
      `/api/projects/${projectId}/plan/objects/delete`,
      withProjectWriteOptions(json({ ids }), options),
    ),
  getPlanHistory: (projectId: string) =>
    request<PlanHistoryState>(`/api/projects/${projectId}/plan/history`),
  undoPlanChange: (projectId: string) =>
    request<Project>(`/api/projects/${projectId}/plan/history/undo`, json()),
  redoPlanChange: (projectId: string) =>
    request<Project>(`/api/projects/${projectId}/plan/history/redo`, json()),
};
