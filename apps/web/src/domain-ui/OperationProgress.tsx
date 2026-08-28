import { useEffect, useMemo, useState } from 'react';
import type { ProjectOperation } from '@green/api-client';
import { Button, InlineMessage, Progress } from '@green/ui';

const formatDuration = (milliseconds: number) => {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000));
  if (seconds < 60) return `${seconds} с`;
  return `${Math.floor(seconds / 60)} мин ${seconds % 60} с`;
};

export function OperationProgress({ operation, title, onCancel, onRetry, onDownloadSource, actionBusy = false }: { operation: ProjectOperation; title: string; onCancel?: () => void; onRetry?: () => void; onDownloadSource?: () => void; actionBusy?: boolean }) {
  const [now, setNow] = useState(() => Date.now());
  const finished = ['completed', 'failed', 'cancelled', 'interrupted'].includes(operation.status);
  const stoppable = operation.status === 'queued' || operation.status === 'running' || operation.status === 'cancelling';
  const retryable = operation.status === 'failed' || operation.status === 'cancelled' || operation.status === 'interrupted';
  useEffect(() => {
    if (finished) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [finished]);
  const elapsed = useMemo(() => {
    const start = Date.parse(operation.started_at ?? operation.created_at ?? operation.updated_at ?? new Date(now).toISOString());
    const end = finished && operation.completed_at ? Date.parse(operation.completed_at) : now;
    return Number.isFinite(start) && Number.isFinite(end) ? formatDuration(end - start) : undefined;
  }, [finished, now, operation.completed_at, operation.created_at, operation.started_at, operation.updated_at]);
  const count = operation.processed_items !== null && operation.processed_items !== undefined
    ? `${operation.processed_items.toLocaleString('ru-RU')}${operation.total_items !== null && operation.total_items !== undefined ? ` из ${operation.total_items.toLocaleString('ru-RU')}` : ''}${operation.progress_unit ? ` ${operation.progress_unit}` : ''}`
    : undefined;
  return (
    <section className="operation-progress" aria-live="polite" aria-label={title}>
      <header>
        <strong>{title}</strong>
      </header>
      <Progress value={operation.progress_mode === 'determinate' ? operation.progress : undefined} label={operation.stage} />
      {count || elapsed ? <div className="operation-progress__facts">{count ? <span>{count}</span> : null}{elapsed ? <span>Прошло {elapsed}</span> : null}</div> : null}
      {operation.error ? <InlineMessage tone={operation.status === 'interrupted' ? 'warning' : 'error'}>{operation.error.message}</InlineMessage> : null}
      {stoppable && onCancel ? <div className="operation-progress__actions"><Button controlSize="compact" variant="secondary" disabled={operation.status === 'cancelling' || actionBusy} onClick={onCancel}>{operation.status === 'cancelling' ? 'Останавливаем' : 'Остановить'}</Button></div> : null}
      {retryable && (onRetry || onDownloadSource) ? <div className="operation-progress__actions">{onDownloadSource ? <Button controlSize="compact" variant="ghost" disabled={actionBusy} onClick={onDownloadSource}>Скачать исходный DXF</Button> : null}{onRetry ? <Button controlSize="compact" variant="secondary" loading={actionBusy} onClick={onRetry}>Запустить повторно</Button> : null}</div> : null}
    </section>
  );
}
