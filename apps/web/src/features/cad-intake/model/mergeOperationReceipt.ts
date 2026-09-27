import type { ProjectOperation } from '@green/api-client';
import { operationActive } from '@/entities/operation/model/operationPresentation';

function compareTime(first?: string | null, second?: string | null) {
  if (!first || !second) return undefined;
  const a = Date.parse(first);
  const b = Date.parse(second);
  if (!Number.isFinite(a) || !Number.isFinite(b)) return undefined;
  if (a !== b) return Math.sign(a - b);
  // Python's UTC timestamps retain microseconds. Date.parse discards them,
  // which would make distinct rapid operations appear to have the same time.
  const remainder = (value: string) =>
    (value.match(/\.(\d+)(?:Z|[+-]\d{2}:\d{2})$/)?.[1] ?? '')
      .slice(3)
      .padEnd(6, '0');
  return Math.sign(Number(remainder(first)) - Number(remainder(second)));
}

/** Mutation receipts cannot move the observed operation backwards. */
export function mergeOperationReceipt(
  current: ProjectOperation | null | undefined,
  incoming: ProjectOperation,
  cancelTargetId?: string,
) {
  if (
    cancelTargetId &&
    (incoming.id !== cancelTargetId || current?.id !== cancelTargetId)
  )
    return current;
  if (!current) return incoming;
  if (current.id !== incoming.id) {
    const order = compareTime(incoming.created_at, current.created_at);
    return order != null && order <= 0 ? current : incoming;
  }
  // Completed, cancelled, failed and interrupted are immutable server states.
  if (!operationActive(current) && current.status !== incoming.status)
    return current;
  if (
    (current.status === 'cancelling' &&
      (incoming.status === 'queued' || incoming.status === 'running')) ||
    (current.status === 'running' && incoming.status === 'queued')
  )
    return current;
  const order = compareTime(incoming.updated_at, current.updated_at);
  if (order != null && order < 0) return current;
  if (
    current.status === incoming.status &&
    current.updated_at &&
    !incoming.updated_at
  )
    return current;
  return incoming;
}
