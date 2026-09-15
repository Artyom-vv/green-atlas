import { AssistantCard } from '@/features/assistant/ui/shared/AssistantCard';
import { Button, Text } from '@green/ui';
import type { FC } from 'react';
import type { RunFeedbackProps } from './RunFeedback.types';
export interface RetryFeedbackProps extends Pick<
  RunFeedbackProps,
  | 'stale'
  | 'retryBlocked'
  | 'busy'
  | 'decisionDisabled'
  | 'retry'
  | 'existingUnverified'
  | 'zoneBlocked'
> {}
export const RetryFeedback: FC<RetryFeedbackProps> = ({
  stale,
  retryBlocked,
  busy,
  decisionDisabled,
  retry,
  existingUnverified,
  zoneBlocked,
}) => (
  <>
    {!!(stale && !retryBlocked) && (
      <AssistantCard role="alert" tone="error">
        <Text as="p" variant="body" className="m-0 wrap-anywhere">
          Проект изменился после расчёта. Предложение устарело — выполните новый
          расчёт.
        </Text>
        <Button
          type="button"
          variant="secondary"
          loading={busy}
          disabled={decisionDisabled}
          onClick={() => void retry()}
        >
          Пересчитать предложение
        </Button>
      </AssistantCard>
    )}
    {!!(existingUnverified && !retryBlocked) && (
      <AssistantCard role="alert" tone="error">
        <Text as="p" variant="body" className="m-0 wrap-anywhere">
          Подробности изменения не подтверждены. Выполните новый расчёт.
        </Text>
        <Button
          type="button"
          variant="secondary"
          loading={busy}
          disabled={decisionDisabled}
          onClick={() => void retry()}
        >
          Пересчитать предложение
        </Button>
      </AssistantCard>
    )}
    {!!(zoneBlocked && !retryBlocked) && (
      <AssistantCard tone="error">
        <Text as="p" variant="body" className="m-0 wrap-anywhere">
          Участок нельзя изменить этим предложением.
        </Text>
        <Button
          type="button"
          variant="secondary"
          loading={busy}
          disabled={decisionDisabled}
          onClick={() => void retry()}
        >
          Пересчитать предложение
        </Button>
      </AssistantCard>
    )}
  </>
);
