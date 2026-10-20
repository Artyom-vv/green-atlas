/** Наблюдение квитанции подготовки: одно чтение и SSE, привязанные к исходнику и исходной версии. */
import { useCallback, useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import type { ProjectOperation } from '@green/api-client';
import { PREPARATION_MESSAGES } from '@/entities/source-data/config/sourceMessages';
import { operationActive } from '@/entities/operation/model/operationPresentation';
import { preparationApi } from '../api/preparationApi';

interface Binding {
  projectId: string;
  operationId?: string;
  source: string;
  expectedStateVersion?: number;
  expectedKind?: ProjectOperation['kind'];
}

export function usePreparationOperationObservation(binding: Binding) {
  const { projectId, operationId, source, expectedStateVersion, expectedKind } =
    binding;
  const client = useQueryClient();
  const key = JSON.stringify([
    projectId,
    operationId,
    source,
    expectedStateVersion,
    expectedKind,
  ]);
  const [failure, setFailure] = useState<{ key: string; error: Error }>();
  const [connectionRevision, setConnectionRevision] = useState(0);
  const query = useQuery({
    queryKey: ['operation', projectId, operationId],
    queryFn: async ({ signal }) =>
      acceptOperation(
        await preparationApi.getOperation(projectId, operationId!, signal),
        binding,
      ),
    enabled: Boolean(operationId),
    retry: false,
    networkMode: 'always',
    staleTime: 0,
    refetchOnMount: 'always',
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
  const observing = Boolean(
    operationId &&
    query.isFetchedAfterMount &&
    !query.isError &&
    operationActive(query.data),
  );
  const resume = useCallback(() => {
    setFailure(undefined);
    setConnectionRevision((current) => current + 1);
  }, []);
  useEffect(() => {
    if (!observing || !operationId) return;
    const controller = new AbortController();
    let cancelled = false;
    const fail = (error: Error) => {
      if (!cancelled) setFailure({ key, error });
    };
    let stop: (() => void) | undefined;
    try {
      stop = preparationApi.subscribeOperation(projectId, operationId, {
        signal: controller.signal,
        onError: fail,
        onResult: (result) => {
          if (cancelled) return;
          try {
            const accepted = acceptOperation(result, {
              projectId,
              operationId,
              source,
              expectedStateVersion,
              expectedKind,
            });
            client.setQueryData(
              ['operation', projectId, operationId],
              accepted,
            );
            setFailure(undefined);
          } catch (error) {
            fail(
              error instanceof Error
                ? error
                : new Error(PREPARATION_MESSAGES.unknownStatus),
            );
            controller.abort();
          }
        },
      });
    } catch (error) {
      fail(
        error instanceof Error
          ? error
          : new Error(PREPARATION_MESSAGES.unknownStatus),
      );
    }
    return () => {
      cancelled = true;
      controller.abort();
      stop?.();
    };
  }, [
    client,
    connectionRevision,
    expectedKind,
    expectedStateVersion,
    key,
    observing,
    operationId,
    projectId,
    source,
  ]);
  const readStatus = query.refetch;
  const refetch = useCallback(
    async (...args: Parameters<typeof readStatus>) => {
      const result = await readStatus(...args);
      if (result.isSuccess) resume();
      return result;
    },
    [readStatus, resume],
  );
  const streamError = failure?.key === key ? failure.error : null;
  return {
    ...query,
    refetch,
    isError: query.isError || Boolean(streamError),
    isSuccess: query.isSuccess && !streamError,
    error: streamError ?? query.error,
  };
}

function acceptOperation(result: ProjectOperation, binding: Binding) {
  if (
    result.id !== binding.operationId ||
    result.project_id !== binding.projectId ||
    (binding.expectedStateVersion !== undefined &&
      result.project_state_version !== binding.expectedStateVersion) ||
    (binding.expectedKind !== undefined && result.kind !== binding.expectedKind)
  )
    throw new Error(PREPARATION_MESSAGES.unconfirmed);
  return result;
}
