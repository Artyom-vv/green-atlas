/** Наблюдение распознавания через SSE и явное восстановление неизвестной команды. */
import { useCallback, useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { RECOGNITION_MESSAGES } from '@/entities/source-data/config/sourceMessages';
import { preparationApi } from '../api/preparationApi';

import {
  checkRecognitionSource,
  layerRecognitionOptions,
  recognitionKey,
  type RecognitionBinding,
} from '../api/layerRecognitionQuery';

interface Options extends RecognitionBinding {
  enabled: boolean;
  mode?: 'discover' | 'observe';
}

interface Request extends RecognitionBinding {
  mode: 'read' | 'resume' | 'start';
}

/** Запросы распознавания привязаны к исходнику. Восстановление только читает
 * состояние; новый запуск и продолжение требуют отдельного действия. */
export function useLayerRecognition({
  projectId,
  source,
  sourceSha,
  enabled,
  mode = 'discover',
}: Options) {
  const queryClient = useQueryClient();
  const binding = { projectId, source, sourceSha };
  const layerRecognitionQuery = useLayerRecognitionObservation(
    binding,
    mode,
    enabled,
  );
  const layerRecognition =
    layerRecognitionQuery.data?.source_sha256 === sourceSha
      ? (layerRecognitionQuery.data ?? undefined)
      : undefined;
  const action = useMutation({
    mutationFn: async (request: Request) => {
      const result = await (request.mode === 'read'
        ? preparationApi.getLayerRecognitionStatus(request.projectId)
        : preparationApi.retryLayerRecognition(request.projectId));
      return result === null
        ? null
        : checkRecognitionSource(result, request.sourceSha);
    },
    // Потерянный ответ POST не разрешает повторную отправку команды.
    retry: false,
    networkMode: 'always',
    // Поздний ответ принадлежит тому проекту и исходнику, где команда началась.
    onSuccess: (result, request) => {
      queryClient.setQueryData(recognitionKey(request), result);
      if (request.projectId === projectId && request.source === source)
        layerRecognitionQuery.resumeUpdates();
    },
  });
  const currentAction =
    action.variables?.projectId === projectId &&
    action.variables.source === source;
  // Фоновый GET мог начаться до POST. Неизвестный результат команды снимается
  // только чтением, которое пользователь явно запросил после ошибки.
  const unknownAction = Boolean(currentAction && action.isError);
  const noKnownJob = Boolean(
    !unknownAction &&
    layerRecognitionQuery.isSuccess &&
    layerRecognitionQuery.data === null &&
    !layerRecognitionQuery.isFetching,
  );
  const request: Request = {
    ...binding,
    mode: noKnownJob
      ? 'start'
      : !layerRecognitionQuery.isError &&
          (layerRecognitionQuery.isFetchedAfterMount ||
            !layerRecognitionQuery.isFetching) &&
          !unknownAction &&
          layerRecognition?.status === 'failed'
        ? 'resume'
        : 'read',
  };
  const retryLayerRecognition = {
    isPending: Boolean(currentAction && action.isPending),
    isPaused: Boolean(currentAction && action.isPaused),
    checkingStatus: Boolean(
      currentAction && action.isPending && action.variables?.mode === 'read',
    ),
    noKnownJob,
    isError: unknownAction,
    error: unknownAction ? action.error : null,
    mutate: () => action.mutate(request),
    mutateAsync: () => action.mutateAsync(request),
  };
  return { layerRecognitionQuery, retryLayerRecognition, layerRecognition };
}

/** Одно первоначальное чтение и один SSE-поток для подготовки и открытого проекта. */
export function useLayerRecognitionObservation(
  binding: RecognitionBinding,
  mode: 'discover' | 'observe',
  enabled: boolean,
) {
  const { projectId, source, sourceSha } = binding;
  const queryClient = useQueryClient();
  const query = useQuery({
    ...layerRecognitionOptions(binding, mode),
    enabled,
  });
  const key = JSON.stringify(recognitionKey(binding));
  const [connectionFailure, setConnectionFailure] = useState<{
    key: string;
    error: Error;
  }>();
  const [connectionRevision, setConnectionRevision] = useState(0);
  const resumeUpdates = useCallback(() => {
    setConnectionFailure(undefined);
    setConnectionRevision((current) => current + 1);
  }, []);
  const observing =
    enabled &&
    query.isFetchedAfterMount &&
    !query.isError &&
    query.data?.status === 'running';
  useEffect(() => {
    if (!observing) return;
    const controller = new AbortController();
    let cancelled = false;
    const fail = (error: unknown) => {
      if (cancelled) return;
      setConnectionFailure({
        key,
        error:
          error instanceof Error
            ? error
            : new Error(RECOGNITION_MESSAGES.phases.unknown.title),
      });
    };
    let stop: (() => void) | undefined;
    try {
      stop = preparationApi.subscribeLayerRecognition(projectId, {
        signal: controller.signal,
        onError: fail,
        onResult: (result) => {
          if (cancelled) return;
          try {
            const accepted =
              result === null
                ? null
                : checkRecognitionSource(result, sourceSha);
            queryClient.setQueryData(
              recognitionKey({ projectId, source }),
              accepted,
            );
            setConnectionFailure(undefined);
          } catch (error) {
            fail(error);
            controller.abort();
          }
        },
      });
    } catch (error) {
      fail(error);
    }
    return () => {
      cancelled = true;
      controller.abort();
      stop?.();
    };
  }, [
    key,
    observing,
    projectId,
    queryClient,
    connectionRevision,
    source,
    sourceSha,
  ]);
  const streamError =
    connectionFailure?.key === key ? connectionFailure.error : null;
  return {
    ...query,
    isError: query.isError || Boolean(streamError),
    isSuccess: query.isSuccess && !streamError,
    error: streamError ?? query.error,
    resumeUpdates,
  };
}
