import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type ProjectOperation } from '@green/api-client';
import { operationActive } from '@/entities/operation/model/operationPresentation';
import { mergeOperationReceipt } from './mergeOperationReceipt';

const CAD_POLL_INTERVAL_MS = 1_000;
type CadOperationKind =
  'inspect_cad_package' | 'prepare_cad_preview' | 'prepare_cad_project';
const operationKey = (projectId: string | undefined, kind: CadOperationKind) =>
  [
    kind === 'inspect_cad_package'
      ? 'cad-intake-operation'
      : kind === 'prepare_cad_preview'
        ? 'cad-preview-operation'
        : 'cad-prepare-operation',
    projectId,
  ] as const;

export function useCadOperation(
  projectId?: string,
  kind: CadOperationKind = 'inspect_cad_package',
) {
  const client = useQueryClient();
  const query = useQuery({
    queryKey: operationKey(projectId, kind),
    queryFn: ({ signal }) => api.getLatestOperation(projectId!, kind, signal),
    enabled: Boolean(projectId),
    retry: false,
    refetchInterval: (state) =>
      operationActive(state.state.data) ? CAD_POLL_INTERVAL_MS : false,
  });
  const publish = async (
    operation: ProjectOperation,
    cancelTargetId?: string,
  ) => {
    const queryKey = operationKey(operation.project_id, kind);
    // The receipt must win over an older in-flight status response.
    await client.cancelQueries({ queryKey, exact: true });
    client.setQueryData<ProjectOperation | null>(queryKey, (current) =>
      mergeOperationReceipt(current, operation, cancelTargetId),
    );
  };
  const cancel = useMutation({
    mutationFn: async () => {
      if (!projectId || !query.data?.id)
        throw new Error('Операция ещё не подтверждена. Обновите состояние.');
      const targetId = query.data.id;
      const operation = await api.cancelOperation(projectId, targetId);
      return { operation, targetId };
    },
    onSuccess: ({ operation, targetId }) => publish(operation, targetId),
  });
  return { query, cancel, publish };
}
