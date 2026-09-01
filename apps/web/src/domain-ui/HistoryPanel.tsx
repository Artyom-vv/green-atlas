import type { PlanHistoryState } from '@green/api-client';
import { Redo2, Undo2 } from 'lucide-react';
import { Button, EmptyState } from '@green/ui';

const changedAt = (value: string) => new Intl.DateTimeFormat('ru-RU', {
  day: '2-digit',
  month: 'short',
  hour: '2-digit',
  minute: '2-digit',
}).format(new Date(value));

export function HistoryPanel({ history, busy, onUndo, onRedo }: { history?: PlanHistoryState; busy?: boolean; onUndo: () => void; onRedo: () => void }) {
  const entries = history?.entries ?? [];
  return <section className="history-panel">
    <div className="history-panel__actions"><Button variant="secondary" icon={Undo2} disabled={!history?.can_undo || busy} onClick={onUndo}>Отменить</Button><Button variant="secondary" icon={Redo2} disabled={!history?.can_redo || busy} onClick={onRedo}>Повторить</Button></div>
    {entries.length ? <ol>{entries.map((entry) => <li key={entry.id} className={entry.applied ? 'is-applied' : 'is-undone'}><span><strong>{entry.label}</strong><small>{entry.author}</small></span><time dateTime={entry.created_at}>{changedAt(entry.created_at)}</time><b>{entry.applied ? 'Применено' : 'Отменено'}</b></li>)}</ol> : <EmptyState title="Изменений пока нет" />}
  </section>;
}
