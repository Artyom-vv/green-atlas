import { useEffect, useMemo, useRef, useState } from 'react';
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
import { saveNativeAreaDecision } from './saveNativeAreaDecision';
import { partialGeometryAccepted } from '@/entities/source-data/model/partialGeometryAccepted';
import { autoAcceptRecognizedLayers } from './autoAcceptRecognizedLayers';
import { sourceWarningText } from '@/entities/source-data/model/sourceWarningText';
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
  const [editingSource, setEditingSource] = useState(false);
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
  const allSavedLayersConfirmed =
    layers.length > 0 && layers.every((layer) => layer.mapping_confirmed === true);
  const currentSource = sourceIdentity(projectQuery.data);
  const layerRecognitionQuery = useQuery({
    queryKey: ['layer-recognition', projectId, currentSource],
    queryFn: () => preparationApi.getLayerRecognition(projectId),
    enabled: Boolean(projectQuery.data?.layers?.length) && !allSavedLayersConfirmed,
    staleTime: Infinity,
    retry: false,
    // The desktop WebView may report an inactive document while its window is
    // visible. A running server job must still reach a terminal UI state.
    refetchIntervalInBackground: true,
    refetchInterval: (query) =>
      query.state.data?.status === 'running' ? 1500 : false,
  });
  const retryLayerRecognition = useMutation({
    mutationFn: () => preparationApi.retryLayerRecognition(projectId),
    onSuccess: (result) =>
      queryClient.setQueryData(
        ['layer-recognition', projectId, currentSource],
        result,
      ),
  });
  const layerRecognition =
    layerRecognitionQuery.data?.source_sha256 ===
    projectQuery.data?.source_file?.content_sha256
      ? layerRecognitionQuery.data
      : undefined;
  const reviewOnly =
    projectQuery.data?.import_status?.editability === 'read_only';
  const cadPreview = projectQuery.data?.import_status?.mode === 'cad_preview';
  const operationId =
    !cadPreview && trackedOperation?.source === currentSource
      ? trackedOperation.id
      : undefined;
  const savedMappings = useMemo(() => layerMappings(layers), [layers]);
  const autoApplySource = useRef<string | undefined>(undefined);
  const autoApplyRecognition = useMutation({
    mutationFn: (values: LayerMapping[]) => preparationApi.saveMappings(projectId, values),
    onSuccess: (fresh) => {
      queryClient.setQueryData<Project>(['setup-project', projectId], fresh);
      void queryClient.invalidateQueries({ queryKey: ['data-passport', projectId] });
    },
  });
  const autoApplyPending = autoApplyRecognition.isPending;
  const autoApplyMutate = autoApplyRecognition.mutate;
  const draftMappings =
    mappingDraft?.source === currentSource
      ? mappingDraft.mappings
      : savedMappings;
  const automaticallyMapped = useMemo(
    () => {
      const accepted = autoAcceptRecognizedLayers(
        layers, savedMappings, layerRecognition,
      );
      // A territory or role edited while Luna is running belongs to the user.
      // Apply the model only to layers whose draft still matches the saved state.
      return Object.fromEntries(Object.entries(draftMappings).map(([id, draft]) => [
        id,
        mappingKey([draft]) === mappingKey([savedMappings[id]])
          ? accepted[id] ?? draft
          : draft,
      ]));
    },
    [layers, savedMappings, draftMappings, layerRecognition],
  );
  const automaticAcceptanceNeeded =
    mappingKey(Object.values(automaticallyMapped)) !==
    mappingKey(Object.values(draftMappings));
  const automaticAcceptancePending = Boolean(
    layerRecognition?.status === 'completed' && automaticAcceptanceNeeded &&
    !reviewOnly && !autoApplyRecognition.isError &&
    !(projectQuery.data?.map_ready && projectQuery.data.plan && !projectQuery.data.source_review),
  );
  useEffect(() => {
    if (!projectQuery.data || !layerRecognition || reviewOnly ||
        layerRecognition.status !== 'completed' ||
        (projectQuery.data.map_ready && projectQuery.data.plan && !projectQuery.data.source_review) ||
        autoApplyPending ||
        autoApplySource.current === currentSource) return;
    if (!automaticAcceptanceNeeded) return;
    autoApplySource.current = currentSource;
    if (mappingKey(Object.values(draftMappings)) !==
        mappingKey(Object.values(savedMappings))) {
      setMappingDraft({ source: currentSource, mappings: automaticallyMapped });
    } else {
      autoApplyMutate(Object.values(automaticallyMapped));
    }
  }, [projectQuery.data, layerRecognition, reviewOnly,
    autoApplyPending, autoApplyMutate, automaticallyMapped, automaticAcceptanceNeeded,
    currentSource, draftMappings, savedMappings]);
  // Refetches of one source preserve edits; a new source never renders the
  // old source's draft, including the render before an effect could reset it.
  const mappings = draftMappings;
  const mappingsChanged =
    mappingKey(Object.values(mappings)) !==
    mappingKey(Object.values(savedMappings));
  const setMappings = (next: Record<string, LayerMapping>) =>
    setMappingDraft({ source: currentSource, mappings: next });
  const sourceWarnings = (projectQuery.data?.source_file?.warnings ?? []).map(
    sourceWarningText,
  );
  const nativeAreaProposals =
    projectQuery.data?.source_file?.native_area_proposals ?? [];
  const partialAccepted = partialGeometryAccepted(projectQuery.data);
  const sourceReadOnly = Boolean(
    projectQuery.data?.map_ready &&
    projectQuery.data.plan &&
    !projectQuery.data.source_review &&
    !editingSource,
  );

  const requiredLayers = useMemo(
    () => layers.filter((layer) => layer.required),
    [layers],
  );
  const usableBoundaryCandidates = useMemo(
    () =>
      layers.filter((layer) => layer.boundary_candidate?.status === 'usable'),
    [layers],
  );
  const liveBoundary = projectQuery.data?.import_status?.mode === 'autocad_live';
  const selectedBoundary = useMemo(
    () => layers.some((layer) => mappings[layer.id]?.kind === 'site_border'
      && (!liveBoundary || !layer.boundary_candidate
        || layer.boundary_candidate.status === 'usable')),
    [layers, mappings, liveBoundary],
  );
  const invalidBoundary = useMemo(
    () => liveBoundary
      ? layers.find((layer) => mappings[layer.id]?.kind === 'site_border'
        && layer.boundary_candidate
        && layer.boundary_candidate.status !== 'usable')
      : undefined,
    [layers, mappings, liveBoundary],
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
  const unconfirmedMappings = useMemo(
    () =>
      layers.filter((layer) => {
        const mapping = mappings[layer.id];
        return Boolean(mapping && mapping.confirmed === false);
      }),
    [layers, mappings],
  );
  const readinessBlockedReason =
    invalidBoundary
      ? `Слой «${invalidBoundary.source_name}» не образует пригодную площадь. Выберите замкнутый контур в разделе «Территория»`
      : usableBoundaryCandidates.length > 0 && !selectedBoundary
      ? 'Выберите границу проектных работ в разделе «Территория»'
      : !requiredReady
        ? 'Назначьте роль обязательным слоям границы перед подготовкой карты.'
        : unconfirmedMappings.length > 0
          ? 'Проверьте предложенные роли слоёв.'
          : undefined;
  const readinessSection =
    invalidBoundary || (usableBoundaryCandidates.length > 0 && !selectedBoundary)
      ? '01'
      : !requiredReady || unconfirmedMappings.length > 0
        ? '03'
        : undefined;
  const partialGeometryPending = incompleteConstraintLayers.length > 0 && !partialAccepted;
  const hasPlanningBoundary = selectedBoundary;
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
    refetchIntervalInBackground: true,
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
      const start = (project: Project) => preparationMutation.mutate({
        projectId,
        source: currentSource,
        mappings: values,
        draftKey: mappingKey(values),
        baseStateVersion: project.state_version ?? 0,
      });
      if (partialGeometryPending) {
        // This is the same explicit primary action the operator sees beside
        // the incompleteness notice. Keep the source-specific decision durable
        // before calculating, without a separate unlock button.
        void acceptPartialGeometry.mutateAsync().then(start).catch(() => undefined);
      } else start(projectQuery.data);
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
  const acceptPartialGeometry = useMutation({
    mutationFn: async () => {
      const project = projectQuery.data;
      const sha = project?.source_file?.content_sha256;
      if (!project || !sha) throw new Error('Исходник ещё не загружен');
      return preparationApi.acceptPartialGeometry(projectId, sha, {
        expectedStateVersion: project.state_version ?? 0,
      });
    },
    onSuccess: (project) => {
      queryClient.setQueryData(['setup-project', projectId], project);
      void queryClient.invalidateQueries({
        queryKey: ['workspace-project', projectId],
      });
      void queryClient.invalidateQueries({
        queryKey: ['data-passport', projectId],
      });
    },
  });
  const decideNativeArea = useMutation({
    mutationFn: async (input: {
      proposalId: string;
      proposalSha256: string;
      decision: 'accepted' | 'rejected';
    }) => {
      const project = projectQuery.data;
      const sourceSha = project?.source_file?.content_sha256;
      if (!project || !sourceSha) throw new Error('Снимок AutoCAD недоступен');
      return saveNativeAreaDecision(
        projectId,
        {
          source_sha256: sourceSha,
          proposal_id: input.proposalId,
          proposal_sha256: input.proposalSha256,
          decision: input.decision,
        },
        project.state_version ?? 0,
      );
    },
    onSuccess: (project) => {
      queryClient.setQueryData(['setup-project', projectId], project);
      void queryClient.invalidateQueries({
        queryKey: ['workspace-project', projectId],
      });
      void queryClient.invalidateQueries({
        queryKey: ['data-passport', projectId],
      });
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
    autoApplyPending ||
    openEditor.isPending ||
    acceptPartialGeometry.isPending ||
    decideNativeArea.isPending ||
    calculating ||
    statusUnknown ||
    checkingStatus ||
    cancelOperation.isPending ||
    preparationMutation.needsRecovery ||
    Boolean(reloadError);
  const mutationError =
    autoApplyRecognition.error ??
    saveMutation.error ??
    cancelOperation.error ??
    openEditor.error ??
    acceptPartialGeometry.error ??
    decideNativeArea.error;

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
      acceptPartialGeometry.reset();
      decideNativeArea.reset();
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
    startSourceEditing: () => setEditingSource(true),
    layerRecognition,
    allSavedLayersConfirmed,
    automaticAcceptancePending,
    layerRecognitionQuery,
    retryLayerRecognition,
    projectQuery,
    dataPassportQuery,
    layers,
    mappings,
    mappingsChanged,
    calculationPending: Boolean(projectQuery.data?.source_review),
    setMappings,
    sourceWarnings,
    nativeAreaProposals,
    decideNativeArea,
    sourceReadOnly,
    reviewOnly,
    cadPreview,
    sourceReviewMessage: projectQuery.data?.import_status?.message,
    readinessBlockedReason,
    readinessSection,
    partialGeometryPending,
    unconfirmedMappings,
    incompleteConstraintLayers,
    partialAccepted,
    acceptPartialGeometry,
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
