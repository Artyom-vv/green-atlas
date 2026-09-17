import { useEffect, useMemo, useState } from 'react';
import { useForm, useWatch } from 'react-hook-form';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type {
  LayerMapping,
  Project,
  ProjectOperation,
} from '@green/api-client';
import { preparationApi } from '../api/preparationApi';
import { mappingKey } from './preparationRecovery';
import { usePreparationCommit } from './usePreparationCommit';
import {
  EMPTY_LAYERS,
  sourceIdentity,
  layerMappings,
  operationActive,
  fromPreviousSource,
} from './sourcePreparation';

export interface SourceMappingForm {
  source: string;
  mappings: Record<string, LayerMapping>;
}
export interface SourcePreparationOptions {
  projectId: string;
  navigate: (to: string) => void;
}

const OPERATION_POLL_INTERVAL_MS = 350;
export function useSourcePreparation({
  projectId,
  navigate,
}: SourcePreparationOptions) {
  const queryClient = useQueryClient();
  const [reloadError, setReloadError] = useState<unknown>();
  const projectQuery = useQuery({
    queryKey: ['setup-project', projectId],
    queryFn: () => preparationApi.getProject(projectId, false),
    enabled: Boolean(projectId),
  });
  const dataPassportQuery = useQuery({
    queryKey: ['data-passport', projectId],
    queryFn: () => preparationApi.getDataPassport(projectId),
    enabled: Boolean(projectId),
    staleTime: 10_000,
  });
  const form = useForm<SourceMappingForm>({
    defaultValues: { source: '', mappings: {} },
    shouldUnregister: false,
  });
  const mappingDraft = useWatch({
    control: form.control,
    compute: (values: SourceMappingForm) => values,
  });
  const setMappingDraft = (draft: SourceMappingForm) => {
    form.setValue('source', draft.source);
    form.setValue('mappings', draft.mappings, { shouldDirty: true });
  };
  const [trackedOperation, setTrackedOperation] = useState<{
    source: string;
    id?: string;
  }>();
  const layers = projectQuery.data?.layers ?? EMPTY_LAYERS;
  const currentSource = sourceIdentity(projectQuery.data);
  const cadPreview = projectQuery.data?.import_status?.mode === 'cad_preview';
  const operationId =
    !cadPreview && trackedOperation?.source === currentSource
      ? trackedOperation.id
      : undefined;
  const savedMappings = useMemo(() => layerMappings(layers), [layers]);
  // Refetches of one source preserve edits; a new source never renders the
  // old source's draft, including the render before an effect could reset it.
  const mappings =
    mappingDraft?.source === currentSource
      ? mappingDraft.mappings
      : savedMappings;
  const mappingsChanged =
    mappingKey(Object.values(mappings)) !==
    mappingKey(Object.values(savedMappings));
  const setMappings = (next: Record<string, LayerMapping>) =>
    setMappingDraft({ source: currentSource, mappings: next });
  const sourceWarnings = projectQuery.data?.source_file?.warnings ?? [];
  const sourceReadOnly = Boolean(
    projectQuery.data?.map_ready &&
    projectQuery.data.plan &&
    !projectQuery.data.source_review,
  );
  const reviewOnly =
    projectQuery.data?.import_status?.editability === 'read_only';

  const requiredLayers = useMemo(
    () => layers.filter((layer) => layer.required),
    [layers],
  );
  const requiredReady = useMemo(
    () =>
      requiredLayers.every((layer) => {
        const kind = mappings[layer.id]?.kind;
        return Boolean(kind) && kind !== 'ignore';
      }),
    [mappings, requiredLayers],
  );
  const incompleteConstraintLayers = useMemo(
    () =>
      layers.filter(
        (layer) =>
          !layer.geometry_complete &&
          mappings[layer.id]?.kind &&
          mappings[layer.id]?.kind !== 'ignore',
      ),
    [layers, mappings],
  );
  const readinessBlockedReason = !requiredReady
    ? 'Назначьте роль обязательным слоям границы перед подготовкой карты.'
    : incompleteConstraintLayers.length
      ? 'В отдельных слоях есть нерассчитанная геометрия. Редактор можно открыть без расчёта.'
      : undefined;
  const hasPlanningBoundary = requiredLayers.length > 0;
  const latestOperationQuery = useQuery({
    queryKey: [
      'latest-operation',
      projectId,
      'calculate_geometry',
      currentSource,
    ],
    queryFn: () =>
      preparationApi.getLatestOperation(projectId, 'calculate_geometry'),
    enabled: Boolean(
      projectId && projectQuery.data && !sourceReadOnly && !cadPreview,
    ),
    staleTime: 0,
    retry: false,
  });
  const operationQuery = useQuery({
    queryKey: ['operation', projectId, operationId],
    queryFn: () => preparationApi.getOperation(projectId, operationId!),
    enabled: Boolean(operationId),
    retry: false,
    refetchOnMount: 'always',
    refetchInterval: (query) =>
      !query.state.error && operationActive(query.state.data)
        ? OPERATION_POLL_INTERVAL_MS
        : false,
  });
  const latest = cadPreview ? undefined : latestOperationQuery.data;
  const latestUnfinishedOperation =
    latest &&
    latest.status !== 'completed' &&
    (!fromPreviousSource(latest, projectQuery.data) || operationActive(latest))
      ? latest
      : undefined;

  useEffect(() => {
    if (!operationId && latestUnfinishedOperation)
      setTrackedOperation({
        source: currentSource,
        id: latestUnfinishedOperation.id,
      });
  }, [currentSource, latestUnfinishedOperation, operationId]);

  useEffect(() => {
    if (
      cadPreview ||
      operationQuery.data?.status !== 'completed' ||
      fromPreviousSource(operationQuery.data, projectQuery.data)
    )
      return;
    // The operation receipt confirms completion; the next view must read the
    // prepared project instead of the still-fresh import response.
    for (const key of ['setup-project', 'workspace-project', 'data-passport'])
      void queryClient.invalidateQueries({
        queryKey: [key, projectId],
        refetchType: 'none',
      });
    navigate(`/projects/${projectId}/workspace`);
  }, [
    cadPreview,
    navigate,
    operationQuery.data,
    projectId,
    projectQuery.data,
    queryClient,
  ]);

  const trackOperation = (operation: ProjectOperation, source: string) => {
    queryClient.setQueryData(['operation', projectId, operation.id], operation);
    setTrackedOperation({ source, id: operation.id });
  };
  const preparationMutation = usePreparationCommit({
    source: currentSource,
    onOperation: trackOperation,
    onProject: (fresh) => {
      queryClient.setQueryData<Project>(
        ['setup-project', projectId],
        (current) =>
          !current || (current.state_version ?? 0) <= (fresh.state_version ?? 0)
            ? fresh
            : current,
      );
    },
  });
  const saveMutation = {
    ...preparationMutation,
    mutate: () => {
      if (
        cadPreview ||
        preparationBlocked ||
        readinessBlockedReason ||
        !projectQuery.data
      )
        return;
      const values = Object.values(mappings);
      preparationMutation.mutate({
        projectId,
        source: currentSource,
        mappings: values,
        draftKey: mappingKey(values),
        baseStateVersion: projectQuery.data.state_version ?? 0,
      });
    },
  };
  const cancelOperation = useMutation({
    mutationFn: (id: string) => preparationApi.cancelOperation(projectId, id),
    onSuccess: (next) =>
      queryClient.setQueryData(['operation', projectId, next.id], next),
  });
  const openEditor = useMutation({
    mutationFn: async () => {
      if (mappingsChanged)
        await preparationApi.saveMappings(projectId, Object.values(mappings));
      return preparationApi.openSourceEditor(projectId);
    },
    onSuccess: (project) => {
      queryClient.setQueryData(['setup-project', projectId], project);
      queryClient.setQueryData(['workspace-project', projectId], project);
      void queryClient.invalidateQueries({
        queryKey: ['data-passport', projectId],
      });
      navigate(`/projects/${projectId}/workspace`);
    },
  });
  const lastKnownOperation =
    operationQuery.data ??
    (!operationId || latestUnfinishedOperation?.id === operationId
      ? latestUnfinishedOperation
      : undefined);
  const previousSourceOperation = Boolean(
    lastKnownOperation &&
    fromPreviousSource(lastKnownOperation, projectQuery.data),
  );
  // An active operation still owns the project until it stops. A terminal
  // operation from an older import is history, not a retry for this source.
  const operation =
    cadPreview ||
    (previousSourceOperation && !operationActive(lastKnownOperation))
      ? undefined
      : lastKnownOperation;
  const statusQuery = operationId ? operationQuery : latestOperationQuery;
  const statusUnknown = !cadPreview && statusQuery.isError;
  const checkingStatus =
    !cadPreview && !sourceReadOnly && statusQuery.isPending;
  const calculating =
    saveMutation.isPending ||
    Boolean(operationId && operationQuery.isLoading) ||
    operationActive(operation);
  const preparationBlocked =
    openEditor.isPending ||
    calculating ||
    statusUnknown ||
    checkingStatus ||
    cancelOperation.isPending ||
    preparationMutation.needsRecovery ||
    Boolean(reloadError);
  const mutationError =
    saveMutation.error ?? cancelOperation.error ?? openEditor.error;

  const reloadAfterConflict = async () => {
    try {
      const result = await projectQuery.refetch({ throwOnError: true });
      if (!result.isSuccess || !result.data) throw result.error;
      form.reset({
        source: sourceIdentity(result.data),
        mappings: layerMappings(result.data.layers ?? []),
      });
      saveMutation.reset();
      cancelOperation.reset();
      openEditor.reset();
      setReloadError(undefined);
    } catch (error) {
      // Cached data in a failed refetch is not a fresh basis for the form.
      // Preserve both the draft and the original mutation conflict.
      setReloadError(error);
    }
  };

  const preparationRecovery = reloadError
    ? {
        message:
          'Не удалось обновить проект. Ваши изменения и сообщение о конфликте сохранены.',
        loading: projectQuery.isFetching,
        onRetry: () => {
          void reloadAfterConflict();
        },
      }
    : preparationMutation.needsRecovery
      ? {
          message:
            preparationMutation.message ??
            'Не удалось подтвердить состояние подготовки карты.',
          loading: preparationMutation.phase === 'recovering',
          onRetry: () => {
            void preparationMutation.recover();
          },
        }
      : undefined;

  return {
    form,
    projectQuery,
    dataPassportQuery,
    layers,
    mappings,
    mappingsChanged,
    calculationPending: Boolean(projectQuery.data?.source_review),
    setMappings,
    sourceWarnings,
    sourceReadOnly,
    reviewOnly,
    cadPreview,
    sourceReviewMessage: projectQuery.data?.import_status?.message,
    readinessBlockedReason,
    incompleteConstraintLayers,
    hasPlanningBoundary,
    operation,
    previousSourceOperation,
    statusUnknown,
    checkingStatus,
    calculating,
    preparationBlocked,
    statusQuery,
    saveMutation,
    openEditor,
    cancelOperation,
    mutationError,
    preparationRecovery,
    reloadAfterConflict,
    downloadSource: () => preparationApi.sourceDownloadUrl(projectId),
  };
}
