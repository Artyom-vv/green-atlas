import { useCallback, useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import {
  ApiClientError,
  isUnsentLocalRequest,
  type ChangeSetPreview,
  type PlanChangeReceipt,
  type PlanMutationResult,
  type Project,
} from '@green/api-client';
import {
  acknowledgeProjectWrite,
  validateAcknowledgedProjectRead,
} from '@/entities/project/model/acknowledgedRead';
import type { WriteOutcome } from '@/shared/model/writeOutcome';
import { requirePlanChangeReceipt } from '@/entities/project/model/planChangeReceipt';
import {
  commitPlanChange,
  readCommittedProject,
  readPlanChangeReceipt,
} from '../api/commitPlanChange';

type CommitPhase =
  | 'idle'
  | 'applying'
  | 'refreshing'
  | 'resolving'
  | 'recovery'
  | 'unknown'
  | 'failed';
interface CommitState {
  phase: CommitPhase;
  writeOutcome: WriteOutcome;
  error?: unknown;
  recoveryError?: unknown;
  data?: PlanMutationResult;
  committedPreviewId?: string;
  submittedAt: number;
}
export interface PlanChangeCommitOptions {
  projectId: string;
  stateVersion?: number;
  preview?: ChangeSetPreview;
  onCommitted: (result: PlanMutationResult, preview: ChangeSetPreview) => void;
  refresh: () => Promise<void>;
}
const emptyState: CommitState = {
  phase: 'idle',
  writeOutcome: 'idle',
  submittedAt: 0,
};
const definitiveRefusal = (error: unknown) =>
  isUnsentLocalRequest(error) ||
  (error instanceof ApiClientError &&
    [
      'BAD_REQUEST',
      'VALIDATION_ERROR',
      'NOT_FOUND',
      'VERSION_CONFLICT',
      'PREVIEW_EXPIRED',
      'PREVIEW_STALE',
      'PREVIEW_BLOCKED',
    ].includes(error.code));

/** A sent apply is never replayed to discover its outcome. Read its exact receipt. */
export function usePlanChangeCommit({
  projectId,
  stateVersion,
  preview,
  onCommitted,
  refresh,
}: PlanChangeCommitOptions) {
  const client = useQueryClient();
  const mounted = useRef(true);
  const gate = useRef(false);
  const recovering = useRef(false);
  const committed = useRef(new Set<string>());
  const recovery = useRef<(() => Promise<void>) | undefined>(undefined);
  const [state, setState] = useState<CommitState>(emptyState);
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
      const baseStateVersion = stateVersion;
      let notified = false;
      let receipt: PlanChangeReceipt | undefined;
      const publish = (next: Omit<CommitState, 'submittedAt'>) => {
        if (mounted.current) setState({ ...next, submittedAt });
      };
      const acknowledge = (stateVersion: number, planVersion: number) => {
        committed.current.add(identity);
        acknowledgeProjectWrite(client, projectId, {
          stateVersion,
          planVersion,
        });
      };
      const finishCommitted = async (result: PlanMutationResult) => {
        committed.current.add(identity);
        const reread = async () => {
          publish({
            phase: 'refreshing',
            writeOutcome: 'confirmed',
            data: result,
            committedPreviewId: accepted.id,
          });
          try {
            acknowledge(result.state_version, result.plan.version);
            client.setQueryData<Project>(
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
            publish({
              phase: 'idle',
              writeOutcome: 'idle',
              data: result,
              committedPreviewId: accepted.id,
            });
          } catch (recoveryError) {
            recovery.current = reread;
            publish({
              phase: 'recovery',
              writeOutcome: 'confirmed',
              data: result,
              committedPreviewId: accepted.id,
              recoveryError,
            });
          }
        };
        await reread();
      };
      const resolveOutcome = async () => {
        publish({
          phase: 'resolving',
          writeOutcome: receipt ? 'confirmed' : 'unknown',
          committedPreviewId: receipt ? accepted.id : undefined,
        });
        try {
          if (!receipt) {
            if (!baseStateVersion)
              throw new Error(
                'Не сохранена исходная версия команды. Результат записи не подтверждён.',
              );
            const found = await readPlanChangeReceipt(
              projectId,
              accepted,
              baseStateVersion,
            );
            receipt = requirePlanChangeReceipt(
              projectId,
              accepted,
              baseStateVersion,
              found,
            );
            acknowledge(receipt.state_version, receipt.plan_version);
          }
          const actual = validateAcknowledgedProjectRead(
            client,
            projectId,
            await readCommittedProject(projectId),
          );
          if (!actual.plan)
            throw new Error('Сохранённый план пока не удалось прочитать.');
          client.setQueryData<Project>(
            ['workspace-project', projectId],
            actual,
          );
          const existing = new Set(
            (actual.plan.objects ?? []).map((object) => object.id),
          );
          await finishCommitted({
            change_set_id: receipt.change_set_id,
            state_version: actual.state_version,
            plan_version: actual.plan.version,
            plan: actual.plan,
            added_ids:
              receipt.status === 'applied'
                ? (receipt.added_ids ?? []).filter((id) => existing.has(id))
                : [],
            updated_ids:
              receipt.status === 'applied'
                ? (receipt.updated_ids ?? []).filter((id) => existing.has(id))
                : [],
            deleted_ids: receipt.deleted_ids,
          });
        } catch (recoveryError) {
          recovery.current = resolveOutcome;
          publish({
            phase: receipt ? 'recovery' : 'unknown',
            writeOutcome: receipt ? 'confirmed' : 'unknown',
            committedPreviewId: receipt ? accepted.id : undefined,
            recoveryError,
          });
        }
      };
      publish({ phase: 'applying', writeOutcome: 'writing' });
      void commitPlanChange(projectId, accepted).then(
        async (result) => {
          if (
            result.change_set_id !== accepted.id ||
            !Number.isSafeInteger(result.state_version) ||
            result.state_version <= (baseStateVersion ?? 0) ||
            !result.plan ||
            !Number.isSafeInteger(result.plan_version) ||
            result.plan_version !== result.plan.version ||
            result.plan_version <= accepted.base_plan_version
          ) {
            await resolveOutcome();
            return;
          }
          await finishCommitted(result);
        },
        async (error: unknown) => {
          if (definitiveRefusal(error)) {
            gate.current = false;
            publish({ phase: 'failed', writeOutcome: 'idle', error });
          } else await resolveOutcome();
        },
      );
    },
    [client, onCommitted, preview, projectId, refresh, stateVersion],
  );

  const retryRefresh = useCallback(() => {
    const reread = recovery.current;
    if (!reread || recovering.current) return;
    recovering.current = true;
    recovery.current = undefined;
    void reread().finally(() => {
      recovering.current = false;
    });
  }, []);
  const reset = useCallback(() => {
    if (!gate.current) setState(emptyState);
  }, []);
  return {
    ...state,
    isPending: ['applying', 'refreshing', 'resolving'].includes(state.phase),
    needsRefresh: state.phase === 'recovery' || state.phase === 'unknown',
    readRecovery: ['refreshing', 'resolving', 'recovery', 'unknown'].includes(
      state.phase,
    ),
    mutate,
    reset,
    retryRefresh,
  };
}
