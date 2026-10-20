import { useEffect, useRef } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import type { Project, ProjectOperation, ProjectRead } from '@green/api-client';
import { savedProjectQuery } from '@/entities/project/api/savedProjectQuery';
import { PREPARATION_MESSAGES } from '@/entities/source-data/config/sourceMessages';
import { UnconfirmedPreparation } from './preparationRecovery';
import { matchesPreparationPublication } from './preparationCompletion';
import { fromPreviousSource, sourceIdentity } from './sourcePreparation';

const COMPLETION_READ_RETRY = { maxRetries: 2, delayMs: 500 } as const;

interface PreparationCompletionOptions {
  projectId: string;
  project?: Project;
  operation?: ProjectOperation;
  writePending: boolean;
  onProject: (project: ProjectRead) => void;
  onCaptureRead: () => void;
  onOpenMap: () => void;
}

/** Читает сохранённый результат подготовки. Квитанция остаётся доступной
 * до получения актуального проекта или повторного чтения. */
export function usePreparationCompletion({
  projectId,
  project,
  operation,
  writePending,
  onProject,
  onCaptureRead,
  onOpenMap,
}: PreparationCompletionOptions) {
  const client = useQueryClient();
  const source = sourceIdentity(project);
  const completed =
    operation?.id &&
    operation.project_id === projectId &&
    (operation.kind === 'calculate_geometry' ||
      operation.kind === 'prepare_cad_capture') &&
    operation.status === 'completed' &&
    !fromPreviousSource(operation, project)
      ? operation
      : undefined;
  const enabled = Boolean(completed && !writePending);
  const handled = useRef<string | undefined>(undefined);
  const key = JSON.stringify([projectId, source, completed?.id]);
  const query = useQuery({
    queryKey: ['preparation-completion', projectId, source, completed?.id],
    enabled,
    // Разрыв сети повторяет только чтение завершённой операции, без новой записи.
    retry: (failureCount, error) =>
      failureCount < COMPLETION_READ_RETRY.maxRetries &&
      error instanceof TypeError,
    retryDelay: (attempt) => COMPLETION_READ_RETRY.delayMs * 2 ** attempt,
    networkMode: 'always',
    staleTime: 0,
    refetchOnMount: 'always',
    queryFn: async () => {
      if (!completed) throw new Error(PREPARATION_MESSAGES.incomplete);
      const options = savedProjectQuery(projectId);
      // Чтение, начатое до квитанции завершения, не подтверждает её результат.
      await Promise.all([
        client.cancelQueries({ queryKey: options.queryKey, exact: true }),
        client.cancelQueries({
          queryKey: ['setup-project', projectId],
          exact: true,
        }),
      ]);
      const fresh = await client.fetchQuery(options);
      if (
        !matchesPreparationPublication(
          fresh,
          completed,
          source,
          project?.state_version ?? 0,
        )
      )
        throw new UnconfirmedPreparation(PREPARATION_MESSAGES.unconfirmed);
      // Проект хранится в общем кеше savedProjectQuery.
      return { stateVersion: fresh.state_version };
    },
  });
  const candidate = client.getQueryData(savedProjectQuery(projectId).queryKey);
  const publication =
    query.isSuccess &&
    candidate?.state_version === query.data.stateVersion &&
    matchesPreparationPublication(
      candidate,
      completed,
      source,
      project?.state_version ?? 0,
    )
      ? candidate
      : undefined;
  useEffect(() => {
    if (!enabled || query.isFetching || !publication || handled.current === key)
      return;
    handled.current = key;
    onProject(publication);
    void client.invalidateQueries({ queryKey: ['data-passport', projectId] });
    if (completed?.kind === 'prepare_cad_capture') onCaptureRead();
    else onOpenMap();
  }, [
    client,
    completed?.kind,
    enabled,
    key,
    onCaptureRead,
    onOpenMap,
    onProject,
    projectId,
    publication,
    query.isFetching,
  ]);
  const reading = enabled && (query.isPending || query.isFetching);
  const failed =
    enabled &&
    !query.isFetching &&
    (query.isError || (query.isSuccess && !publication));
  const capture = completed?.kind === 'prepare_cad_capture';
  return {
    reading,
    failed,
    label: reading
      ? capture
        ? PREPARATION_MESSAGES.readingDrawing
        : PREPARATION_MESSAGES.openingMap
      : undefined,
    recovery: failed
      ? {
          message: capture
            ? PREPARATION_MESSAGES.drawingReadFailed
            : PREPARATION_MESSAGES.mapOpenFailed,
          actionLabel: capture
            ? PREPARATION_MESSAGES.retryDrawingRead
            : PREPARATION_MESSAGES.retryMapOpen,
          loading: query.isFetching,
          onRetry: () => {
            void query.refetch();
          },
        }
      : undefined,
  };
}
