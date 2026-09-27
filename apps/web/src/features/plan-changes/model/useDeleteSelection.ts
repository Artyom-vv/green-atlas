import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { useQueryClient } from '@tanstack/react-query';
import type { Plan, Project } from '@green/api-client';
import { deleteSelection, readDeletionProject } from '../api/deleteSelection';
import {
  captureDeletion,
  UNKNOWN_DELETION_NOTICE,
  validateDeletionRead,
  type DeleteSelectionContext,
} from './deleteSelection';

export interface DeleteSelectionOptions {
  projectId: string;
  project?: Project;
  disabled?: boolean;
  onCommitted: (
    project: Project,
    context: DeleteSelectionContext,
  ) => void | Promise<void>;
  refresh: () => Promise<void>;
}

type DeletePhase = 'idle' | 'writing' | 'refreshing' | 'recovery';
interface DeleteState {
  projectId: string;
  phase: DeletePhase;
  submittedAt: number;
  error?: unknown;
  recoveryError?: unknown;
  notice?: string;
}
interface DeleteOwner {
  projectId: string;
}
interface DeleteAttempt {
  context: DeleteSelectionContext;
  owner: DeleteOwner;
  submittedAt: number;
  receipt?: Plan;
  writeError?: unknown;
  notified: boolean;
  reading: boolean;
  onCommitted: DeleteSelectionOptions['onCommitted'];
  refresh: DeleteSelectionOptions['refresh'];
}

/** A Plan receipt confirms the write, but only a Project read supplies its state
 * version. Recovery never copies a receipt into a guessed Project or repeats POST. */
export function useDeleteSelection(options: DeleteSelectionOptions) {
  const client = useQueryClient();
  const latest = useRef(options);
  const visibleOwner = useMemo<DeleteOwner>(
    () => ({ projectId: options.projectId }),
    [options.projectId],
  );
  const owner = useRef(visibleOwner);
  useLayoutEffect(() => {
    latest.current = options;
    owner.current = visibleOwner;
  }, [options, visibleOwner]);
  const mounted = useRef(true);
  const attempts = useRef(new Map<string, DeleteAttempt>());
  const [states, setStates] = useState<Record<string, DeleteState>>({});
  const publishState = useCallback((state: DeleteState) => {
    if (mounted.current)
      setStates((current) => ({ ...current, [state.projectId]: state }));
  }, []);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const publishProject = useCallback(
    (projectId: string, project: Project) => {
      client.setQueryData<Project>(
        ['workspace-project', projectId],
        (current) =>
          !current || current.state_version <= project.state_version
            ? project
            : current,
      );
      return (
        client.getQueryData<Project>(['workspace-project', projectId]) ??
        project
      );
    },
    [client],
  );

  const synchronize = useCallback(
    async (attempt: DeleteAttempt) => {
      const { projectId } = attempt.context;
      if (attempt.reading || attempts.current.get(projectId) !== attempt)
        return;
      attempt.reading = true;
      const state = {
        projectId,
        submittedAt: attempt.submittedAt,
        error: attempt.writeError,
      };
      publishState({ ...state, phase: 'refreshing' });
      try {
        const actual = await readDeletionProject(projectId);
        validateDeletionRead(actual, attempt.context, attempt.receipt);
        const cached = client.getQueryData<Project>([
          'workspace-project',
          projectId,
        ]);
        if (
          !attempt.receipt &&
          cached &&
          actual.state_version < cached.state_version
        )
          throw new Error(
            'Получен устаревший снимок плана. Повторите чтение проекта.',
          );
        const project = publishProject(projectId, actual);
        if (attempt.receipt && !attempt.notified) {
          attempt.notified = true;
          if (mounted.current && owner.current === attempt.owner)
            await attempt.onCommitted(project, attempt.context);
        }
        await attempt.refresh();
        // An older external query must not roll back an acknowledged snapshot.
        publishProject(projectId, project);
        attempts.current.delete(projectId);
        publishState({
          ...state,
          phase: 'idle',
          notice: attempt.receipt ? undefined : UNKNOWN_DELETION_NOTICE,
        });
      } catch (recoveryError) {
        publishState({ ...state, phase: 'recovery', recoveryError });
      } finally {
        attempt.reading = false;
      }
    },
    [client, publishProject, publishState],
  );

  const mutate = useCallback(
    (ids: string[]) => {
      const captured = latest.current;
      const project = captured.project;
      if (
        !mounted.current ||
        owner.current !== visibleOwner ||
        captured.disabled ||
        !project ||
        project.id !== captured.projectId ||
        !project.plan ||
        ids.length === 0 ||
        attempts.current.has(captured.projectId)
      )
        return;
      const context = captureDeletion(captured.projectId, project, ids);
      const attempt: DeleteAttempt = {
        context,
        owner: visibleOwner,
        submittedAt: Date.now(),
        notified: false,
        reading: false,
        onCommitted: captured.onCommitted,
        refresh: captured.refresh,
      };
      // Close the gate before React renders a disabled control.
      attempts.current.set(context.projectId, attempt);
      const cached = client.getQueryData<Project>([
        'workspace-project',
        context.projectId,
      ]);
      if (cached && cached.state_version > context.expectedStateVersion) {
        attempt.writeError = new Error(
          'Рабочая версия плана устарела. Обновляем проект перед удалением.',
        );
        void synchronize(attempt);
        return;
      }
      publishState({
        projectId: context.projectId,
        phase: 'writing',
        submittedAt: attempt.submittedAt,
      });
      void deleteSelection(context).then(
        async (receipt) => {
          if (
            !Number.isFinite(receipt.version) ||
            receipt.version < context.basePlanVersion
          )
            attempt.writeError = new Error(
              'Не удалось подтвердить удаление. Проверяем актуальный проект.',
            );
          else attempt.receipt = receipt;
          await synchronize(attempt);
        },
        async (error) => {
          attempt.writeError = error;
          await synchronize(attempt);
        },
      );
    },
    [client, visibleOwner, publishState, synchronize],
  );

  const retryRefresh = useCallback(() => {
    if (owner.current !== visibleOwner) return;
    const attempt = attempts.current.get(options.projectId);
    if (attempt && states[options.projectId]?.phase === 'recovery')
      void synchronize(attempt);
  }, [options.projectId, visibleOwner, states, synchronize]);
  const reset = useCallback(() => {
    if (
      owner.current !== visibleOwner ||
      attempts.current.has(options.projectId)
    )
      return;
    publishState({
      projectId: options.projectId,
      phase: 'idle',
      submittedAt: 0,
    });
  }, [options.projectId, visibleOwner, publishState]);
  const visible = states[options.projectId] ?? {
    projectId: options.projectId,
    phase: 'idle' as const,
    submittedAt: 0,
  };
  const isPending =
    visible.phase === 'writing' || visible.phase === 'refreshing';
  return {
    ...visible,
    mutate,
    reset,
    isPending,
    blocked: visible.phase !== 'idle',
    recovery:
      visible.phase === 'recovery'
        ? {
            message:
              'Удаление отправлено. Перечитайте проект, прежде чем продолжить работу.',
            loading: false,
            onRetry: retryRefresh,
          }
        : undefined,
  };
}
