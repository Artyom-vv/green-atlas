import type { FC } from 'react';
import type { PlanHistoryState } from '@green/api-client';
import { Check, Redo2, Undo2 } from 'lucide-react';
import { Button, EmptyState, Icon } from '@green/ui';
import { ResultPanel } from '@/shared/ui/results/ResultPanel';

const historyTime = new Intl.DateTimeFormat('ru-RU', {
  day: '2-digit',
  month: 'short',
  hour: '2-digit',
  minute: '2-digit',
});

export interface HistoryPanelProps {
  history?: PlanHistoryState;
  busy?: boolean;
  onUndo: () => void;
  onRedo: () => void;
}

export const HistoryPanel: FC<HistoryPanelProps> = ({
  history,
  busy,
  onUndo,
  onRedo,
}) => {
  const entries = history?.entries ?? [];
  return (
    <ResultPanel
      title="История изменений"
      label="История изменений"
      count={history ? `Записей: ${entries.length}` : undefined}
      actions={
        <>
          <Button
            variant="secondary"
            startIcon={<Undo2 />}
            title={
              history?.undo_label
                ? `Отменить: ${history.undo_label}`
                : undefined
            }
            disabled={!history?.can_undo || busy}
            onClick={onUndo}
          >
            Отменить
          </Button>
          <Button
            variant="secondary"
            startIcon={<Redo2 />}
            title={
              history?.redo_label
                ? `Повторить: ${history.redo_label}`
                : undefined
            }
            disabled={!history?.can_redo || busy}
            onClick={onRedo}
          >
            Повторить
          </Button>
        </>
      }
    >
      {entries.length ? (
        <ol className="m-0 list-none p-0">
          {entries.map((entry) => (
            <li
              key={entry.id}
              className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-1 border-0 border-b border-solid border-neutral-200 py-2 last:border-b-0 @min-[640px]/results:grid-cols-[minmax(0,1fr)_auto_auto]"
            >
              <span className="row-span-2 flex min-w-0 flex-col gap-1 @min-[640px]/results:row-span-1">
                <strong className="font-medium wrap-anywhere">
                  {entry.label}
                </strong>
                <small className="text-xs text-neutral-600">
                  {entry.author}
                </small>
              </span>
              <time
                className="justify-self-end text-xs whitespace-nowrap text-neutral-600 tabular-nums"
                dateTime={entry.created_at}
              >
                {historyTime.format(new Date(entry.created_at))}
              </time>
              <span
                className={`rounded-control inline-flex min-w-24 items-center justify-center gap-1 justify-self-end px-2 py-1 text-xs ${entry.applied ? 'bg-success-soft text-green-700' : 'bg-neutral-100 text-neutral-600'}`}
              >
                <Icon icon={entry.applied ? <Check /> : <Undo2 />} size={12} />
                {entry.applied ? 'Применено' : 'Отменено'}
              </span>
            </li>
          ))}
        </ol>
      ) : (
        <EmptyState
          title={history ? 'Изменений пока нет' : 'История пока недоступна'}
        />
      )}
    </ResultPanel>
  );
};
