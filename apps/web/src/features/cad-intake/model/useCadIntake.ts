import { useEffect, useRef } from 'react';
import {
  skipToken,
  useIsMutating,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import { operationActive } from '@/entities/operation/model/operationPresentation';
import { startIntake, type IntakeSelection } from '../api/startIntake';
import { useCadOperation } from './useCadOperation';

export interface CadIntakeOptions {
  projectId?: string;
  routeKey: string;
  projectStateVersion?: number;
  hasSource?: boolean;
  onNavigate: (path: string) => void;
}

export function useCadIntake({
  projectId,
  routeKey,
  onNavigate,
}: CadIntakeOptions) {
  const client = useQueryClient();
  const sessionKey = ['cad-intake-project', routeKey];
  const session = useQuery<string>({
    queryKey: sessionKey,
    queryFn: skipToken,
    enabled: false,
    gcTime: Infinity,
    staleTime: Infinity,
  });
  const resolvedId = projectId ?? session.data;
  const mutationKey = ['cad-intake-start', routeKey, projectId];
  const pending = useIsMutating({ mutationKey, exact: true });
  const mounted = useRef(true);
  const starting = useRef(false);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const { query: operation, cancel, publish } = useCadOperation(resolvedId);
  const start = useMutation({
    mutationKey,
    mutationFn: (selection: IntakeSelection) => {
      return startIntake(
        selection,
        projectId ?? client.getQueryData<string>(sessionKey),
        (id) => client.setQueryData(sessionKey, id),
      );
    },
    onSuccess: async (result) => {
      await publish(result.operation);
      void client.invalidateQueries({ queryKey: ['projects'] });
      if (mounted.current && projectId !== result.projectId)
        onNavigate(`/projects/${result.projectId}/import?source=cad`);
    },
  });
  const launch = (selection: IntakeSelection) => {
    if (
      starting.current ||
      client.isMutating({ mutationKey, exact: true }) ||
      operationActive(operation.data)
    )
      return;
    starting.current = true;
    void start
      .mutateAsync(selection)
      .catch(() => undefined)
      .finally(() => {
        starting.current = false;
      });
  };
  return {
    operation: operation.data,
    loading: operation.isLoading,
    busy: pending > 0 || operationActive(operation.data),
    error: start.error ?? cancel.error ?? operation.error,
    launch,
    cancel: () => cancel.mutate(),
    cancelling: cancel.isPending,
    canRefresh: Boolean(resolvedId),
    refresh: async () => {
      if (!resolvedId) return;
      const refreshed = await operation.refetch();
      if (!refreshed.error) {
        start.reset();
        cancel.reset();
      }
    },
  };
}
