import { AssistantCard } from '@/features/assistant/ui/shared/AssistantCard';
import { Button, Text } from '@green/ui';
import { Square } from 'lucide-react';
import type { FC } from 'react';
import type { RunFeedbackProps } from './RunFeedback.types';
export interface RecoveryFeedbackProps extends Pick<
  RunFeedbackProps,
  | 'recoveryPending'
  | 'outcomeUnknown'
  | 'busy'
  | 'decisionDisabled'
  | 'refreshOutcome'
  | 'canCancel'
  | 'active'
  | 'approval'
  | 'stopping'
  | 'cancel'
  | 'status'
  | 'error'
> {}
export const RecoveryFeedback: FC<RecoveryFeedbackProps> = ({
  recoveryPending,
  outcomeUnknown,
  busy,
  decisionDisabled,
  refreshOutcome,
  canCancel,
  active,
  approval,
  stopping,
  cancel,
  status,
  error,
}) => (
  <>
    {!!(recoveryPending || outcomeUnknown) && (
      <AssistantCard aria-label="Проверка результата запроса" tone="muted">
        <Text as="p" variant="body" className="m-0 wrap-anywhere">
          {recoveryPending
            ? 'Проверяем, успел ли сервер выполнить запрос. Продолжить можно после обновления состояния.'
            : 'Обновите состояние, чтобы проверить подтверждение сохранения.'}
        </Text>
        <Button
          type="button"
          variant="secondary"
          loading={busy}
          disabled={decisionDisabled}
          onClick={() => void refreshOutcome()}
        >
          Обновить состояние
        </Button>
      </AssistantCard>
    )}
    {!!(canCancel && !active && !approval) && (
      <Button
        type="button"
        variant="secondary"
        icon={<Square />}
        loading={stopping}
        disabled={decisionDisabled}
        onClick={() => void cancel()}
      >
        {status === 'waiting_approval'
          ? 'Отклонить предложение'
          : 'Остановить запуск'}
      </Button>
    )}
    {!!error && (
      <AssistantCard role="alert" tone="error">
        <Text as="p" variant="body" className="m-0 wrap-anywhere">
          {error}
        </Text>
      </AssistantCard>
    )}
  </>
);
