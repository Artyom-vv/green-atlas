/** Подготовка исходника: черновик назначений, подтверждение записи и переход к карте */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useForm, useWatch } from 'react-hook-form';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ApiClientError,
  isUnsentLocalRequest,
  type LayerMapping,
  type LayerRecognition,
  type Project,
  type ProjectOperation,
} from '@green/api-client';
import { preparationApi } from '../api/preparationApi';
import { mappingKey } from './preparationRecovery';
import { useMappingUndo } from './useMappingUndo';
import { mappingImpact } from './mappingImpact';
import { useProjectWrite } from '@/entities/project/model/useProjectWrite';
import { usePreparationCommit } from './usePreparationCommit';
import { usePreparationCompletion } from './usePreparationCompletion';
import { usePreparationOperationObservation } from './usePreparationOperationObservation';
import { needsPreparationPublication } from './preparationCompletion';
import { saveNativeAreaDecision } from './saveNativeAreaDecision';
import { partialGeometryAccepted } from '@/entities/source-data/model/partialGeometryAccepted';
import { autoAcceptRecognizedLayers } from './autoAcceptRecognizedLayers';
import { useLayerRecognition } from './useLayerRecognition';
import { useProjectWorkContribution } from '@/features/project-navigation/model/ProjectWorkContext';
import {
  acknowledgeProjectWrite,
  validateAcknowledgedProjectRead,
} from '@/entities/project/model/acknowledgedRead';
import { sourceWarningText } from '@/entities/source-data/model/sourceWarningText';
import { requiresLayerRoleReview } from '@/entities/source-data/model/layerKinds';
import { PREPARATION_MESSAGES } from '@/entities/source-data/config/sourceMessages';
import {
  EMPTY_LAYERS,
  sourceIdentity,
  layerMappings,
  operationActive,
  fromPreviousSource,
  latestPreparationOperation,
  canCalculatePreparedNative,
} from './sourcePreparation';

export interface SourceMappingForm {
  source: string;
  mappings: Record<string, LayerMapping>;
}
export interface SourcePreparationOptions {
  projectId: string;
  navigate: (to: string) => void;
  editingRequested?: boolean;
}

/** Терминальный ответ имеет собственную ревизию, независимую от версии проекта */
function recognitionResultKey(source: string, result?: LayerRecognition) {
  if (
    !result ||
    (result.status !== 'completed' &&
      !(result.status === 'failed' && result.completed_layer_ids?.length))
  )
    return undefined;
  return JSON.stringify([
    source,
    result.provider,
    result.status,
    result.status === 'failed' ? result.completed_layer_ids : null,
    result.started_at ?? null,
    result.finished_at ?? [
      result.categories
        .map(({ category, kind }) => [category, kind])
        .sort((left, right) => String(left[0]).localeCompare(String(right[0]))),
      result.proposals
        .map(({ layer_id, category, confidence }) => [
          layer_id,
          category,
          confidence,
        ])
        .sort((left, right) => String(left[0]).localeCompare(String(right[0]))),
    ],
  ]);
}

/** Предложения дополняют неизменённые назначения, сохраняя ручной черновик */
function recognizedMappings(
  layers: Project['layers'],
  saved: Record<string, LayerMapping>,
  draft: Record<string, LayerMapping>,
  recognition?: LayerRecognition,
  preview = false,
) {
  const accepted = autoAcceptRecognizedLayers(
    layers ?? [],
    saved,
    recognition,
    preview,
  );
  return Object.fromEntries(
    Object.entries(draft).map(([id, mapping]) => [
      id,
      mappingKey([mapping]) === mappingKey([saved[id]])
        ? (accepted[id] ?? mapping)
        : mapping,
    ]),
  );
}

export function useSourcePreparation({
  projectId,
  navigate,
  editingRequested = false,
}: SourcePreparationOptions) {
  const directAssignmentEditing = editingRequested;
  const queryClient = useQueryClient();
  const [reloadError, setReloadError] = useState<unknown>();
  const [editingSource, setEditingSource] = useState(false);
  const [cancelUnknown, setCancelUnknown] = useState(false);
  const [captureOpening, setCaptureOpening] = useState<{
    source: string;
    draftKey: string;
    completed: boolean;
    operationId?: string;
  }>();
  const cancelAttempt = useRef<{ id: string; basis: number } | undefined>(
    undefined,
  );
  const projectQuery = useQuery({
    queryKey: ['setup-project', projectId],
    queryFn: async ({ signal }) =>
      validateAcknowledgedProjectRead(
        queryClient,
        projectId,
        await preparationApi.getProjectRead(projectId, false, signal),
      ),
    enabled: Boolean(projectId),
    staleTime: 0,
    refetchOnMount: 'always',
    retry: false,
    networkMode: 'always',
  });
  const form = useForm<SourceMappingForm>({
    defaultValues: { source: '', mappings: {} },
    shouldUnregister: false,
  });
  const mappingDraft = useWatch({
    control: form.control,
    compute: (values: SourceMappingForm) => values,
  });
  const setMappingDraft = useCallback(
    (draft: SourceMappingForm) => {
      form.setValue('source', draft.source);
      form.setValue('mappings', draft.mappings, { shouldDirty: true });
    },
    [form],
  );
  const [trackedOperation, setTrackedOperation] = useState<{
    source: string;
    id?: string;
    stateVersion: number;
    kind: ProjectOperation['kind'];
    requestedHere?: boolean;
  }>();
  const layers = projectQuery.data?.layers ?? EMPTY_LAYERS;
  const allSavedLayersConfirmed =
    layers.length > 0 &&
    layers.every((layer) => layer.mapping_confirmed === true);
  const currentSource = sourceIdentity(projectQuery.data);
  const { layerRecognitionQuery, retryLayerRecognition, layerRecognition } =
    useLayerRecognition({
      projectId,
      source: currentSource,
      sourceSha: projectQuery.data?.source_file?.content_sha256,
      mode: directAssignmentEditing ? 'observe' : 'discover',
      enabled:
        Boolean(projectQuery.data?.layers?.length) && !allSavedLayersConfirmed,
    });
  const reviewOnly =
    projectQuery.data?.import_status?.editability === 'read_only';
  const cadPreview = projectQuery.data?.import_status?.mode === 'cad_preview';
  const operationId =
    !cadPreview && trackedOperation?.source === currentSource
      ? trackedOperation.id
      : undefined;
  const trackOperation = (operation: ProjectOperation, source: string) => {
    queryClient.setQueryData(['operation', projectId, operation.id], operation);
    setTrackedOperation({
      source,
      id: operation.id,
      stateVersion: operation.project_state_version,
      kind: operation.kind,
      requestedHere: true,
    });
    if (operation.kind === 'prepare_cad_capture')
      setCaptureOpening((intent) =>
        intent?.source === source
          ? { ...intent, operationId: operation.id }
          : intent,
      );
  };
  const publishProject = (fresh: Project) => {
    if ('prepared_state' in fresh)
      validateAcknowledgedProjectRead(queryClient, projectId, fresh);
    acknowledgeProjectWrite(queryClient, projectId, {
      stateVersion: fresh.state_version,
    });
    if ('prepared_state' in fresh)
      queryClient.setQueryData<Project>(
        ['setup-project', projectId],
        (current) =>
          !current || current.state_version <= fresh.state_version
            ? fresh
            : current,
      );
    else
      void queryClient.invalidateQueries({
        queryKey: ['setup-project', projectId],
      });
  };
  const sourceWrite = useProjectWrite(projectQuery.data, publishProject);
  const preparationMutation = usePreparationCommit({
    source: currentSource,
    onOperation: trackOperation,
    onProject: publishProject,
    onContinue: () => navigate(`/projects/${projectId}/workspace`),
  });
  const savedMappings = useMemo(() => layerMappings(layers), [layers]);
  const acceptedRecognitionResults = useRef(new Set<string>());
  const recognitionRevision = useMemo(
    () => recognitionResultKey(currentSource, layerRecognition),
    [currentSource, layerRecognition],
  );
  const autoApplyRecognition = useMutation({
    mutationFn: (request: { revision: string; mappings: LayerMapping[] }) =>
      preparationMutation.saveOnly(
        {
          projectId,
          source: currentSource,
          mappings: request.mappings,
          draftKey: mappingKey(request.mappings),
          baseStateVersion: projectQuery.data?.state_version ?? 0,
        },
        { confirmRead: true, recoverLostResponse: true },
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ['data-passport', projectId],
      });
    },
  });
  const autoApplyPending =
    autoApplyRecognition.isPending &&
    ['saving', 'reading', 'recovering'].includes(preparationMutation.phase);
  const autoApplyMutate = autoApplyRecognition.mutate;
  const automaticAcceptanceFailed =
    autoApplyRecognition.isError &&
    autoApplyRecognition.variables?.revision === recognitionRevision;
  const draftMappings =
    mappingDraft?.source === currentSource
      ? mappingDraft.mappings
      : savedMappings;
  const automaticallyMapped = useMemo(
    () =>
      recognizedMappings(
        layers,
        savedMappings,
        draftMappings,
        layerRecognition,
      ),
    [layers, savedMappings, draftMappings, layerRecognition],
  );
  const recognitionMappings = useMemo(
    () =>
      recognizedMappings(
        layers,
        savedMappings,
        draftMappings,
        layerRecognition,
        true,
      ),
    [layers, savedMappings, draftMappings, layerRecognition],
  );
  const automaticAcceptanceNeeded =
    mappingKey(Object.values(automaticallyMapped)) !==
    mappingKey(Object.values(draftMappings));
  const canApplyRecognition = Boolean(
    projectQuery.data &&
    recognitionRevision &&
    !directAssignmentEditing &&
    !reviewOnly &&
    !projectQuery.isFetching &&
    !projectQuery.isError &&
    !layerRecognitionQuery.isFetching &&
    !layerRecognitionQuery.isError &&
    !retryLayerRecognition.isPending &&
    !retryLayerRecognition.isError &&
    !autoApplyPending &&
    !automaticAcceptanceFailed &&
    !sourceWrite.blocked &&
    !preparationMutation.needsRecovery &&
    !acceptedRecognitionResults.current.has(recognitionRevision) &&
    !(
      projectQuery.data?.map_ready &&
      projectQuery.data.plan &&
      !projectQuery.data.source_review
    ),
  );
  const autoApplyQueued =
    canApplyRecognition &&
    automaticAcceptanceNeeded &&
    mappingKey(Object.values(draftMappings)) ===
      mappingKey(Object.values(savedMappings));
  const confirmedAutomaticProject = autoApplyRecognition.data;
  const automaticWriteAwaitingRead = Boolean(
    autoApplyRecognition.isSuccess &&
    confirmedAutomaticProject &&
    sourceIdentity(confirmedAutomaticProject) === currentSource &&
    projectQuery.data &&
    projectQuery.data.state_version < confirmedAutomaticProject.state_version,
  );
  // После подтверждённого PUT ждём только текущее чтение новой версии.
  // Ошибка чтения показывает восстановление, а не бесконечное принятие слоёв.
  const automaticAcceptancePending = Boolean(
    !projectQuery.isError &&
    !sourceWrite.readRecovery &&
    (autoApplyPending ||
      autoApplyQueued ||
      (projectQuery.isFetching && automaticWriteAwaitingRead)),
  );
  useEffect(() => {
    if (
      !canApplyRecognition ||
      !recognitionRevision ||
      !automaticAcceptanceNeeded
    )
      return;
    acceptedRecognitionResults.current.add(recognitionRevision);
    if (
      mappingKey(Object.values(draftMappings)) !==
      mappingKey(Object.values(savedMappings))
    ) {
      setMappingDraft({ source: currentSource, mappings: automaticallyMapped });
    } else {
      autoApplyMutate({
        revision: recognitionRevision,
        mappings: Object.values(automaticallyMapped),
      });
    }
  }, [
    canApplyRecognition,
    recognitionRevision,
    autoApplyMutate,
    automaticallyMapped,
    automaticAcceptanceNeeded,
    currentSource,
    draftMappings,
    savedMappings,
    setMappingDraft,
  ]);
  // Чтение того же исходника сохраняет черновик; новый исходник сразу получает
  // собственные назначения, не показывая черновик предыдущего проекта.
  const mappings = draftMappings;
  const mappingsChanged =
    mappingKey(Object.values(mappings)) !==
    mappingKey(Object.values(savedMappings));
  const physicalMappingsChanged = mappingImpact(
    Object.values(savedMappings),
    Object.values(mappings),
  ).physicalChanged;
  const localMapAvailable = ['available', 'not_required'].includes(
    projectQuery.data?.prepared_state ?? '',
  );
  const mappingUndo = useMappingUndo({
    source: currentSource,
    mappings,
    savedMappings,
    onChange: (next) =>
      setMappingDraft({ source: currentSource, mappings: next }),
  });
  const setMappings = (next: Record<string, LayerMapping>) => {
    setCaptureOpening(undefined);
    mappingUndo.change(next);
  };
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
    !editingSource &&
    (!directAssignmentEditing || reviewOnly),
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
  const liveBoundary =
    projectQuery.data?.import_status?.mode === 'autocad_live';
  const selectedBoundary = useMemo(
    () =>
      layers.some(
        (layer) =>
          mappings[layer.id]?.kind === 'site_border' &&
          (!liveBoundary ||
            !layer.boundary_candidate ||
            layer.boundary_candidate.status === 'usable'),
      ),
    [layers, mappings, liveBoundary],
  );
  const invalidBoundary = useMemo(
    () =>
      liveBoundary
        ? layers.find(
            (layer) =>
              mappings[layer.id]?.kind === 'site_border' &&
              layer.boundary_candidate &&
              layer.boundary_candidate.status !== 'usable',
          )
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
        return Boolean(mapping && requiresLayerRoleReview(mapping));
      }),
    [layers, mappings],
  );
  const readinessBlockedReason = invalidBoundary
    ? `Слой «${invalidBoundary.source_name}» не образует пригодную площадь. Выберите замкнутый контур в разделе «Геометрия»`
    : usableBoundaryCandidates.length > 0 && !selectedBoundary
      ? 'Выберите границу проектных работ в разделе «Геометрия»'
      : !requiredReady
        ? PREPARATION_MESSAGES.requiredBoundaryMapping
        : unconfirmedMappings.length > 0 &&
            !canCalculatePreparedNative(projectQuery.data, mappings)
          ? PREPARATION_MESSAGES.reviewMappings
          : undefined;
  const readinessSection =
    invalidBoundary ||
    (usableBoundaryCandidates.length > 0 && !selectedBoundary)
      ? '01'
      : !requiredReady || unconfirmedMappings.length > 0
        ? '03'
        : undefined;
  const partialGeometryPending =
    incompleteConstraintLayers.length > 0 && !partialAccepted;
  const hasPlanningBoundary = selectedBoundary;
  const latestOperationQuery = useQuery({
    queryKey: ['latest-operation', projectId, 'preparation', currentSource],
    queryFn: async ({ signal }) => {
      const [calculation, capture] = await Promise.all([
        preparationApi.getLatestOperation(
          projectId,
          'calculate_geometry',
          signal,
        ),
        projectQuery.data?.source_file?.native_session
          ? preparationApi.getLatestOperation(
              projectId,
              'prepare_cad_capture',
              signal,
            )
          : Promise.resolve(null),
      ]);
      return latestPreparationOperation(calculation, capture);
    },
    enabled: Boolean(
      projectId && projectQuery.data && !sourceReadOnly && !cadPreview,
    ),
    staleTime: 0,
    retry: false,
    networkMode: 'always',
  });
  const operationQuery = usePreparationOperationObservation({
    projectId,
    operationId,
    source: currentSource,
    expectedStateVersion: trackedOperation?.stateVersion,
    expectedKind: trackedOperation?.kind,
  });
  const latest =
    cadPreview || sourceReadOnly ? undefined : latestOperationQuery.data;
  const latestRecoverableOperation =
    latest &&
    (latest.status !== 'completed' ||
      (!mappingsChanged &&
        needsPreparationPublication(latest, projectQuery.data))) &&
    (!fromPreviousSource(latest, projectQuery.data) || operationActive(latest))
      ? latest
      : undefined;

  useEffect(() => {
    if (
      !operationId &&
      latestRecoverableOperation &&
      latestRecoverableOperation.status !== 'completed' &&
      latestOperationQuery.isFetchedAfterMount &&
      !latestOperationQuery.isFetching
    )
      setTrackedOperation({
        source: currentSource,
        id: latestRecoverableOperation.id,
        stateVersion: latestRecoverableOperation.project_state_version,
        kind: latestRecoverableOperation.kind,
      });
  }, [
    currentSource,
    latestRecoverableOperation,
    operationId,
    latestOperationQuery.isFetchedAfterMount,
    latestOperationQuery.isFetching,
  ]);

  // Свежая завершённая квитанция уже подтверждает статус. Её результат читается
  // напрямую: дополнительный запрос статуса не должен задерживать открытие карты.
  const recoveredCompletion =
    !operationId &&
    latestOperationQuery.isFetchedAfterMount &&
    !latestOperationQuery.isFetching &&
    latestRecoverableOperation?.status === 'completed'
      ? latestRecoverableOperation
      : undefined;
  const completion = usePreparationCompletion({
    projectId,
    project: projectQuery.data,
    operation: cadPreview
      ? undefined
      : (operationQuery.data ?? recoveredCompletion),
    writePending:
      preparationMutation.isPending || preparationMutation.needsRecovery,
    onProject: (fresh) => {
      const completed = operationQuery.data ?? recoveredCompletion;
      if (completed && cancelAttempt.current?.id !== completed.id) {
        const draft = form.getValues();
        preparationMutation.confirmCaptureRead(
          fresh,
          completed,
          mappingKey(
            Object.values(
              draft.source === currentSource ? draft.mappings : mappings,
            ),
          ),
        );
      }
      publishProject(fresh);
    },
    onCaptureRead: () => {
      setTrackedOperation(undefined);
      setCaptureOpening((intent) =>
        intent?.source === currentSource
          ? { ...intent, completed: true }
          : undefined,
      );
      void latestOperationQuery.refetch();
    },
    onOpenMap: () => {
      // Завершение может опередить отмену на сервере. Результат читаем,
      // но намерение пользователя остановиться не заменяем переходом.
      const completed = operationQuery.data ?? recoveredCompletion;
      const requestedHere =
        trackedOperation?.requestedHere &&
        trackedOperation.source === currentSource &&
        trackedOperation.id === completed?.id;
      if (
        cancelAttempt.current?.id !== completed?.id &&
        (!directAssignmentEditing || requestedHere)
      )
        navigate(`/projects/${projectId}/workspace`);
    },
  });

  const saveMutation = {
    ...preparationMutation,
    pendingLabel: completion.label ?? preparationMutation.pendingLabel,
    mutate: () => {
      if (
        cadPreview ||
        preparationBlocked ||
        (readinessBlockedReason &&
          (!mappingsChanged || physicalMappingsChanged)) ||
        !projectQuery.data
      )
        return;
      if (capturePreparationNeeded) {
        prepareCapture();
        return;
      }
      const values = Object.values(mappings);
      const start = (project: Project) => {
        const metadataOnly = mappingsChanged && !physicalMappingsChanged;
        const localContinuation = metadataOnly && project.map_ready;
        preparationMutation.mutate({
          projectId,
          source: currentSource,
          mappings: values,
          writeMappings: metadataOnly
            ? values.filter(
                (mapping) =>
                  mappingKey([mapping]) !==
                  mappingKey([savedMappings[mapping.layer_id]]),
              )
            : undefined,
          draftKey: mappingKey(values),
          baseStateVersion: project.state_version ?? 0,
          mode: localContinuation
            ? 'continue'
            : metadataOnly && (project.map_ready || readinessBlockedReason)
              ? 'save'
              : 'prepare',
        });
      };
      if (
        partialGeometryPending &&
        (!mappingsChanged || physicalMappingsChanged)
      ) {
        // This is the same explicit primary action the operator sees beside
        // the incompleteness notice. Keep the source-specific decision durable
        // before calculating, without a separate unlock button.
        void acceptPartialGeometry
          .mutateAsync()
          .then(start)
          .catch(() => undefined);
      } else start(projectQuery.data);
    },
  };
  const capturePreparationNeeded = Boolean(
    !cadPreview &&
    !reviewOnly &&
    !localMapAvailable &&
    projectQuery.data?.source_file?.native_session &&
    (!projectQuery.data.map_ready ||
      projectQuery.data.source_review ||
      physicalMappingsChanged) &&
    (readinessBlockedReason ||
      layers.some(
        (layer) =>
          layer.mapping_review_required &&
          mappings[layer.id]?.confirmed === false,
      )),
  );
  const prepareCapture = () => {
    if (preparationBlocked || !capturePreparationNeeded || !projectQuery.data)
      return;
    const values = Object.values(mappings);
    setCaptureOpening({
      source: currentSource,
      draftKey: mappingKey(values),
      completed: false,
    });
    preparationMutation.mutate({
      projectId,
      source: currentSource,
      mappings: values,
      draftKey: mappingKey(values),
      baseStateVersion: projectQuery.data.state_version ?? 0,
      mode: 'capture',
    });
  };
  const continueCapture = useRef(saveMutation.mutate);
  useEffect(() => {
    continueCapture.current = saveMutation.mutate;
  });
  const cancelOperation = useMutation({
    networkMode: 'always',
    retry: false,
    mutationFn: (id: string) => {
      setCaptureOpening(undefined);
      if (cancelUnknown) throw new Error('Сначала проверьте результат отмены.');
      cancelAttempt.current = {
        id,
        basis:
          operationQuery.data?.project_state_version ??
          latest?.project_state_version ??
          projectQuery.data?.state_version ??
          0,
      };
      return preparationApi.cancelOperation(projectId, id);
    },
    onSuccess: (next) => {
      setCancelUnknown(false);
      queryClient.setQueryData(['operation', projectId, next.id], next);
    },
    onError: (error) => {
      if (!(
        isUnsentLocalRequest(error) ||
        (error instanceof ApiClientError &&
          ['BAD_REQUEST', 'NOT_FOUND', 'PROJECT_VERSION_CONFLICT'].includes(
            error.code,
          ))
      ))
        setCancelUnknown(true);
    },
  });
  const recoverCancel = async () => {
    const accepted = cancelAttempt.current;
    if (!accepted) return;
    const fresh = await preparationApi.getOperation(projectId, accepted.id);
    if (
      fresh.id !== accepted.id ||
      fresh.project_id !== projectId ||
      fresh.project_state_version !== accepted.basis ||
      (operationActive(fresh) && !fresh.cancel_requested_at)
    )
      throw new Error(
        'Отмена ещё не подтверждена. Запрос повторно не отправляется.',
      );
    queryClient.setQueryData(['operation', projectId, fresh.id], fresh);
    setCancelUnknown(false);
    cancelOperation.reset();
  };
  const openEditor = useMutation({
    mutationFn: async () => {
      setCaptureOpening(undefined);
      if (mappingsChanged)
        await preparationMutation.saveOnly({
          projectId,
          source: currentSource,
          mappings: Object.values(mappings),
          draftKey: mappingKey(Object.values(mappings)),
          baseStateVersion: projectQuery.data?.state_version ?? 0,
        });
      return sourceWrite.execute({
        send: () => preparationApi.openSourceEditor(projectId),
        matches: (fresh) =>
          fresh.map_ready &&
          Boolean(
            fresh.source_review ||
            fresh.geometry_version > (projectQuery.data?.geometry_version ?? 0),
          ),
      });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ['workspace-project', projectId],
      });
      void queryClient.invalidateQueries({
        queryKey: ['data-passport', projectId],
      });
      navigate(`/projects/${projectId}/workspace`);
    },
  });
  const acceptPartialGeometry = useMutation({
    mutationFn: async () => {
      setCaptureOpening(undefined);
      const project = projectQuery.data;
      const sha = project?.source_file?.content_sha256;
      if (!project || !sha) throw new Error('Исходник ещё не загружен');
      return sourceWrite.execute({
        send: () =>
          preparationApi.acceptPartialGeometry(projectId, sha, {
            expectedStateVersion: project.state_version ?? 0,
          }),
        matches: (fresh) => fresh.source_file?.accept_partial_geometry === true,
      });
    },
    onSuccess: () => {
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
      setCaptureOpening(undefined);
      const project = projectQuery.data;
      const sourceSha = project?.source_file?.content_sha256;
      if (!project || !sourceSha) throw new Error('Снимок AutoCAD недоступен');
      return sourceWrite.execute({
        send: () =>
          saveNativeAreaDecision(
            projectId,
            {
              source_sha256: sourceSha,
              proposal_id: input.proposalId,
              proposal_sha256: input.proposalSha256,
              decision: input.decision,
            },
            project.state_version ?? 0,
          ),
        matches: (fresh) =>
          fresh.source_file?.native_area_proposals?.some(
            (proposal) =>
              proposal.id === input.proposalId &&
              proposal.proposal_sha256 === input.proposalSha256 &&
              proposal.decision === input.decision,
          ) === true,
      });
    },
    onSuccess: () => {
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
    (!operationId || latestRecoverableOperation?.id === operationId
      ? latestRecoverableOperation
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
    !cadPreview &&
    !sourceReadOnly &&
    statusQuery.isPending &&
    !lastKnownOperation;
  const calculating =
    saveMutation.isPending ||
    acceptPartialGeometry.isPending ||
    completion.reading ||
    Boolean(operationId && operationQuery.isLoading) ||
    operationActive(operation);
  const preparationBlocked =
    projectQuery.isFetching ||
    projectQuery.isError ||
    sourceWrite.blocked ||
    cancelUnknown ||
    autoApplyPending ||
    openEditor.isPending ||
    acceptPartialGeometry.isPending ||
    decideNativeArea.isPending ||
    calculating ||
    statusUnknown ||
    checkingStatus ||
    cancelOperation.isPending ||
    preparationMutation.needsRecovery ||
    completion.failed ||
    Boolean(reloadError);
  // Кнопка выражает намерение открыть карту. После физической подготовки
  // продолжаем тот же сценарий, если сохранённые решения уже допускают расчёт.
  useEffect(() => {
    if (!captureOpening) return;
    if (
      captureOpening.source !== currentSource ||
      (captureOpening.operationId === operation?.id &&
        ['failed', 'cancelled', 'interrupted'].includes(
          operation?.status ?? '',
        )) ||
      preparationMutation.phase === 'failed'
    ) {
      setCaptureOpening(undefined);
      return;
    }
    if (!captureOpening.completed || preparationBlocked) return;
    setCaptureOpening(undefined);
    if (
      !readinessBlockedReason &&
      mappingKey(Object.values(mappings)) === captureOpening.draftKey
    )
      continueCapture.current();
  }, [
    captureOpening,
    currentSource,
    mappings,
    operation?.status,
    operation?.id,
    preparationBlocked,
    preparationMutation.phase,
    readinessBlockedReason,
  ]);
  const mutationError =
    (saveMutation.needsRecovery ? undefined : saveMutation.error) ??
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

  const preparationRecovery =
    completion.recovery ??
    (cancelUnknown
      ? {
          message:
            'Результат отмены не подтверждён. Проверка читает состояние подготовки; отмена повторно не отправляется.',
          loading: false,
          onRetry: () => {
            void recoverCancel().catch(() => undefined);
          },
        }
      : sourceWrite.readRecovery
        ? {
            message:
              sourceWrite.writeOutcome === 'confirmed'
                ? 'Решение сохранено. Прочитайте актуальное состояние перед редактированием; можно выйти к проектам.'
                : 'Результат сохранения решения не подтверждён. Проверка читает проект; запись повторно не отправляется.',
            loading: sourceWrite.writeOutcome === 'writing',
            onRetry: () => {
              void sourceWrite.recover();
            },
          }
        : projectQuery.isError
          ? {
              message: automaticWriteAwaitingRead
                ? PREPARATION_MESSAGES.mappingsReadRecovery
                : PREPARATION_MESSAGES.projectReadRecovery,
              loading: projectQuery.isFetching,
              onRetry: () => {
                void projectQuery.refetch();
              },
            }
          : reloadError
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
              : undefined);

  const continuingCapture = Boolean(
    captureOpening?.completed &&
    captureOpening.source === currentSource &&
    !readinessBlockedReason &&
    !preparationRecovery,
  );
  const hasUnappliedMappings =
    mappingsChanged &&
    preparationMutation.acknowledgedDraftKey !==
      mappingKey(Object.values(mappings));
  useProjectWorkContribution('source-cancel', {
    writeOutcome: cancelOperation.isPending
      ? 'writing'
      : cancelUnknown
        ? 'unknown'
        : 'idle',
    readRecovery: cancelUnknown,
  });
  useProjectWorkContribution('source-decisions', {
    writeOutcome: sourceWrite.writeOutcome,
    readRecovery: sourceWrite.readRecovery,
    editingBlocked: sourceWrite.blocked,
  });
  useProjectWorkContribution('source-mappings', {
    hasDraft: hasUnappliedMappings,
    writeOutcome: preparationMutation.writeOutcome,
    readRecovery: preparationMutation.readRecovery,
    editingBlocked: preparationBlocked,
    preserveOperation: operationActive(operation),
  });

  return {
    form,
    startSourceEditing: () => {
      setCaptureOpening(undefined);
      setEditingSource(true);
    },
    layerRecognition,
    allSavedLayersConfirmed,
    automaticAcceptancePending,
    autoApplyingRecognition: autoApplyPending || autoApplyQueued,
    recognitionMappings:
      mappingKey(Object.values(recognitionMappings)) !==
      mappingKey(Object.values(mappings))
        ? recognitionMappings
        : undefined,
    recognitionSaveRecovery:
      (autoApplyRecognition.isError || autoApplyRecognition.isPending) &&
      preparationMutation.needsRecovery
        ? {
            loading: preparationMutation.phase === 'recovering',
            onRetry: () => void preparationMutation.recover(),
          }
        : undefined,
    layerRecognitionQuery,
    retryLayerRecognition,
    projectQuery,
    projectNotFound:
      projectQuery.error instanceof ApiClientError &&
      projectQuery.error.code === 'NOT_FOUND',
    layers,
    mappings,
    mappingsChanged,
    physicalMappingsChanged,
    localMapAvailable,
    calculationPending: Boolean(projectQuery.data?.source_review),
    setMappings,
    canUndoMappings: mappingUndo.canUndo,
    undoMappings: () => {
      setCaptureOpening(undefined);
      mappingUndo.undo();
    },
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
    calculating: calculating || continuingCapture,
    preparationBlocked: preparationBlocked || continuingCapture,
    statusQuery,
    saveMutation,
    capturePreparationNeeded,
    prepareCapture,
    openEditor,
    cancelOperation,
    mutationError,
    preparationRecovery,
    reloadAfterConflict,
    downloadSource: () => preparationApi.sourceDownloadUrl(projectId),
  };
}
