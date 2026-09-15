import { api } from '@green/api-client';
import { queryOptions } from '@tanstack/react-query';
const PROJECT_STALE_TIME_MS = 5000;
export function assistantProjectQuery(projectId: string) {
  return queryOptions({
    queryKey: ['workspace-project', projectId],
    queryFn: () => api.getProject(projectId, false),
    enabled: Boolean(projectId),
    staleTime: PROJECT_STALE_TIME_MS,
  });
}
