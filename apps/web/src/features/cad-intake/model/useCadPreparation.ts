import { useEffect, useRef } from 'react';
import {
  useIsMutating,
  useMutation,
  useQueryClient,
} from '@tanstack/react-query';
import type { ProjectOperation } from '@green/api-client';
import { operationActive } from '@/entities/operation/model/operationPresentation';
import { useCadOperation } from './useCadOperation';
import { useOpenCadPreview } from './useOpenCadPreview';

export interface CadPreparationOptions {
  projectId?: string;
  version?: number;
  onNavigate: (path: string) => void;
}

export function useCadPreparation<Request>(
  { projectId, version, onNavigate }: CadPreparationOptions,
  kind: 'prepare_cad_preview' | 'prepare_cad_project',
  startOperation: (
    projectId: string,
    version: number,
    request: Request,
  ) => Promise<ProjectOperation>,
) {
  const client = useQueryClient();
  const mutationKey = [
    kind === 'prepare_cad_preview' ? 'cad-preview-start' : 'cad-prepare-start',
    projectId,
  ];
  const pending = useIsMutating({ mutationKey, exact: true });
  const { query, cancel, publish } = useCadOperation(projectId, kind);
  const opening = useOpenCadPreview(projectId, onNavigate);
  const startedId = useRef<string | undefined>(undefined);
  const start = useMutation({
    mutationKey,
    mutationFn: (request: Request) => {
      if (!projectId || version == null)
        throw new Error('Обновите проект перед подготовкой карты.');
      return startOperation(projectId, version, request);
    },
    onSuccess: async (operation) => {
      startedId.current = operation.id;
      await publish(operation);
    },
  });
  const completed = query.data?.status === 'completed' ? query.data : undefined;
  useEffect(() => {
    if (completed?.id && completed.id === startedId.current) {
      startedId.current = undefined;
      opening.mutate(completed);
    }
  }, [completed, opening]);
  const busy = pending > 0 || operationActive(query.data);
  return {
    operation: query.data,
    loading: query.isLoading,
    busy,
    error: start.error ?? cancel.error ?? opening.error ?? query.error,
    launch: (request: Request) => {
      if (!busy && !client.isMutating({ mutationKey, exact: true }))
        start.mutate(request);
    },
    cancel: () => cancel.mutate(),
    cancelling: cancel.isPending,
    open: () => completed && opening.mutate(completed),
    opening: opening.isPending,
    refresh: async () => {
      if (!projectId) return;
      const result = await query.refetch();
      if (!result.error) {
        start.reset();
        cancel.reset();
        opening.reset();
      }
    },
  };
}
