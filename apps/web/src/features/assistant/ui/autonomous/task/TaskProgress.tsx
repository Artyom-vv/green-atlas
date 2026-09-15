import { currentActivity } from '@/features/assistant/model/autonomous/presentation';
import { AssistantCard } from '@/features/assistant/ui/shared/AssistantCard';
import { Button, Text } from '@green/ui';
import { Circle, LoaderCircle, Square } from 'lucide-react';
import type { FC } from 'react';
import type { RunTaskProps } from './RunTask.types';
export interface TaskProgressProps extends Pick<
  RunTaskProps,
  | 'active'
  | 'run'
  | 'lifecycle'
  | 'busy'
  | 'decisionDisabled'
  | 'continueRun'
  | 'stopping'
  | 'canCancel'
  | 'restoring'
  | 'cancel'
> {}
export const TaskProgress: FC<TaskProgressProps> = ({
  active,
  run,
  lifecycle,
  busy,
  decisionDisabled,
  continueRun,
  stopping,
  canCancel,
  restoring,
  cancel,
}) => (
  <>
    {!!(active && run) && (
      <AssistantCard
        role="status"
        tone="info"
        className="flex flex-wrap items-start gap-2"
      >
        {lifecycle.canContinue && !busy ? (
          <Circle size={18} aria-hidden="true" />
        ) : (
          <LoaderCircle
            size={18}
            aria-hidden="true"
            className="animate-spin motion-reduce:animate-none"
          />
        )}
        <div>
          <Text as="strong" variant="label" className="m-0 wrap-anywhere">
            {lifecycle.canContinue && !busy
              ? 'Задание готово к расчёту'
              : currentActivity(run)}
          </Text>
          <Text as="p" variant="body" className="m-0 wrap-anywhere">
            {lifecycle.canContinue && !busy
              ? 'Продолжите расчёт с сохранённого задания.'
              : 'Можно закрыть панель и вернуться к запуску позже.'}
          </Text>
          {!!(lifecycle.canContinue && !busy) && (
            <Button
              type="button"
              variant="primary"
              disabled={decisionDisabled}
              onClick={() => void continueRun()}
            >
              Продолжить расчёт
            </Button>
          )}
          <Button
            type="button"
            variant="secondary"
            icon={<Square />}
            loading={stopping}
            disabled={!canCancel || restoring}
            onClick={() => void cancel()}
          >
            Остановить запуск
          </Button>
        </div>
      </AssistantCard>
    )}
  </>
);
