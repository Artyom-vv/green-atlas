import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@green/api-client';
import { validateAcknowledgedProjectRead } from '@/entities/project/model/acknowledgedRead';

export function useSavedProjectEntry(projectId: string) {
  const client = useQueryClient();
  return useQuery({
    queryKey: ['import-project-read', projectId],
    queryFn: async () =>
      validateAcknowledgedProjectRead(
        client,
        projectId,
        await api.getProjectRead(projectId, false),
      ),
    enabled: Boolean(projectId),
    networkMode: 'always',
    retry: false,
    staleTime: 0,
    refetchOnMount: 'always',
  });
}
