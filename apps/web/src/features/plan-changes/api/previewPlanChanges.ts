import { api, type PlanChangeSetDraft } from '@green/api-client';

export const previewPlanChanges = (
  projectId: string,
  draft: PlanChangeSetDraft,
  signal: AbortSignal,
) => api.previewPlanChanges(projectId, draft, signal);
