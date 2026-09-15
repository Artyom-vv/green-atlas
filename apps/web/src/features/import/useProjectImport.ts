import { useCallback, useEffect, useLayoutEffect, useRef } from 'react';
import {
  skipToken,
  useIsMutating,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import { api } from '@green/api-client';
import { importProject } from './api/importProject';
import {
  hasFixedProjectSource,
  isProjectSnapshotQuery,
  isProjectSourceQuery,
  projectImportDestination,
} from './model/importProject';

export interface ProjectImportOptions {
  projectId?: string;
  /** The router location key distinguishes separate new-project intents. */
  routeKey: string;
  onNavigate: (path: string) => void;
}

interface ImportAttempt {
  file: File;
  routeKey: string;
  projectId?: string;
  sessionKey: readonly ['project-import-session', string, string | undefined];
}

export function useProjectImport({
  projectId,
  routeKey,
  onNavigate,
}: ProjectImportOptions) {
  const queryClient = useQueryClient();
  const latestRoute = useRef({ routeKey, projectId });
  useLayoutEffect(() => {
    latestRoute.current = { routeKey, projectId };
  }, [routeKey, projectId]);
  const mounted = useRef(true);
  const startingRoutes = useRef(new Set<string>());
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const mutationKey = ['project-import', routeKey, projectId] as const;
  const sessionKey = ['project-import-session', routeKey, projectId] as const;
  // This is an import intent, not server project data. Retain the created ID
  // across a route unmount/back navigation, including a late create response.
  useQuery<string>({
    queryKey: sessionKey,
    queryFn: skipToken,
    enabled: false,
    gcTime: Infinity,
    staleTime: Infinity,
  });
  const currentUploads = useIsMutating({ mutationKey, exact: true });
  const projectQuery = useQuery({
    queryKey: ['setup-project', projectId],
    queryFn: () => api.getProject(projectId!, false),
    enabled: Boolean(projectId),
  });

  const mutation = useMutation({
    mutationKey,
    retry: false,
    mutationFn: async (attempt: ImportAttempt) => {
      const retryProjectId = queryClient.getQueryData<string>(
        attempt.sessionKey,
      );
      const project = await importProject(api, {
        file: attempt.file,
        projectId: attempt.projectId ?? retryProjectId,
        onProjectResolved: (id) => {
          queryClient.setQueryData(attempt.sessionKey, id);
        },
      });

      // Cache publication belongs to this captured import, including when its
      // route has unmounted. A late result can only replace its own source.
      const sourceQueries = {
        predicate: (query: { queryKey: readonly unknown[] }) =>
          isProjectSourceQuery(query.queryKey, project.id),
      };
      await queryClient.cancelQueries(sourceQueries);
      queryClient.removeQueries({
        predicate: (query) =>
          sourceQueries.predicate(query) &&
          !isProjectSnapshotQuery(query.queryKey),
      });
      queryClient.setQueryData(['setup-project', project.id], project);
      queryClient.setQueryData(['workspace-project', project.id], project);
      void queryClient.invalidateQueries({ queryKey: ['projects'] });
      return project;
    },
    onSuccess: (project, attempt) => {
      const current = latestRoute.current;
      if (
        mounted.current &&
        current.routeKey === attempt.routeKey &&
        current.projectId === attempt.projectId
      ) {
        onNavigate(projectImportDestination(project));
      }
    },
  });

  const upload = useCallback(
    (file: File) => {
      const scope = JSON.stringify([routeKey, projectId]);
      if (
        startingRoutes.current.has(scope) ||
        queryClient.isMutating({
          mutationKey: ['project-import', routeKey, projectId],
          exact: true,
        })
      ) {
        return;
      }
      startingRoutes.current.add(scope);
      void mutation
        .mutateAsync({
          file,
          routeKey,
          projectId,
          sessionKey: ['project-import-session', routeKey, projectId],
        })
        .catch(() => {
          // The mutation owns the visible error; this handler consumes the
          // promise without converting a failed upload into a second create.
        })
        .finally(() => startingRoutes.current.delete(scope));
    },
    [mutation, projectId, queryClient, routeKey],
  );
  const currentAttempt =
    mutation.variables?.routeKey === routeKey &&
    mutation.variables.projectId === projectId;
  const error =
    currentAttempt && mutation.error instanceof Error
      ? mutation.error.message
      : undefined;

  return {
    project: projectQuery.data,
    loadingProject: Boolean(projectId && projectQuery.isLoading),
    projectError: projectQuery.isError,
    retryProject: () => projectQuery.refetch(),
    sourceReadOnly: hasFixedProjectSource(projectQuery.data),
    uploading: currentUploads > 0,
    error,
    upload,
  };
}
