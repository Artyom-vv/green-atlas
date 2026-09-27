import { repeatedItemLabel } from '@/entities/planting-zone/model/plantingZoneLabels';
import { zoneChangeSummary } from '@/entities/planting-zone/model/zoneChangeSummary';
import { assistantProjectQuery } from '@/features/assistant/api/queries';
import { rememberRun, storageKey } from '@/features/assistant/api/runStorage';
import type { AgentMapControl } from '@/features/assistant/model/autonomous/autonomousControl';
import { autonomousRunLifecycle } from '@/features/assistant/model/autonomous/autonomousRunLifecycle';
import {
  acceptedSelection,
  selectionLabel,
  selectionRemedies,
  snapshotSelection,
} from '@/features/assistant/model/autonomous/autonomousSelection';
import { committedZoneChange } from '@/features/assistant/model/autonomous/committedZoneChange';
import {
  existingChangeResult,
  existingChangeSummary,
} from '@/features/assistant/model/autonomous/existingChangeSummary';
import {
  arrangementLabels,
  attemptEvents,
  capacity,
  errorText,
  latestShortlist,
  placementData,
  record,
  Recovery,
  RestoreFailure,
  savedIssues,
  shortlistNames,
} from '@/features/assistant/model/autonomous/presentation';
import { useAutonomousControl } from '@/features/assistant/model/autonomous/useAutonomousControl';
import {
  useAutonomousPreview,
  type AutonomousPreview,
} from '@/features/assistant/model/autonomous/useAutonomousPreview';
import { useAssistantDraft } from '@/features/assistant/model/useAssistantDraft';
import {
  api,
  ApiClientError,
  type AgentRun,
  type AgentSelectionContext,
  type Project,
} from '@green/api-client';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
export interface AutonomousRunOptions {
  projectId: string;
  onPreviewChange?: (value: AutonomousPreview | undefined) => void;
  selectionContext?: AgentSelectionContext;
  onMapControl?: AgentMapControl;
}
export function useAutonomousRunController({
  projectId,
  onPreviewChange,
  selectionContext,
  onMapControl,
}: AutonomousRunOptions) {
  const queryClient = useQueryClient();
  const { data: project } = useQuery(assistantProjectQuery(projectId));
  const { form: draftForm, draft, setDraft } = useAssistantDraft();
  const [storedRun, setRun] = useState<AgentRun>();
  const [error, setError] = useState<string>();
  const [recovery, setRecovery] = useState<Recovery>();
  const [busy, setBusy] = useState(false);
  const [creatingRun, setCreating] = useState(false);
  const [submission, setSubmission] = useState<{
    projectId: string;
    selection?: AgentSelectionContext;
    label?: string;
  }>();
  const [stopping, setStopping] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [restoreFailure, setRestoreFailure] = useState<RestoreFailure>();
  const [historyOpen, setHistoryOpen] = useState(false);
  const runs = useQuery({
    queryKey: ['agent-runs', projectId],
    queryFn: () => api.listAgentRuns(projectId),
    enabled: historyOpen,
    retry: false,
  });
  const historyRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const requestRef = useRef(0);
  const submitted =
    submission?.projectId === projectId ? submission : undefined;
  const creating = creatingRun && Boolean(submitted);
  const currentSelection =
    selectionContext?.project_id === projectId ? selectionContext : undefined;
  const availableSelectionLabel = selectionLabel(currentSelection, project);
  const run =
    !creating && storedRun?.state.project_id === projectId
      ? storedRun
      : undefined;
  const runId = run?.state.run_id;
  const failedRestore =
    restoreFailure?.projectId === projectId ? restoreFailure : undefined;
  const status = run?.state.status;
  const recoveryPending =
    recovery?.projectId === projectId && recovery.runId === runId;
  const lifecycle = autonomousRunLifecycle(run, recoveryPending);
  const outcomeUnknown =
    run?.state.failure?.code === 'APPROVAL_OUTCOME_UNKNOWN';
  const retryBlocked = !lifecycle.canRetry;
  const acceptedSelectionLabel = selectionLabel(
    acceptedSelection(run),
    project,
  );
  const remediesForSelection = selectionRemedies(currentSelection);
  const events = useMemo(
    () =>
      attemptEvents(run).filter((event) =>
        [
          'run_started',
          'run_restarted',
          'run_resumed',
          'question_answered',
          'run_cancelled',
          'tool_result',
          'question',
          'approval_requested',
          'commit_applied',
          'run_failed',
          'scope_fallback_started',
        ].includes(event.kind),
      ),
    [run],
  );
  const data = placementData(run);
  const result = capacity(data);
  const zoneRequested = run?.state.pending_approval?.kind === 'planting_zones';
  const existing = zoneRequested ? undefined : existingChangeSummary(run);
  const existingResult = existingChangeResult(run);
  const existingExpected =
    !zoneRequested &&
    Boolean(
      existingResult ||
      ['edit', 'delete'].includes(
        String(record(run?.state.intent.goal)?.operation),
      ),
    );
  const existingUnverified = Boolean(
    run?.state.status === 'waiting_approval' && existingExpected && !existing,
  );
  const issues = savedIssues(run);
  const shortlist = latestShortlist(run);
  const proposal = record(data?.proposal);
  const selectedSpecies =
    data?.species_revision_ids ??
    proposal?.species_revision_ids ??
    (typeof proposal?.species_revision_id === 'string'
      ? [proposal.species_revision_id]
      : []);
  const speciesIds = Array.isArray(selectedSpecies)
    ? [
        ...new Set(
          selectedSpecies.filter((id): id is string => typeof id === 'string'),
        ),
      ]
    : [];
  const catalog = useQuery({
    queryKey: ['species'],
    queryFn: () => api.listSpecies(),
    enabled: speciesIds.length > 0 || Boolean(existing?.speciesIds.length),
    staleTime: Number.POSITIVE_INFINITY,
    retry: false,
  });
  const confirmedNames = shortlistNames(run);
  const speciesNames = speciesIds.map(
    (id) =>
      catalog.data?.find((species) => species.id === id)?.common_name ??
      confirmedNames.get(id),
  );
  const arrangement = data?.arrangement ?? proposal?.arrangement;
  const arrangementLabel =
    typeof arrangement === 'string'
      ? arrangementLabels[arrangement]
      : undefined;
  const existingSpecies = existing?.speciesIds.map(
    (id) =>
      catalog.data?.find((species) => species.id === id)?.common_name ??
      confirmedNames.get(id) ??
      'Название породы недоступно',
  );
  const previousSpecies = existing?.previousSpeciesIds.map(
    (id) =>
      catalog.data?.find((species) => species.id === id)?.common_name ??
      confirmedNames.get(id) ??
      'Название породы недоступно',
  );
  const existingScope = existing?.zoneIds
    .map((id) => project?.planting_zones?.find((zone) => zone.id === id))
    .map((zone) =>
      zone
        ? repeatedItemLabel(project?.planting_zones ?? [], zone)
        : 'Название участка недоступно',
    );
  const lastPlacement = [...attemptEvents(run)]
    .reverse()
    .find(
      (event) =>
        event.kind === 'tool_result' &&
        event.payload.name === 'prepare_placement',
    );
  const incomplete = Boolean(
    (result && result.status !== 'exact') ||
    ['partial', 'impossible'].includes(
      String(record(data?.placement_outcome)?.status),
    ) ||
    run?.state.last_result?.status === 'partial' ||
    lastPlacement?.payload.status === 'partial',
  );
  const active = lifecycle.active;
  const snapshotStale = Boolean(
    run?.state.status === 'waiting_approval' &&
    project &&
    (project.state_version !== run.state.snapshot_version ||
      (run.state.plan_version != null &&
        project.plan?.version !== run.state.plan_version)),
  );
  const previewState = useAutonomousPreview(
    incomplete ||
      snapshotStale ||
      stopping ||
      existingUnverified ||
      recoveryPending
      ? undefined
      : run,
    projectId,
    onPreviewChange,
  );
  const zonePreview = previewState.zonePreview;
  const zoneChange = zonePreview ? zoneChangeSummary(zonePreview) : undefined;
  const stale =
    snapshotStale ||
    Boolean(
      zonePreview &&
      project &&
      project.geometry_version !== zonePreview.base_geometry_version,
    );
  const zoneBlocked = Boolean(zoneChange && !zoneChange.canApply);
  const approval =
    lifecycle.canApprove &&
    !incomplete &&
    !stale &&
    !existingUnverified &&
    !zoneBlocked
      ? run?.state.pending_approval
      : undefined;
  const previewUnavailable = Boolean(
    previewState.error ||
    ((onPreviewChange || zoneRequested) &&
      (!previewState.preview || previewState.loading)),
  );
  const reviewStatus =
    status === 'waiting_approval'
      ? stale
        ? 'Предложение устарело'
        : existingUnverified || zoneBlocked || previewState.error
          ? 'Предложение недоступно'
          : undefined
      : undefined;
  const reviewNotice = reviewStatus
    ? stale
      ? 'Это результат предыдущего расчёта. Перед применением нужен новый расчёт.'
      : 'Результат расчёта сохранён, но предложение недоступно для подтверждения.'
    : undefined;
  const committed = events.some((event) => event.kind === 'commit_applied');
  const zoneCommitted = events.some(
    (event) =>
      event.kind === 'commit_applied' &&
      event.payload.kind === 'planting_zones',
  );
  const appliedZoneChange = committedZoneChange(run);
  const answering = lifecycle.question;
  const staleSelection =
    answering &&
    run?.state.pending_question?.slot === 'selection' &&
    record(run.state.intent.selection_issue)?.code === 'SELECTION_STALE';
  const selectionRecovery = useQuery({
    queryKey: [
      'agent-selection-recovery',
      projectId,
      runId,
      staleSelection ? run?.revision : null,
    ],
    queryFn: async () => {
      const current = await api.getProject(projectId, false);
      if (current.id !== projectId)
        throw new Error('Не удалось обновить данные текущего проекта.');
      // Refresh versions only; workspace selection and the saved run snapshot keep their own IDs.
      const accepted = queryClient.setQueryData<Project>(
        ['workspace-project', projectId],
        (cached) =>
          cached?.id === projectId &&
          cached.state_version > current.state_version
            ? cached
            : current,
      );
      return accepted?.state_version ?? current.state_version;
    },
    enabled: Boolean(staleSelection),
    retry: false,
    staleTime: Number.POSITIVE_INFINITY,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    refetchOnMount: false,
  });
  const selectionRefreshBlocked = Boolean(
    staleSelection &&
    (selectionRecovery.isPending ||
      selectionRecovery.isError ||
      (currentSelection &&
        selectionRecovery.data !== undefined &&
        currentSelection.state_version < selectionRecovery.data)),
  );
  const decisionDisabled = busy || stopping || restoring;
  const inputDisabled =
    decisionDisabled ||
    !lifecycle.canCompose ||
    Boolean(failedRestore) ||
    selectionRefreshBlocked;
  const canCancel = lifecycle.canCancel;
  const kinds = record(data?.kind_counts);
  const zoneIds =
    data?.resolved_zone_ids ?? run?.state.resolved_scope?.zone_ids;
  const zones = Array.isArray(zoneIds)
    ? zoneIds.filter((id): id is string => typeof id === 'string')
    : [];
  const scopeLabels = zones
    .map((id) => project?.planting_zones?.find((zone) => zone.id === id))
    .flatMap((zone) =>
      zone ? [repeatedItemLabel(project?.planting_zones ?? [], zone)] : [],
    );
  const failureRemedy =
    typeof run?.state.failure?.remedy === 'string'
      ? run.state.failure.remedy.trim()
      : undefined;
  const accept = (next: AgentRun, token: number) => {
    if (token !== requestRef.current || next.state.project_id !== projectId)
      return false;
    setRun((current) =>
      current?.state.run_id === next.state.run_id &&
      current.revision > next.revision
        ? current
        : next,
    );
    rememberRun(projectId, next.state.run_id);
    void queryClient.invalidateQueries({ queryKey: ['agent-runs', projectId] });
    return true;
  };
  const mapControl = useAutonomousControl(
    run,
    !busy && !stopping && !recoveryPending && !restoring,
    onMapControl,
    (next) => accept(next, requestRef.current),
  );
  const controlCommand = run?.state.control_command;
  const controlReceipt = run?.state.control_result;
  const shownZone =
    run?.state.status === 'finished' &&
    controlCommand &&
    controlReceipt?.status === 'completed' &&
    controlCommand.project_id === projectId &&
    controlCommand.run_id === run.state.run_id &&
    controlCommand.execution_attempt_id === run.state.execution_attempt_id &&
    controlReceipt.command_id === controlCommand.id &&
    controlReceipt.project_id === projectId &&
    controlReceipt.run_id === controlCommand.run_id &&
    controlReceipt.execution_attempt_id ===
      controlCommand.execution_attempt_id &&
    controlReceipt.zone_id === controlCommand.zone_id &&
    controlReceipt.geometry_version === controlCommand.geometry_version &&
    controlReceipt.geometry_digest === controlCommand.geometry_digest
      ? controlCommand.zone_label
      : undefined;
  const restoreSavedRun = useCallback(
    async (savedId: string, token: number) => {
      try {
        const next = await api.getAgentRun(projectId, savedId);
        if (token !== requestRef.current) return;
        if (
          next.state.project_id !== projectId ||
          next.state.run_id !== savedId
        )
          throw new Error(
            'Не удалось подтвердить сохранённый запуск. Повторите загрузку.',
          );
        setRun((current) =>
          current?.state.run_id === next.state.run_id &&
          current.revision > next.revision
            ? current
            : next,
        );
        setRestoreFailure(undefined);
      } catch (cause) {
        if (token !== requestRef.current) return;
        if (cause instanceof ApiClientError && cause.code === 'NOT_FOUND') {
          rememberRun(projectId, null);
          setRestoreFailure(undefined);
        } else
          setRestoreFailure({
            projectId,
            runId: savedId,
            message: errorText(cause),
          });
      } finally {
        if (token === requestRef.current) setRestoring(false);
      }
    },
    [projectId],
  );
  useEffect(() => {
    const lifecycle = requestRef;
    const token = ++lifecycle.current;
    setRun(undefined);
    setDraft('');
    setError(undefined);
    setRecovery(undefined);
    setRestoreFailure(undefined);
    setBusy(false);
    setCreating(false);
    setSubmission(undefined);
    setStopping(false);
    setHistoryOpen(false);
    let saved: string | null = null;
    try {
      saved = window.sessionStorage.getItem(storageKey(projectId));
    } catch {
      /* Storage may be disabled. */
    }
    setRestoring(Boolean(saved));
    if (saved) void restoreSavedRun(saved, token);
    return () => {
      ++lifecycle.current;
    };
  }, [projectId, restoreSavedRun, setDraft]);
  const retryRestore = () => {
    if (!failedRestore || decisionDisabled) return;
    const token = ++requestRef.current;
    setRestoring(true);
    setError(undefined);
    void restoreSavedRun(failedRestore.runId, token);
  };
  useEffect(() => {
    if (!runId || !lifecycle.poll || busy || stopping || restoring)
      return undefined;
    let disposed = false;
    let pending = false;
    const token = requestRef.current;
    const refresh = async () => {
      if (pending) return;
      pending = true;
      try {
        const next = await api.getAgentRun(projectId, runId);
        if (
          !disposed &&
          token === requestRef.current &&
          next.state.project_id === projectId
        ) {
          setRun((current) =>
            current?.state.run_id === next.state.run_id &&
            current.revision > next.revision
              ? current
              : next,
          );
          setError(undefined);
        }
      } catch (cause) {
        if (!disposed && token === requestRef.current)
          setError(errorText(cause));
      } finally {
        pending = false;
      }
    };
    const timer = window.setInterval(() => void refresh(), 1200);
    void refresh();
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [projectId, runId, busy, stopping, restoring, lifecycle.poll]);
  useEffect(() => {
    if (historyRef.current)
      historyRef.current.scrollTop =
        lifecycle.executing || creating ? historyRef.current.scrollHeight : 0;
  }, [events.length, lifecycle.executing, runId, creating]);
  const fillDraft = (text: string) => {
    setDraft(text);
    inputRef.current?.focus();
  };
  const recover = async (pending: Recovery, token: number) => {
    if (token !== requestRef.current) return;
    setRecovery(pending);
    // Read the authoritative checkpoint before offering another action after a lost response.
    const [checkpoint, freshProject] = await Promise.allSettled([
      api.getAgentRun(projectId, pending.runId),
      api.getProject(projectId, false),
    ]);
    if (token !== requestRef.current) return;
    const next =
      checkpoint.status === 'fulfilled' &&
      checkpoint.value.state.project_id === projectId &&
      checkpoint.value.state.run_id === pending.runId &&
      checkpoint.value.revision >= pending.revision
        ? checkpoint.value
        : undefined;
    const current =
      freshProject.status === 'fulfilled' && freshProject.value.id === projectId
        ? freshProject.value
        : undefined;
    if (next) {
      accept(next, token);
      if (
        pending.action === 'answer' &&
        next.revision > pending.revision &&
        next.state.status !== 'waiting_question'
      )
        setDraft('');
    }
    if (current) {
      queryClient.setQueryData<Project>(
        ['workspace-project', projectId],
        (cached) =>
          cached?.id === projectId &&
          cached.state_version > current.state_version
            ? cached
            : current,
      );
      void Promise.all(
        ['setup-project', 'plan-history', 'map-features', 'data-passport'].map(
          (key) =>
            queryClient.invalidateQueries({ queryKey: [key, projectId] }),
        ),
      );
    }
    if (next && current) {
      const outcomeConfirmed =
        (next.state.status === 'finished' &&
          next.events.some((event) => event.kind === 'commit_applied')) ||
        next.state.failure?.code === 'APPROVAL_OUTCOME_UNKNOWN';
      setRecovery(undefined);
      setError(
        next.revision > pending.revision || outcomeConfirmed
          ? undefined
          : pending.errorMessage,
      );
    } else
      setError(
        'Не удалось обновить запуск и проект. Обновите состояние, чтобы проверить результат запроса.',
      );
  };
  const refreshOutcome = async () => {
    if (!run || decisionDisabled) return;
    const token = ++requestRef.current;
    setBusy(true);
    setError(undefined);
    try {
      await recover(
        recovery ?? {
          projectId,
          runId: run.state.run_id,
          action: 'approve',
          revision: run.revision,
        },
        token,
      );
    } finally {
      if (token === requestRef.current) setBusy(false);
    }
  };
  const submit = async () => {
    const text = draft.trim();
    if (text.length < (answering ? 1 : 5) || inputDisabled) return;
    const token = ++requestRef.current;
    const selection = snapshotSelection(projectId, currentSelection);
    setSubmission({
      projectId,
      selection,
      label: selectionLabel(selection, project),
    });
    setBusy(true);
    setCreating(!answering);
    setError(undefined);
    let pending: Recovery | undefined =
      answering && run
        ? {
            projectId,
            runId: run.state.run_id,
            action: 'answer',
            revision: run.revision,
          }
        : undefined;
    try {
      const created =
        answering && run
          ? await (selection
              ? api.answerAgentRun(projectId, run.state.run_id, text, selection)
              : api.answerAgentRun(projectId, run.state.run_id, text))
          : await (selection
              ? api.createAgentRun(
                  projectId,
                  text,
                  undefined,
                  undefined,
                  selection,
                )
              : api.createAgentRun(projectId, text));
      if (!accept(created, token)) return;
      setCreating(false);
      setDraft('');
      if (!autonomousRunLifecycle(created, false).canContinue) return;
      pending = {
        projectId,
        runId: created.state.run_id,
        action: 'execute',
        revision: created.revision,
      };
      const next = await api.runAgentRun(projectId, created.state.run_id);
      accept(next, token);
    } catch (cause) {
      if (token === requestRef.current) {
        if (pending)
          await recover({ ...pending, errorMessage: errorText(cause) }, token);
        else setError(errorText(cause));
      }
    } finally {
      if (token === requestRef.current) {
        setCreating(false);
        setSubmission(undefined);
        setBusy(false);
      }
    }
  };
  const approve = async () => {
    if (
      !run ||
      !approval ||
      decisionDisabled ||
      incomplete ||
      stale ||
      previewUnavailable
    )
      return;
    const token = ++requestRef.current;
    setBusy(true);
    setError(undefined);
    try {
      const next = await api.approveAgentRun(
        projectId,
        run.state.run_id,
        approval.preview_ref,
      );
      if (!accept(next, token)) return;
      await Promise.all(
        [
          'workspace-project',
          'setup-project',
          'plan-history',
          'map-features',
          'data-passport',
        ].map((key) =>
          queryClient.invalidateQueries({ queryKey: [key, projectId] }),
        ),
      );
    } catch (cause) {
      if (token === requestRef.current) {
        setError(errorText(cause));
        await recover(
          {
            projectId,
            runId: run.state.run_id,
            action: 'approve',
            revision: run.revision,
            errorMessage: errorText(cause),
          },
          token,
        );
      }
    } finally {
      if (token === requestRef.current) setBusy(false);
    }
  };
  const retry = async () => {
    if (!run || !lifecycle.canRetry || decisionDisabled) return;
    const token = ++requestRef.current;
    setBusy(true);
    setError(undefined);
    let pending: Recovery = {
      projectId,
      runId: run.state.run_id,
      action: run.state.status === 'waiting_approval' ? 'cancel' : 'resume',
      revision: run.revision,
    };
    try {
      if (run.state.status === 'waiting_approval') {
        const cancelled = await api.cancelAgentRun(projectId, run.state.run_id);
        if (!accept(cancelled, token)) return;
        if (!autonomousRunLifecycle(cancelled, false).canRetry) return;
        pending = {
          ...pending,
          action: 'resume',
          revision: cancelled.revision,
        };
      }
      const queued = await api.resumeAgentRun(projectId, run.state.run_id);
      if (!accept(queued, token)) return;
      if (!autonomousRunLifecycle(queued, false).canContinue) return;
      pending = { ...pending, action: 'execute', revision: queued.revision };
      accept(await api.runAgentRun(projectId, run.state.run_id), token);
    } catch (cause) {
      await recover({ ...pending, errorMessage: errorText(cause) }, token);
    } finally {
      if (token === requestRef.current) setBusy(false);
    }
  };
  const continueRun = async () => {
    if (!run || !lifecycle.canContinue || decisionDisabled) return;
    const token = ++requestRef.current;
    setBusy(true);
    setError(undefined);
    try {
      accept(await api.runAgentRun(projectId, run.state.run_id), token);
    } catch (cause) {
      await recover(
        {
          projectId,
          runId: run.state.run_id,
          action: 'execute',
          revision: run.revision,
          errorMessage: errorText(cause),
        },
        token,
      );
    } finally {
      if (token === requestRef.current) setBusy(false);
    }
  };
  const cancel = async () => {
    if (!run || !canCancel || restoring || stopping || (busy && !active))
      return;
    mapControl.cancel();
    // Supersede an in-flight /run response; stopping the server is a separate request.
    const token = ++requestRef.current;
    setStopping(true);
    setError(undefined);
    try {
      accept(await api.cancelAgentRun(projectId, run.state.run_id), token);
    } catch (cause) {
      await recover(
        {
          projectId,
          runId: run.state.run_id,
          action: 'cancel',
          revision: run.revision,
          errorMessage: errorText(cause),
        },
        token,
      );
    } finally {
      if (token === requestRef.current) {
        setStopping(false);
        setBusy(false);
      }
    }
  };
  const openRun = async (selected: AgentRun) => {
    if (decisionDisabled || selected.state.project_id !== projectId) return;
    const token = ++requestRef.current;
    setRestoring(true);
    setError(undefined);
    try {
      if (
        accept(await api.getAgentRun(projectId, selected.state.run_id), token)
      ) {
        setDraft('');
        setHistoryOpen(false);
        setRecovery(undefined);
        setRestoreFailure(undefined);
      }
    } catch (cause) {
      if (token === requestRef.current) setError(errorText(cause));
    } finally {
      if (token === requestRef.current) setRestoring(false);
    }
  };
  return {
    draftForm,
    project,
    draft,
    setDraft,
    error,
    busy,
    stopping,
    restoring,
    historyOpen,
    setHistoryOpen,
    runs,
    historyRef,
    inputRef,
    submitted,
    creating,
    currentSelection,
    availableSelectionLabel,
    run,
    runId,
    failedRestore,
    status,
    recoveryPending,
    lifecycle,
    outcomeUnknown,
    retryBlocked,
    acceptedSelectionLabel,
    remediesForSelection,
    events,
    data,
    result,
    zoneRequested,
    existing,
    existingUnverified,
    issues,
    shortlist,
    speciesIds,
    catalog,
    speciesNames,
    arrangementLabel,
    existingSpecies,
    previousSpecies,
    existingScope,
    incomplete,
    active,
    previewState,
    zonePreview,
    zoneChange,
    stale,
    zoneBlocked,
    approval,
    previewUnavailable,
    reviewStatus,
    reviewNotice,
    committed,
    zoneCommitted,
    appliedZoneChange,
    answering,
    staleSelection,
    selectionRecovery,
    decisionDisabled,
    inputDisabled,
    canCancel,
    kinds,
    zones,
    scopeLabels,
    failureRemedy,
    mapControl,
    shownZone,
    retryRestore,
    fillDraft,
    refreshOutcome,
    submit,
    approve,
    retry,
    continueRun,
    cancel,
    openRun,
  };
}
export type AutonomousRunController = ReturnType<
  typeof useAutonomousRunController
>;
