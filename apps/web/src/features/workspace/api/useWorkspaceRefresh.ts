import { useCallback } from 'react';
import { useQueryClient } from '@tanstack/react-query';

export interface WorkspaceRefreshOptions {
  mapGeometry?: boolean;
  strict?: boolean;
}

/** Planting changes refresh the plan overlay. Only changed terrain reloads CAD. */
export function useWorkspaceRefresh(projectId: string) {
  const client = useQueryClient();
  return useCallback(
    async ({
      mapGeometry = false,
      strict = false,
    }: WorkspaceRefreshOptions = {}) => {
      const keys = [
        ['workspace-project', projectId],
        ['plan-history', projectId],
        ['projects'],
      ];
      if (mapGeometry) keys.push(['map-features', projectId]);
      await Promise.all(
        keys.map((queryKey) =>
          client.invalidateQueries({ queryKey }, { throwOnError: strict }),
        ),
      );
    },
    [client, projectId],
  );
}
