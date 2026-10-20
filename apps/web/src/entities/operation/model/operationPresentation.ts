import type { ProjectOperation } from '@green/api-client';
import { CLOCK_INTERVAL_MS, formatDuration } from '@/shared/time/duration';

type OperationStatus = ProjectOperation['status'];

const ACTIVE_STATUSES: readonly OperationStatus[] = [
  'queued',
  'running',
  'cancelling',
];
const RETRYABLE_STATUSES: readonly OperationStatus[] = [
  'failed',
  'cancelled',
  'interrupted',
];

export const OPERATION_CLOCK_INTERVAL_MS = CLOCK_INTERVAL_MS;

export function operationActive(operation?: ProjectOperation | null): boolean {
  return Boolean(operation && ACTIVE_STATUSES.includes(operation.status));
}

export function operationRetryable(operation: ProjectOperation): boolean {
  return RETRYABLE_STATUSES.includes(operation.status);
}

export function operationElapsed(
  operation: ProjectOperation,
  now: number,
): string | undefined {
  const startedAt =
    operation.started_at ?? operation.created_at ?? operation.updated_at;
  const start = startedAt ? Date.parse(startedAt) : now;
  const end =
    !operationActive(operation) && operation.completed_at
      ? Date.parse(operation.completed_at)
      : now;
  return Number.isFinite(start) && Number.isFinite(end)
    ? formatDuration(end - start)
    : undefined;
}

export function operationCount(
  operation: ProjectOperation,
): string | undefined {
  if (operation.processed_items == null) return undefined;
  const count = operation.processed_items.toLocaleString('ru-RU');
  const total =
    operation.total_items == null
      ? ''
      : ` из ${operation.total_items.toLocaleString('ru-RU')}`;
  const unit = operation.progress_unit ? ` ${operation.progress_unit}` : '';
  return `${count}${total}${unit}`;
}
