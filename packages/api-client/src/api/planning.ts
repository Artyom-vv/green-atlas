import * as sdk from '../sdk.generated';
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
  ChangeSetPreview,
  PatternPreviewRequest,
  PlacementCheckRequest,
  PlanChangeSetDraft,
  PlanObjectCreate,
  ProjectWriteOptions,
  RecommendationRequest,
  SpeciesRevision,
} from '../contracts';
import { json } from '../transport/request';
import { withProjectWriteOptions } from '../transport/projectWrite';

export const planningApi = {
  getGrowthForecast: (projectId: string, year: number, signal?: AbortSignal) =>
    sdk.getPlanGrowthForecast(projectId, { year }, { signal }),
  listSpecies: (kind?: SpeciesRevision['kind']) =>
    sdk.getSpeciesCatalog(kind ? { kind } : undefined),
  getAssortment: () => sdk.getAssortmentCatalog(),
  createManualPlan: (projectId: string, options?: ProjectWriteOptions) =>
    sdk.createManualPlan(projectId, withProjectWriteOptions(json(), options)),
  addPlanObject: (
    projectId: string,
    object: PlanObjectCreate,
    options?: ProjectWriteOptions,
  ) =>
    sdk.addPlanObject(projectId, object, withProjectWriteOptions({}, options)),
  checkPlacement: (
    projectId: string,
    object: PlacementCheckRequest,
    signal?: AbortSignal,
  ) => sdk.checkPlanPlacement(projectId, object, { signal }),
  previewPlanChanges: (
    projectId: string,
    draft: PlanChangeSetDraft,
    signal?: AbortSignal,
    options?: ProjectWriteOptions,
  ) =>
    sdk
      .previewPlanChangeSet(
        projectId,
        draft,
        withProjectWriteOptions({ signal }, options),
      )
      .then(normalizeChangeSetPreview),
  applyPlanChanges: (projectId: string, preview: ChangeSetPreview) =>
    sdk.applyPlanChangeSet(projectId, {
      preview_id: preview.id,
      digest: preview.digest,
      base_plan_version: preview.base_plan_version,
    }),
  getPlanChangeReceipt: (
    projectId: string,
    preview: ChangeSetPreview,
    baseStateVersion: number,
  ) =>
    sdk.readPlanChangeReceipt(
      projectId,
      preview.id,
      {
        digest: preview.digest,
        base_plan_version: preview.base_plan_version,
        base_state_version: baseStateVersion,
      },
      { cache: 'no-store' },
    ),
  listPlacementMasks: sdk.getPlanPlacementMasks,
  previewPlanPattern: (
    projectId: string,
    pattern: PatternPreviewRequest,
    signal?: AbortSignal,
  ) =>
    sdk
      .previewPlanPattern(projectId, pattern, { signal })
      .then(normalizePatternPreview),
  previewRecommendation: (
    projectId: string,
    recommendation: RecommendationRequest,
    signal?: AbortSignal,
  ) =>
    sdk
      .previewPlanRecommendation(projectId, recommendation, { signal })
      .then(normalizeRecommendationPreview),
  previewAutomaticPlacement: (
    projectId: string,
    draft: AutomaticPlacementRequest,
    signal?: AbortSignal,
  ): Promise<AutomaticPlacementPreview> =>
    sdk.previewAutomaticPlan(projectId, draft, { signal }).then((result) => ({
      ...result,
      change_set: result.change_set
        ? normalizeChangeSetPreview(result.change_set)
        : result.change_set,
    })),
  getBuildingScreenTargets: (
    projectId: string,
    zoneIds: string[],
    signal?: AbortSignal,
  ) => sdk.buildingScreenTargets(projectId, { zone_ids: zoneIds }, { signal }),
  previewBuildingScreen: (
    projectId: string,
    draft: BuildingScreenRequest,
    signal?: AbortSignal,
  ) =>
    sdk
      .previewBuildingScreen(projectId, draft, { signal })
      .then(normalizeRecommendationPreview),
  previewBrush: (
    projectId: string,
    brush: BrushPreviewRequest,
    signal?: AbortSignal,
  ) =>
    sdk
      .previewPlanBrush(projectId, brush, { signal })
      .then(normalizeBrushPreview),
  shortlistSpecies: (
    projectId: string,
    scope: string[] | { zoneIds: string[] },
  ) =>
    sdk.shortlistProjectSpecies(
      projectId,
      Array.isArray(scope)
        ? { object_ids: scope }
        : { zone_ids: scope.zoneIds },
    ),
  updatePlanObject: sdk.updatePlanObject,
  deletePlanObjects: (
    projectId: string,
    ids: string[],
    options?: ProjectWriteOptions,
  ) =>
    sdk.deletePlanObjects(
      projectId,
      { ids },
      withProjectWriteOptions({}, options),
    ),
  getPlanHistory: sdk.getPlanHistory,
  undoPlanChange: (projectId: string, options?: ProjectWriteOptions) =>
    sdk.undoPlanChange(projectId, withProjectWriteOptions(json(), options)),
  redoPlanChange: (projectId: string, options?: ProjectWriteOptions) =>
    sdk.redoPlanChange(projectId, withProjectWriteOptions(json(), options)),
};
