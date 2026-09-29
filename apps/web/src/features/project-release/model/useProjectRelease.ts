import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useSyncExternalStore,
} from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ApiClientError, type Plan } from '@green/api-client';
import {
  emptyReleaseForm,
  releaseDraftRequest,
  sameReleaseContext,
  type ReleaseDraft,
  type ReleaseDraftContext,
} from './releaseDraft';
import {
  getReleaseDraftSnapshot,
  subscribeReleaseDraft,
  updateReleaseDraft,
} from './releaseDraftStorage';

import {
  createRelease,
  readRelease,
  releaseQueryKey as releaseKey,
} from '../api/releases';
import { useReleaseDraftForm } from './useReleaseDraftForm';

export interface ProjectReleaseOptions {
  plan?: Plan;
  geometryVersion?: number;
  initialHorizon?: number;
}

/** One feature owner coordinates local notes and authoritative immutable packages. */
export function useProjectRelease(
  projectId: string,
  options: ProjectReleaseOptions = {},
) {
  const queryClient = useQueryClient();
  const subscribe = useCallback(
    (callback: () => void) => subscribeReleaseDraft(projectId, callback),
    [projectId],
  );
  const getSnapshot = useCallback(
    () => getReleaseDraftSnapshot(projectId),
    [projectId],
  );
  const state = useSyncExternalStore(subscribe, getSnapshot);
  const planId = options.plan?.id,
    planVersion = options.plan?.version,
    geometryVersion = options.geometryVersion;
  const context = useMemo<ReleaseDraftContext>(
    () => ({
      planId,
      planVersion: planVersion ?? 1,
      geometryVersion: geometryVersion ?? 0,
    }),
    [planId, planVersion, geometryVersion],
  );
  const contextKnown = Boolean(
    options.plan && options.geometryVersion !== undefined,
  );
  const stale = Boolean(
    state.draft && !sameReleaseContext(state.draft.context, context),
  );
  const loading = state.submission?.phase === 'pending';
  const form = state.draft ?? emptyReleaseForm(options.initialHorizon ?? 0);
  useLayoutEffect(() => {
    if (planVersion === undefined || geometryVersion === undefined) return;
    const observedContext = { planId, planVersion, geometryVersion };
    updateReleaseDraft(projectId, (current) =>
      current.observedContext &&
      sameReleaseContext(current.observedContext, observedContext)
        ? current
        : { ...current, observedContext },
    );
  }, [projectId, planId, planVersion, geometryVersion]);

  const saved = useQuery({
    queryKey: releaseKey(projectId, state.releaseId),
    queryFn: () => readRelease(projectId, state.releaseId!),
    enabled: Boolean(projectId && state.releaseId),
    retry: false,
    staleTime: Number.POSITIVE_INFINITY,
    refetchOnMount: 'always',
    refetchOnWindowFocus: false,
  });
  const missing =
    saved.error instanceof ApiClientError && saved.error.code === 'NOT_FOUND';
  useEffect(() => {
    if (!missing || !state.releaseId) return;
    updateReleaseDraft(projectId, (current) =>
      current.releaseId === state.releaseId
        ? { ...current, releaseId: undefined }
        : current,
    );
  }, [missing, projectId, state.releaseId]);

  const newDraft = useCallback(
    (): ReleaseDraft => ({
      ...emptyReleaseForm(options.initialHorizon ?? 0),
      id: crypto.randomUUID(),
      revision: 0,
      context,
      editingReleaseId: state.releaseId,
    }),
    [context, options.initialHorizon, state.releaseId],
  );
  const { formMethods, updateForm } = useReleaseDraftForm({
    projectId,
    draft: state.draft,
    initialHorizon: options.initialHorizon ?? 0,
    newDraft,
  });
  const openForm = () =>
    updateReleaseDraft(projectId, (current) =>
      current.submission?.phase === 'pending'
        ? current
        : {
            ...current,
            view: 'form',
            draft: current.draft ?? newDraft(),
            error: null,
          },
    );
  const clearDraft = () =>
    updateReleaseDraft(projectId, (current) =>
      current.submission?.phase === 'pending'
        ? current
        : {
            ...current,
            draft: undefined,
            view: 'form',
            submission: undefined,
            error: null,
            notice: undefined,
          },
    );
  const reviewContext = () =>
    updateReleaseDraft(projectId, (current) =>
      !current.draft || current.submission?.phase === 'pending' || !contextKnown
        ? current
        : {
            ...current,
            draft: {
              ...current.draft,
              context,
              revision: current.draft.revision + 1,
            },
            error: null,
            notice: undefined,
          },
    );

  const create = async () => {
    const current = getReleaseDraftSnapshot(projectId);
    if (current.submission?.phase === 'pending' || current.recoveryReleaseId)
      return;
    const draft = current.draft ?? newDraft();
    const submitted = releaseDraftRequest(draft);
    const retainUnusedBasis =
      submitted.mode === 'draft' &&
      (draft.basis.pp616_status !== 'pending' ||
        draft.basis.pp1160_status !== 'pending' ||
        draft.basis.pp616_reference !== '' ||
        draft.basis.pp1160_reference !== '' ||
        draft.basis.confirmed_by !== '');
    if (
      contextKnown &&
      submitted.mode === 'final' &&
      !sameReleaseContext(draft.context, context)
    )
      return;
    const submission = {
      id: crypto.randomUUID(),
      draftId: draft.id,
      draftRevision: draft.revision,
      context,
      request: submitted,
      phase: 'pending' as const,
    };
    updateReleaseDraft(projectId, (value) => ({
      ...value,
      draft,
      submission,
      error: null,
      notice: undefined,
    }));
    try {
      const release = await createRelease(projectId, submitted);
      const versionsMatch =
        !contextKnown ||
        (release.plan_version === context.planVersion &&
          release.geometry_version === context.geometryVersion);
      const receipt = updateReleaseDraft(projectId, (latest) => {
        if (latest.submission?.id !== submission.id) return latest;
        const currentContextMatches =
          !latest.observedContext ||
          sameReleaseContext(latest.observedContext, submission.context);
        const matchesDraft =
          versionsMatch &&
          currentContextMatches &&
          latest.draft?.id === submission.draftId &&
          latest.draft.revision === submission.draftRevision &&
          sameReleaseContext(latest.draft.context, submission.context);
        const consumes = matchesDraft && !retainUnusedBasis;
        return {
          ...latest,
          releaseId: release.id,
          recoveryReleaseId: release.id,
          recoveryError: undefined,
          submission: undefined,
          error: null,
          draft: consumes ? undefined : latest.draft,
          view: matchesDraft ? 'files' : latest.view,
          notice:
            matchesDraft && retainUnusedBasis
              ? 'Основания финального выпуска сохранены в черновике.'
              : versionsMatch
                ? undefined
                : 'Пакет собран для другой версии плана или геометрии. Заметки сохранены — проверьте основания перед следующим выпуском.',
        };
      });
      // The receipt is durable before cache publication. From this point any
      // failure can only reread this exact package; it must not create another.
      if (receipt.releaseId !== release.id) return;
      try {
        queryClient.setQueryData(releaseKey(projectId, release.id), release);
        if (!versionsMatch)
          await queryClient.invalidateQueries({
            queryKey: ['workspace-project', projectId],
          });
        updateReleaseDraft(projectId, (latest) =>
          latest.recoveryReleaseId === release.id
            ? {
                ...latest,
                recoveryReleaseId: undefined,
                recoveryError: undefined,
              }
            : latest,
        );
      } catch {
        updateReleaseDraft(projectId, (latest) =>
          latest.releaseId === release.id
            ? {
                ...latest,
                recoveryError: new Error(
                  'Пакет сохранён. Не удалось обновить представление. Загрузите сохранённый пакет повторно.',
                ),
                notice:
                  'Пакет сохранён. Не удалось обновить представление. Загрузите сохранённый пакет повторно.',
              }
            : latest,
        );
      }
    } catch (cause) {
      // A validated 4xx response is a definitive refusal: no package was
      // published. Only a lost/ambiguous response may have committed a package.
      const rejected =
        cause instanceof ApiClientError &&
        ['BAD_REQUEST', 'NOT_FOUND', 'VALIDATION_ERROR'].includes(cause.code);
      updateReleaseDraft(projectId, (latest) =>
        latest.submission?.id === submission.id
          ? {
              ...latest,
              submission: rejected
                ? undefined
                : { ...latest.submission!, phase: 'unknown' },
              error:
                cause instanceof Error
                  ? cause
                  : new Error('Не удалось собрать пакет.'),
            }
          : latest,
      );
    }
  };

  const retryRestore = async () => {
    const current = getReleaseDraftSnapshot(projectId);
    if (!current.recoveryReleaseId) return saved.refetch();
    const releaseId = current.recoveryReleaseId;
    try {
      const release = await readRelease(projectId, releaseId);
      queryClient.setQueryData(releaseKey(projectId, releaseId), release);
      updateReleaseDraft(projectId, (latest) =>
        latest.recoveryReleaseId === releaseId
          ? {
              ...latest,
              recoveryReleaseId: undefined,
              recoveryError: undefined,
              notice: undefined,
            }
          : latest,
      );
    } catch (cause) {
      updateReleaseDraft(projectId, (latest) =>
        latest.recoveryReleaseId === releaseId
          ? {
              ...latest,
              recoveryError:
                cause instanceof Error
                  ? cause
                  : new Error('Не удалось загрузить сохранённый пакет.'),
            }
          : latest,
      );
    }
  };

  return {
    release: state.releaseId && !saved.isError ? saved.data : undefined,
    restoring: Boolean(state.releaseId && saved.isPending),
    restoreError:
      state.recoveryError ?? (state.releaseId && !missing ? saved.error : null),
    retryRestore,
    formMethods,
    form,
    formOpen: state.view === 'form',
    hasDraft: Boolean(state.draft),
    stale,
    draftContext: state.draft?.context,
    storageAvailable: state.storageAvailable,
    notice: state.notice,
    submissionUnknown: state.submission?.phase === 'unknown',
    openForm,
    updateForm,
    clearDraft,
    reviewContext,
    showFiles: () =>
      updateReleaseDraft(projectId, (current) =>
        current.submission?.phase === 'pending'
          ? current
          : { ...current, view: 'files' },
      ),
    createRelease: {
      mutate: () => {
        void create();
      },
      reset: () =>
        updateReleaseDraft(projectId, (current) => ({
          ...current,
          error: null,
        })),
      isPending: loading,
      error: state.error,
    },
  };
}
