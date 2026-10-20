import { useCallback, useLayoutEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { isUnsentLocalRequest, type Project } from '@green/api-client';
import {
  acknowledgeProjectWrite,
  validateAcknowledgedProjectRead,
} from '@/entities/project/model/acknowledgedRead';
import {
  captureHistoryCommand,
  historyGoalMatches,
  type HistoryCommandContext,
} from './historyProof';
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
  sent: boolean;
  context?: HistoryCommandContext;
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
  useLayoutEffect(() => {
    owner.current = projectId;
  }, [projectId]);
  useLayoutEffect(() => {
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
        validateAcknowledgedProjectRead(client, attempt.projectId, project);
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
        if (
          attempt.sent &&
          !attempt.receipt &&
          (!attempt.context ||
            !historyGoalMatches(attempt.context, project, history))
        )
          throw new Error(
            'Результат команды истории пока не подтверждён. Перечитайте проект. Повтор команды не выполняется.',
          );
        if (current()) {
          if (attempt.sent && !attempt.notified) {
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
        sent: false,
      };
      active.current.set(projectId, attempt);
      setState({ ...attempt, phase: 'refreshing' });
      void (async () => {
        try {
          const snapshot = await readHistorySnapshot(projectId);
          validateAcknowledgedProjectRead(client, projectId, snapshot.project);
          const cached = client.getQueryData<Project>([
            'workspace-project',
            projectId,
          ]);
          if (cached && cached.state_version > snapshot.project.state_version)
            throw new Error(
              'Рабочая версия истории устарела. Повторите чтение проекта.',
            );
          attempt.context = captureHistoryCommand(
            projectId,
            snapshot.project,
            snapshot.history,
            action,
          );
        } catch (recoveryError) {
          if (mounted.current)
            setState({ ...attempt, phase: 'recovery', recoveryError });
          return;
        }
        // A pure preflight read does not block leaving. Its late answer must
        // never start a new write for a departed document or project.
        if (
          !mounted.current ||
          owner.current !== projectId ||
          active.current.get(projectId) !== attempt
        ) {
          if (active.current.get(projectId) === attempt)
            active.current.delete(projectId);
          if (mounted.current)
            setState({
              projectId,
              phase: 'idle',
              submittedAt: attempt.submittedAt,
            });
          return;
        }
        attempt.sent = true;
        if (mounted.current) setState({ ...attempt, phase: 'writing' });
        try {
          const receipt = await historyCommands[action](projectId, {
            expectedStateVersion: attempt.context.stateVersion,
          });
          if (receipt.id !== projectId)
            throw new Error(
              'Сервер вернул другой проект. Требуется проверить историю.',
            );
          attempt.receipt = receipt;
          acknowledgeProjectWrite(client, projectId, {
            stateVersion: receipt.state_version,
            planVersion: receipt.plan?.version,
          });
          client.setQueryData<Project>(
            ['workspace-project', projectId],
            (previous) =>
              !previous || previous.state_version <= receipt.state_version
                ? receipt
                : previous,
          );
          await synchronize(attempt);
        } catch (error: unknown) {
          if (!attempt.receipt && isUnsentLocalRequest(error))
            attempt.sent = false;
          // Includes publication failures after a known receipt. Neither path writes again.
          await synchronize(attempt);
        }
      })();
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
    writeOutcome:
      visible.phase === 'writing'
        ? ('writing' as const)
        : visible.phase === 'idle'
          ? ('idle' as const)
          : active.current.get(projectId)?.receipt
            ? ('confirmed' as const)
            : active.current.get(projectId)?.sent
              ? ('unknown' as const)
              : ('idle' as const),
    readRecovery:
      visible.phase === 'refreshing' || visible.phase === 'recovery',
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
