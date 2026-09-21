import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { useQueryClient } from '@tanstack/react-query';
import type { Project } from '@green/api-client';
import { readZoneProject, saveZoneSnapshot } from '../api/zoneCommands';
import {
  captureZoneCommand,
  UNKNOWN_ZONE_SAVE_NOTICE,
  type ManagedZonesSave,
  type PlacementZoneSave,
  type ZoneCommandContext,
  type ZoneSaveCommand,
} from './zoneCommand';

type ZoneSavePhase = 'idle' | 'writing' | 'refreshing' | 'recovery';
interface ZoneSaveState {
  projectId: string;
  phase: ZoneSavePhase;
  kind?: ZoneSaveCommand['kind'];
  submittedAt: number;
  data?: Project;
  error?: unknown;
  recoveryError?: unknown;
  notice?: string;
}
interface ZoneOwner {
  projectId: string;
}
interface ZoneSaveAttempt {
  context: ZoneCommandContext;
  owner: ZoneOwner;
  submittedAt: number;
  receipt?: Project;
  writeError?: unknown;
  notified: boolean;
  reading: boolean;
  onCommitted: ZoneCommandsOptions['onCommitted'];
  refresh: ZoneCommandsOptions['refresh'];
}
export interface ZoneCommandsOptions {
  projectId: string;
  project?: Project;
  disabled?: boolean;
  onCommitted: (
    project: Project,
    context: ZoneCommandContext,
  ) => void | Promise<void>;
  refresh: () => Promise<void>;
}

/** Placement and management share one gate per project. Once a write is sent,
 * cancellation/reset cannot turn its unknown or committed outcome into a retry. */
export function useZoneCommands(options: ZoneCommandsOptions) {
  const client = useQueryClient();
  const latest = useRef(options);
  const visibleOwner = useMemo<ZoneOwner>(
    () => ({ projectId: options.projectId }),
    [options.projectId],
  );
  const owner = useRef(visibleOwner);
  useLayoutEffect(() => {
    latest.current = options;
    owner.current = visibleOwner;
  }, [options, visibleOwner]);
  const mounted = useRef(true);
  const attempts = useRef(new Map<string, ZoneSaveAttempt>());
  const [states, setStates] = useState<Record<string, ZoneSaveState>>({});
  const publishState = useCallback((state: ZoneSaveState) => {
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
    (projectId: string, snapshot: Project) => {
      if (snapshot.id !== projectId)
        throw new Error(
          'Сервер вернул другой проект. Требуется перечитать участки.',
        );
      client.setQueryData<Project>(
        ['workspace-project', projectId],
        (current) =>
          !current || current.state_version <= snapshot.state_version
            ? snapshot
            : current,
      );
      return (
        client.getQueryData<Project>(['workspace-project', projectId]) ??
        snapshot
      );
    },
    [client],
  );

  const synchronize = useCallback(
    async (attempt: ZoneSaveAttempt) => {
      const { projectId } = attempt.context;
      if (attempt.reading || attempts.current.get(projectId) !== attempt)
        return;
      attempt.reading = true;
      const state = {
        projectId,
        kind: attempt.context.kind,
        submittedAt: attempt.submittedAt,
        error: attempt.writeError,
      };
      publishState({ ...state, phase: 'refreshing', data: attempt.receipt });
      try {
        let project: Project;
        if (attempt.receipt) {
          project = publishProject(projectId, attempt.receipt);
          if (!attempt.notified) {
            attempt.notified = true;
            if (mounted.current && owner.current === attempt.owner)
              await attempt.onCommitted(project, attempt.context);
          }
        } else {
          // A failed response is not evidence of a failed PUT. Read the actual state
          // without claiming that this command was committed, even if zones match.
          project = await readZoneProject(projectId);
          const cached = client.getQueryData<Project>([
            'workspace-project',
            projectId,
          ]);
          if (
            project.state_version < attempt.context.expectedStateVersion ||
            (cached && cached.state_version > project.state_version)
          ) {
            throw new Error(
              'Получен устаревший снимок участков. Повторите чтение проекта.',
            );
          }
          project = publishProject(projectId, project);
        }
        // This callback was captured before the write and must only refresh that project.
        await attempt.refresh();
        // A consumer refresh may complete an older query. Keep the acknowledged
        // snapshot as a floor even when that external read writes its own cache.
        const canonical = publishProject(projectId, project);
        attempts.current.delete(projectId);
        publishState({
          ...state,
          phase: 'idle',
          data: canonical,
          notice: attempt.receipt ? undefined : UNKNOWN_ZONE_SAVE_NOTICE,
        });
      } catch (recoveryError) {
        publishState({
          ...state,
          phase: 'recovery',
          data: attempt.receipt,
          recoveryError,
        });
      } finally {
        attempt.reading = false;
      }
    },
    [client, publishProject, publishState],
  );

  const mutate = useCallback(
    (command: ZoneSaveCommand) => {
      const captured = latest.current;
      const project = captured.project;
      if (
        !mounted.current ||
        owner.current !== visibleOwner ||
        captured.disabled ||
        !project ||
        project.id !== captured.projectId ||
        attempts.current.has(captured.projectId)
      )
        return;
      const context = captureZoneCommand(captured.projectId, project, command);
      const attempt: ZoneSaveAttempt = {
        context,
        owner: visibleOwner,
        submittedAt: Date.now(),
        notified: false,
        reading: false,
        onCommitted: captured.onCommitted,
        refresh: captured.refresh,
      };
      // Set the gate before React rerenders or another entry point can start its PUT.
      attempts.current.set(context.projectId, attempt);
      const cached = client.getQueryData<Project>([
        'workspace-project',
        context.projectId,
      ]);
      if (cached && cached.state_version > context.expectedStateVersion) {
        attempt.writeError = new Error(
          'Рабочая версия участков устарела. Обновляем проект перед сохранением.',
        );
        void synchronize(attempt);
        return;
      }
      publishState({
        projectId: context.projectId,
        kind: context.kind,
        phase: 'writing',
        submittedAt: attempt.submittedAt,
      });
      void saveZoneSnapshot(context).then(
        async (receipt) => {
          if (
            receipt.id !== context.projectId ||
            receipt.state_version < context.expectedStateVersion
          ) {
            attempt.writeError = new Error(
              'Не удалось подтвердить сохранение участков. Проверяем актуальный проект.',
            );
          } else {
            attempt.receipt = receipt;
          }
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
    const attempt = attempts.current.get(options.projectId);
    if (attempt && states[options.projectId]?.phase === 'recovery')
      void synchronize(attempt);
  }, [options.projectId, states, synchronize]);
  const reset = useCallback(() => {
    if (attempts.current.has(options.projectId)) return;
    publishState({
      projectId: options.projectId,
      phase: 'idle',
      submittedAt: 0,
    });
  }, [options.projectId, publishState]);
  const visible = states[options.projectId] ?? {
    projectId: options.projectId,
    phase: 'idle' as const,
    submittedAt: 0,
  };
  const isPending =
    visible.phase === 'writing' || visible.phase === 'refreshing';
  const commandState = (kind: ZoneSaveCommand['kind']) => ({
    reset,
    isPending: visible.kind === kind && isPending,
    error: visible.kind === kind ? visible.error : undefined,
    submittedAt: visible.kind === kind ? visible.submittedAt : 0,
    isSuccess:
      visible.kind === kind &&
      visible.phase === 'idle' &&
      Boolean(visible.data) &&
      !visible.error &&
      !visible.notice,
  });
  return {
    ...visible,
    isPending,
    blocked: visible.phase !== 'idle',
    needsRefresh: visible.phase === 'recovery',
    retryRefresh,
    reset,
    savePlacementZone: {
      ...commandState('placement'),
      mutate: (variables: PlacementZoneSave) =>
        mutate({ kind: 'placement', ...variables }),
    },
    saveManagedZones: {
      ...commandState('managed'),
      mutate: (variables: ManagedZonesSave) =>
        mutate({ kind: 'managed', ...variables }),
    },
  };
}
