import { runStatus } from '@/features/assistant/model/autonomous/presentation';
import type { AutonomousRunController } from '@/features/assistant/model/autonomous/useAutonomousRunController';
import { cx } from '@green/ui';
import type { FC } from 'react';
export interface RunStatusProps extends Pick<
  AutonomousRunController,
  | 'lifecycle'
  | 'creating'
  | 'restoring'
  | 'failedRestore'
  | 'run'
  | 'reviewStatus'
  | 'incomplete'
  | 'committed'
  | 'result'
> {}
export const RunStatus: FC<RunStatusProps> = ({
  lifecycle,
  creating,
  restoring,
  failedRestore,
  run,
  reviewStatus,
  incomplete,
  committed,
  result,
}) => (
  <>
    <div
      role="status"
      aria-label="Состояние запуска"
      aria-live="polite"
      aria-busy={lifecycle.executing || creating || restoring}
      className="flex min-h-8 shrink-0 items-center gap-2 px-3 text-xs text-neutral-600"
    >
      <span
        className={cx(
          'size-2 shrink-0 rounded-full',
          lifecycle.executing || creating || restoring
            ? 'animate-pulse bg-blue-600 motion-reduce:animate-none'
            : failedRestore || run?.state.status === 'failed'
              ? 'bg-error'
              : reviewStatus || incomplete
                ? 'bg-warning'
                : committed
                  ? 'bg-green-600'
                  : 'bg-neutral-400',
        )}
        aria-hidden="true"
      />
      {restoring
        ? 'Восстанавливаю запуск'
        : failedRestore
          ? 'Сохранённый запуск не загружен'
          : creating
            ? 'Разбираю задачу'
            : lifecycle.phase === 'uncertain'
              ? 'Проверяем результат запроса'
              : (reviewStatus ?? runStatus(run, result))}
    </div>
  </>
);
