import { useCallback, useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import type { Project } from '@green/api-client';
import {
  historyCommands,
  readHistorySnapshot,
  type HistoryCommand,
} from '../api/history';

type HistoryPhase = 'idle' | 'writing' | 'refreshing' | 'recovery';

interface HistoryState {
  projectId: string;
  phase: HistoryPhase;
  action?: HistoryCommand;
  submittedAt: number;
  recoveryError?: unknown;
}

interface HistoryAttempt {
  projectId: string;
  action: HistoryCommand;
  submittedAt: number;
  receipt?: Project;
  notified: boolean;
}

export interface PlanHistoryCommandsOptions {
  projectId: string;
  onCommitted: () => void;
  refresh: () => Promise<void>;
}

/** Undo/Redo share one write gate. A failed write response is not permission to
 * repeat an operation; recovery only reads and synchronizes the current facts. */
export function usePlanHistoryCommands({
  projectId,
  onCommitted,
  refresh,
}: PlanHistoryCommandsOptions) {
  const client = useQueryClient();
  const owner = useRef(projectId);
  const mounted = useRef(true);
  const active = useRef(new Map<string, HistoryAttempt>());
  const reading = useRef(new Set<string>());
  const [states, setStates] = useState<Record<string, HistoryState>>({});
  const setState = useCallback(
    (value: HistoryState) =>
      setStates((current) => ({ ...current, [value.projectId]: value })),
    [],
  );
  useEffect(() => {
    owner.current = projectId;
  }, [projectId]);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const synchronize = useCallback(
    async (attempt: HistoryAttempt) => {
      if (reading.current.has(attempt.projectId)) return;
      reading.current.add(attempt.projectId);
      const current = () =>
        mounted.current &&
        owner.current === attempt.projectId &&
        active.current.get(attempt.projectId) === attempt;
      if (mounted.current) setState({ ...attempt, phase: 'refreshing' });
      try {
        const { project, history } = await readHistorySnapshot(
          attempt.projectId,
        );
        const cached = client.getQueryData<Project>([
          'workspace-project',
          attempt.projectId,
        ]);
        if (
          (cached && cached.state_version > project.state_version) ||
          (attempt.receipt &&
            attempt.receipt.state_version > project.state_version)
        ) {
          throw new Error(
            'Полученный снимок истории устарел. Повторите чтение.',
          );
        }
        client.setQueryData<Project>(
          ['workspace-project', attempt.projectId],
          (previous) =>
            !previous || previous.state_version <= project.state_version
              ? project
              : previous,
        );
        client.setQueryData(['plan-history', attempt.projectId], history);
        if (current()) {
          if (!attempt.notified) {
            attempt.notified = true;
            onCommitted();
          }
          await refresh();
        }
        if (active.current.get(attempt.projectId) === attempt)
          active.current.delete(attempt.projectId);
        if (mounted.current)
          setState({
            projectId: attempt.projectId,
            phase: 'idle',
            submittedAt: attempt.submittedAt,
          });
      } catch (recoveryError) {
        if (mounted.current)
          setState({ ...attempt, phase: 'recovery', recoveryError });
      } finally {
        reading.current.delete(attempt.projectId);
      }
    },
    [client, onCommitted, refresh, setState],
  );

  const mutate = useCallback(
    (action: HistoryCommand) => {
      if (active.current.has(projectId)) return;
      const attempt: HistoryAttempt = {
        projectId,
        action,
        submittedAt: Date.now(),
        notified: false,
      };
      active.current.set(projectId, attempt);
      setState({ ...attempt, phase: 'writing' });
      void historyCommands[action](projectId)
        .then(async (receipt) => {
          if (receipt.id !== projectId)
            throw new Error(
              'Сервер вернул другой проект. Требуется проверить историю.',
            );
          attempt.receipt = receipt;
          client.setQueryData<Project>(
            ['workspace-project', projectId],
            (previous) =>
              !previous || previous.state_version <= receipt.state_version
                ? receipt
                : previous,
          );
          await synchronize(attempt);
        })
        .catch(async () => {
          // Includes publication failures after a known receipt. Neither path writes again.
          await synchronize(attempt);
        });
    },
    [client, projectId, synchronize, setState],
  );

  const retry = useCallback(() => {
    const attempt = active.current.get(projectId);
    if (attempt) void synchronize(attempt);
  }, [projectId, synchronize]);
  const reset = useCallback(() => {
    if (active.current.has(projectId)) return;
    setState({ projectId, phase: 'idle', submittedAt: 0 });
  }, [projectId, setState]);

  const visible = states[projectId] ?? {
    projectId,
    phase: 'idle' as const,
    submittedAt: 0,
  };
  const command = (action: HistoryCommand) => ({
    mutate: () => mutate(action),
    reset,
    isPending:
      visible.action === action &&
      (visible.phase === 'writing' || visible.phase === 'refreshing'),
    error: null,
    submittedAt: visible.action === action ? visible.submittedAt : 0,
  });
  return {
    undoChange: command('undo'),
    redoChange: command('redo'),
    blocked: visible.phase !== 'idle',
    recovery:
      visible.phase === 'recovery' || visible.phase === 'refreshing'
        ? {
            message:
              visible.recoveryError instanceof Error
                ? visible.recoveryError.message
                : 'Проверяем актуальный план и историю. Повтор команды не выполняется.',
            loading: visible.phase === 'refreshing',
            onRetry: retry,
          }
        : undefined,
  };
}
