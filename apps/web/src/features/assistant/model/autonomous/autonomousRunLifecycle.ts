import type { AgentRun } from '@green/api-client';

/** Execution readiness and ownership come from the server, including after reload. */
export function autonomousRunLifecycle(
  run: AgentRun | undefined,
  uncertain: boolean,
) {
  const status = run?.state.status;
  const active =
    status === 'queued' ||
    status === 'scheduled' ||
    status === 'running' ||
    status === 'waiting_ui';
  const executing =
    status === 'scheduled' || status === 'running' || status === 'waiting_ui';
  const outcomeUnknown =
    run?.state.failure?.code === 'APPROVAL_OUTCOME_UNKNOWN';
  const phase = uncertain ? 'uncertain' : (status ?? 'idle');
  return {
    phase,
    active,
    executing: executing && !uncertain,
    poll: executing && !uncertain,
    canContinue: status === 'queued' && !uncertain,
    canCompose: !active && !uncertain,
    question: status === 'waiting_question',
    canApprove: status === 'waiting_approval' && !uncertain,
    canCancel: Boolean(
      status &&
      !['finished', 'failed', 'cancelled'].includes(status) &&
      !uncertain,
    ),
    canRetry: Boolean(
      status &&
      ['failed', 'cancelled', 'waiting_approval'].includes(status) &&
      !uncertain &&
      !outcomeUnknown &&
      run?.state.failure?.retryable !== false,
    ),
  };
}
