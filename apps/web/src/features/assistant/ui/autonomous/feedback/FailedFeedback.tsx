import { failureText } from '@/features/assistant/model/autonomous/presentation';
import { AssistantCard } from '@/features/assistant/ui/shared/AssistantCard';
import { Button, Text } from '@green/ui';
import type { FC } from 'react';
import type { RunFeedbackProps } from './RunFeedback.types';
export interface FailedFeedbackProps extends Pick<
  RunFeedbackProps,
  | 'run'
  | 'failureRemedy'
  | 'retryBlocked'
  | 'busy'
  | 'decisionDisabled'
  | 'retry'
> {}
export const FailedFeedback: FC<FailedFeedbackProps> = ({
  run,
  failureRemedy,
  retryBlocked,
  busy,
  decisionDisabled,
  retry,
}) => (
  <>
    {(run?.state.status === 'failed' || run?.state.status === 'cancelled') && (
      <AssistantCard
        role={run.state.status === 'failed' ? 'alert' : 'status'}
        tone={run.state.status === 'failed' ? 'error' : 'muted'}
      >
        <Text as="p" variant="body" className="m-0 wrap-anywhere">
          {run.state.status === 'failed'
            ? failureText(run)
            : 'Запуск остановлен. Можно повторить задачу с актуальным планом.'}
        </Text>
        {!!(
          run.state.status === 'failed' &&
          failureRemedy &&
          failureRemedy !== failureText(run)
        ) && (
          <Text as="p" variant="body" className="m-0 wrap-anywhere">
            {failureRemedy}
          </Text>
        )}
        {!retryBlocked && (
          <Button
            type="button"
            variant="secondary"
            loading={busy}
            disabled={decisionDisabled}
            onClick={() => void retry()}
          >
            Повторить расчёт
          </Button>
        )}
      </AssistantCard>
    )}
  </>
);
