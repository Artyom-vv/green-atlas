import type { FC } from 'react';
import type { PlanHistoryState } from '@green/api-client';
import { Check, Redo2, Undo2 } from 'lucide-react';
import { Button, Icon } from '@green/ui';
import { ResultPanel } from '@/shared/ui/results/ResultPanel';
import { RESULTS_CONTENT_INSET } from '@/shared/ui/results/resultsLayout';

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
  embedded?: boolean;
}

export const HistoryPanel: FC<HistoryPanelProps> = ({
  history,
  busy,
  onUndo,
  onRedo,
  embedded,
}) => {
  const entries = history?.entries ?? [];
  return (
    <ResultPanel
      embedded={embedded}
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
        <ol className={`m-0 list-none ${RESULTS_CONTENT_INSET}`}>
          {entries.map((entry) => (
            <li
              key={entry.id}
              className="grid grid-cols-[minmax(0,1fr)_7.5rem_6rem] items-center gap-4 border-b border-neutral-200 py-3 last:border-b-0"
            >
              <span className="grid min-w-0 gap-1">
                <strong className="font-medium wrap-anywhere">
                  {entry.label}
                </strong>
                <small className="text-xs wrap-anywhere text-neutral-600">
                  {entry.author}
                </small>
              </span>
              <time
                className="text-right text-xs text-neutral-600 tabular-nums"
                dateTime={entry.created_at}
              >
                {historyTime.format(new Date(entry.created_at))}
              </time>
              <span
                className={`inline-flex items-center justify-end gap-1 text-xs ${entry.applied ? 'text-green-700' : 'text-neutral-600'}`}
              >
                <Icon icon={entry.applied ? <Check /> : <Undo2 />} size={12} />
                {entry.applied ? 'Применено' : 'Отменено'}
              </span>
            </li>
          ))}
        </ol>
      ) : (
        <p
          role="status"
          className={`${RESULTS_CONTENT_INSET} py-4 text-xs text-neutral-600`}
        >
          {history ? 'Изменений пока нет' : 'История пока недоступна'}
        </p>
      )}
    </ResultPanel>
  );
};
