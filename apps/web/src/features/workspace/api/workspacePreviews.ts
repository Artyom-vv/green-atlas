/** Рабочая область: типизированные запросы предпросмотра и продолжение подготовки */
import {
  api,
  type BrushPreviewRequest,
  type BuildingScreenRequest,
  type PatternPreviewRequest,
  type PatternPreview,
  type RecommendationRequest,
  type AutomaticPlacementRequest,
  type AutomaticPlacementPreview,
  type RecommendationPreview,
} from '@green/api-client';
import { completeSearchDomainPreview } from './completeSearchDomainPreview';
import { PATTERN_PASS_RESPONSE_TIMEOUT_MS } from './patternPreviewPass';

export const previewWorkspacePattern = (
  projectId: string,
  request: PatternPreviewRequest,
  signal: AbortSignal,
  publish?: (progress: PatternPreview) => void,
) =>
  completeSearchDomainPreview(
    (passSignal) => api.previewPlanPattern(projectId, request, passSignal),
    signal,
    publish,
    PATTERN_PASS_RESPONSE_TIMEOUT_MS * Math.max(1, request.zone_ids.length),
  );

export const previewWorkspaceBrush = (
  projectId: string,
  request: BrushPreviewRequest,
  signal: AbortSignal,
) => api.previewBrush(projectId, request, signal);

export const previewWorkspaceAutomatic = (
  projectId: string,
  request: AutomaticPlacementRequest,
  signal: AbortSignal,
  publish?: (progress: AutomaticPlacementPreview) => void,
) =>
  completeSearchDomainPreview(
    (passSignal) =>
      api.previewAutomaticPlacement(projectId, request, passSignal),
    signal,
    publish,
  );

export const previewWorkspaceRecommendation = (
  projectId: string,
  request: RecommendationRequest | BuildingScreenRequest,
  signal: AbortSignal,
  publish?: (progress: RecommendationPreview) => void,
) =>
  completeSearchDomainPreview(
    (passSignal) =>
      'screen_side' in request
        ? api.previewBuildingScreen(projectId, request, passSignal)
        : api.previewRecommendation(projectId, request, passSignal),
    signal,
    publish,
    PATTERN_PASS_RESPONSE_TIMEOUT_MS * Math.max(1, request.zone_ids.length),
  );
