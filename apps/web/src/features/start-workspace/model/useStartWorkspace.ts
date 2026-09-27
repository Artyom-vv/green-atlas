import { useQueryClient } from '@tanstack/react-query';
import type { PlantingZoneAssignment, Project } from '@green/api-client';
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { startWorkspaceApi } from '../api/startWorkspace';

type StartPhase =
  'idle' | 'saving' | 'starting' | 'refreshing' | 'recovery' | 'ready';

interface StartAttempt {
  projectId: string;
  zones: PlantingZoneAssignment[];
  basis: number;
  saved?: Project;
  completed?: Project;
  notified: boolean;
  writing: boolean;
  unresolved: boolean;
  onCommitted: (project: Project) => void;
  refresh: () => Promise<void>;
}

interface StartState {
  projectId: string;
  phase: StartPhase;
  submittedAt: number;
  error?: unknown;
  notice?: string;
}

export interface StartWorkspaceOptions {
  projectId: string;
  project?: Project;
  zones: PlantingZoneAssignment[];
  onCommitted: (project: Project) => void;
  refresh: () => Promise<void>;
}

const sameZones = (
  left: PlantingZoneAssignment[],
  right: PlantingZoneAssignment[],
) => JSON.stringify(left) === JSON.stringify(right);

/** A confirmed zones receipt is retained across a failed start. Explicit
 * If-Match on each step prevents a late request from recreating a newer plan. */
export function useStartWorkspace({
  projectId,
  project,
  zones,
  onCommitted,
  refresh,
}: StartWorkspaceOptions) {
  const client = useQueryClient();
  const mounted = useRef(true);
  const owner = useRef(projectId);
  const attempts = useRef(new Map<string, StartAttempt>());
  const [states, setStates] = useState<Record<string, StartState>>({});
  useLayoutEffect(() => {
    owner.current = projectId;
  }, [projectId]);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const current = (attempt: StartAttempt) =>
    mounted.current &&
    owner.current === attempt.projectId &&
    attempts.current.get(attempt.projectId) === attempt;
  const publish = (
    attempt: StartAttempt,
    phase: StartPhase,
    error?: unknown,
    notice?: string,
  ) => {
    if (mounted.current && attempts.current.get(attempt.projectId) === attempt)
      setStates((previous) => ({
        ...previous,
        [attempt.projectId]: {
          submittedAt: previous[attempt.projectId]?.submittedAt ?? 0,
          projectId: attempt.projectId,
          phase,
          error,
          notice,
        },
      }));
  };
  const cache = (next: Project) =>
    client.setQueryData<Project>(['workspace-project', next.id], (previous) =>
      !previous || previous.state_version <= next.state_version
        ? next
        : previous,
    );
  const finish = async (attempt: StartAttempt, result: Project) => {
    attempt.completed = result;
    const effective = cache(result) ?? result;
    publish(attempt, 'refreshing');
    if (current(attempt) && !attempt.notified) {
      attempt.notified = true;
      attempt.onCommitted(effective);
    }
    await attempt.refresh();
    attempt.unresolved = false;
    publish(attempt, 'idle');
  };
  const read = async (attempt: StartAttempt) => {
    publish(attempt, 'refreshing');
    try {
      const observed = await startWorkspaceApi.readProject(attempt.projectId);
      const actual = cache(observed) ?? observed;
      if (actual.plan) {
        await finish(attempt, actual);
        return;
      }
      attempt.unresolved = false;
      if (sameZones(actual.planting_zones ?? [], attempt.zones)) {
        attempt.saved = actual;
        publish(
          attempt,
          'ready',
          undefined,
          'Участки сохранены. Можно продолжить создание плана.',
        );
      } else {
        attempt.saved = undefined;
        publish(
          attempt,
          'idle',
          undefined,
          'Состояние проекта обновлено. Проверьте участки перед созданием плана.',
        );
      }
    } catch (error) {
      attempt.unresolved = true;
      publish(
        attempt,
        'recovery',
        error,
        'Не удалось подтвердить состояние проекта. Повторите чтение перед продолжением.',
      );
    }
  };
  const retryRead = async () => {
    const attempt = attempts.current.get(projectId);
    if (!attempt || attempt.writing) return;
    attempt.writing = true;
    try {
      await read(attempt);
    } finally {
      attempt.writing = false;
    }
  };
  const mutate = () => {
    if (!project) return;
    const previous = attempts.current.get(projectId);
    if (previous?.writing || previous?.unresolved) return;
    const reusable =
      previous?.saved &&
      previous.saved.state_version === project.state_version &&
      sameZones(previous.zones, zones);
    const attempt: StartAttempt = {
      projectId,
      zones: structuredClone(zones),
      basis: project.state_version,
      saved: reusable ? previous.saved : undefined,
      completed: project.plan ? project : undefined,
      notified: false,
      writing: true,
      unresolved: false,
      onCommitted,
      refresh,
    };
    attempts.current.set(projectId, attempt);
    setStates((previous) => ({
      ...previous,
      [projectId]: {
        projectId,
        phase: attempt.saved ? 'starting' : 'saving',
        submittedAt: Date.now(),
      },
    }));
    void (async () => {
      try {
        if (attempt.completed) {
          await finish(attempt, attempt.completed);
          return;
        }
        if (!attempt.saved) {
          attempt.saved = await startWorkspaceApi.saveZones(
            projectId,
            attempt.zones,
            { expectedStateVersion: attempt.basis },
          );
          cache(attempt.saved);
        }
        // A newer project can have acquired a plan while the source task was open.
        if (attempt.saved.plan) {
          await finish(attempt, attempt.saved);
          return;
        }
        publish(attempt, 'starting');
        const result = await startWorkspaceApi.createPlan(projectId, {
          expectedStateVersion: attempt.saved.state_version,
        });
        await finish(attempt, result);
      } catch (error) {
        attempt.unresolved = true;
        publish(attempt, 'recovery', error);
        await read(attempt);
      } finally {
        attempt.writing = false;
      }
    })();
  };
  const shown = states[projectId];
  const pending =
    shown?.phase === 'saving' ||
    shown?.phase === 'starting' ||
    shown?.phase === 'refreshing';
  return {
    mutate,
    isPending: pending,
    blocked: pending || shown?.phase === 'recovery',
    submittedAt: shown?.submittedAt ?? 0,
    error: shown?.error,
    notice: shown?.notice,
    reset: () => {
      const attempt = attempts.current.get(projectId);
      if (attempt?.writing || attempt?.unresolved) return;
      setStates((previous) => ({
        ...previous,
        [projectId]: { projectId, phase: 'idle', submittedAt: 0 },
      }));
    },
    recovery: shown?.phase === 'recovery' ? { onRetry: retryRead } : undefined,
  };
}
