import { AssistantCard } from '@/features/assistant/ui/shared/AssistantCard';
import { Button, Text } from '@green/ui';
import type { FC } from 'react';
import type { RunTaskProps } from './RunTask.types';
export interface RestoreTaskProps extends Pick<
  RunTaskProps,
  'failedRestore' | 'restoring' | 'decisionDisabled' | 'retryRestore'
> {}
export const RestoreTask: FC<RestoreTaskProps> = ({
  failedRestore,
  restoring,
  decisionDisabled,
  retryRestore,
}) => (
  <>
    {!!failedRestore && (
      <AssistantCard
        aria-label="Восстановление сохранённого запуска"
        tone="muted"
      >
        <Text as="h3" variant="heading" className="m-0 wrap-anywhere">
          Не удалось загрузить запуск
        </Text>
        <Text role="alert" as="p" variant="body" className="m-0 wrap-anywhere">
          {failedRestore.message}
        </Text>
        <Text as="p" variant="body" className="m-0 wrap-anywhere">
          Повторите загрузку, чтобы продолжить с сохранённого состояния.
        </Text>
        <Button
          type="button"
          variant="secondary"
          loading={restoring}
          disabled={decisionDisabled}
          onClick={retryRestore}
        >
          Повторить загрузку запуска
        </Button>
      </AssistantCard>
    )}
  </>
);
