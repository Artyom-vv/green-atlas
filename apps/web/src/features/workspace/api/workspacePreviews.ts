import {
  api,
  type BrushPreviewRequest,
  type BuildingScreenRequest,
  type PatternPreviewRequest,
  type RecommendationRequest,
} from '@green/api-client';

export const previewWorkspacePattern = (
  projectId: string,
  request: PatternPreviewRequest,
  signal: AbortSignal,
) => api.previewPlanPattern(projectId, request, signal);

export const previewWorkspaceBrush = (
  projectId: string,
  request: BrushPreviewRequest,
  signal: AbortSignal,
) => api.previewBrush(projectId, request, signal);

export const previewWorkspaceRecommendation = (
  projectId: string,
  request: RecommendationRequest | BuildingScreenRequest,
  signal: AbortSignal,
) =>
  'screen_side' in request
    ? api.previewBuildingScreen(projectId, request, signal)
    : api.previewRecommendation(projectId, request, signal);
