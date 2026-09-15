import { api } from '@green/api-client';
import type { ChangeSetPreview } from '@green/api-client';

export function commitPlanChange(projectId: string, preview: ChangeSetPreview) {
  return api.applyPlanChanges(projectId, preview);
}
