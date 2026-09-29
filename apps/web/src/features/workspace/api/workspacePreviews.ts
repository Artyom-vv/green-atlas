import {
  api,
  type BrushPreviewRequest,
  type BuildingScreenRequest,
  type PatternPreviewRequest,
  type PatternPreview,
  type RecommendationRequest,
  type AutomaticPlacementRequest,
} from '@green/api-client';
import { completePatternPreview } from './completePatternPreview';
import { PATTERN_PASS_RESPONSE_TIMEOUT_MS } from './patternPreviewPass';

export const previewWorkspacePattern = (
  projectId: string,
  request: PatternPreviewRequest,
  signal: AbortSignal,
  publish?: (progress: PatternPreview) => void,
) =>
  completePatternPreview(
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
) => api.previewAutomaticPlacement(projectId, request, signal);

export const previewWorkspaceRecommendation = (
  projectId: string,
  request: RecommendationRequest | BuildingScreenRequest,
  signal: AbortSignal,
) =>
  'screen_side' in request
    ? api.previewBuildingScreen(projectId, request, signal)
    : api.previewRecommendation(projectId, request, signal);
