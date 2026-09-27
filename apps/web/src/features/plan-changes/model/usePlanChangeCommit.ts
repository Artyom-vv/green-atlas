import { useCallback, useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import type {
  ChangeSetPreview,
  PlanMutationResult,
  Project,
} from '@green/api-client';
import { commitPlanChange } from '../api/commitPlanChange';

type CommitPhase = 'idle' | 'applying' | 'refreshing' | 'recovery' | 'failed';

interface CommitState {
  phase: CommitPhase;
  error?: unknown;
  recoveryError?: unknown;
  data?: PlanMutationResult;
  submittedAt: number;
}

export interface PlanChangeCommitOptions {
  projectId: string;
  preview?: ChangeSetPreview;
  onCommitted: (result: PlanMutationResult, preview: ChangeSetPreview) => void;
  refresh: () => Promise<void>;
}

/** A write has one synchronous gate. Recovery only rereads after a known commit;
 * it cannot issue another apply, even when invoked before React rerenders. */
export function usePlanChangeCommit({
  projectId,
  preview,
  onCommitted,
  refresh,
}: PlanChangeCommitOptions) {
  const queryClient = useQueryClient();
  const mounted = useRef(true);
  const gate = useRef(false);
  const committed = useRef(new Set<string>());
  const recovery = useRef<(() => Promise<void>) | undefined>(undefined);
  const [state, setState] = useState<CommitState>({
    phase: 'idle',
    submittedAt: 0,
  });
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const mutate = useCallback(
    (explicitPreview?: ChangeSetPreview) => {
      if (gate.current) return;
      const accepted = explicitPreview ?? preview;
      if (!accepted?.can_apply || !accepted.id || !accepted.digest) return;
      const identity = `${accepted.id}:${accepted.digest}`;
      if (committed.current.has(identity)) return;
      gate.current = true;
      const submittedAt = Date.now();
      setState({ phase: 'applying', submittedAt });

      const afterSave = async (result: PlanMutationResult) => {
        committed.current.add(identity);
        let notified = false;
        const reread = async () => {
          if (mounted.current)
            setState({ phase: 'refreshing', data: result, submittedAt });
          try {
            // All work after a receipt belongs to recovery, never to write failure.
            // Publication is idempotent and targets the captured project.
            queryClient.setQueryData<Project>(
              ['workspace-project', projectId],
              (current) =>
                current && current.state_version <= result.state_version
                  ? {
                      ...current,
                      plan: result.plan,
                      state_version: result.state_version,
                    }
                  : current,
            );
            if (mounted.current && !notified) {
              notified = true;
              onCommitted(result, accepted);
            }
            await refresh();
            recovery.current = undefined;
            gate.current = false;
            if (mounted.current)
              setState({ phase: 'idle', data: result, submittedAt });
          } catch (recoveryError) {
            recovery.current = reread;
            if (mounted.current)
              setState({
                phase: 'recovery',
                data: result,
                recoveryError,
                submittedAt,
              });
          }
        };
        await reread();
      };
      void commitPlanChange(projectId, accepted).then(
        afterSave,
        (error: unknown) => {
          gate.current = false;
          if (mounted.current)
            setState({ phase: 'failed', error, submittedAt });
        },
      );
    },
    [onCommitted, preview, projectId, queryClient, refresh],
  );

  const retryRefresh = useCallback(() => {
    const reread = recovery.current;
    if (!reread) return;
    recovery.current = undefined;
    void reread();
  }, []);
  const reset = useCallback(() => {
    if (gate.current) return;
    setState({ phase: 'idle', submittedAt: 0 });
  }, []);

  return {
    ...state,
    isPending: state.phase === 'applying' || state.phase === 'refreshing',
    needsRefresh: state.phase === 'recovery',
    mutate,
    reset,
    retryRefresh,
  };
}
