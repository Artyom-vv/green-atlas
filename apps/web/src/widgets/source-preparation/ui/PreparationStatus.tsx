import type { FC } from 'react';
import { Button, InlineMessage } from '@green/ui';
import { PREPARATION_MESSAGES } from '@/entities/source-data/config/sourceMessages';
import type { PreparationStatusProps } from './PreparationStatus.props';
export const PreparationStatus: FC<PreparationStatusProps> = ({
  preparationRecovery,
  statusUnknown,
  operation,
  statusQuery,
}) => (
  <>
    {preparationRecovery && (
      <InlineMessage tone="warning" title={PREPARATION_MESSAGES.recoveryTitle}>
        <div className="flex flex-wrap items-center gap-3">
          <span>{preparationRecovery.message}</span>
          <Button
            variant="secondary"
            loading={preparationRecovery.loading}
            onClick={preparationRecovery.onRetry}
          >
            {'actionLabel' in preparationRecovery
              ? preparationRecovery.actionLabel
              : PREPARATION_MESSAGES.checkProject}
          </Button>
        </div>
      </InlineMessage>
    )}
    {statusUnknown ? (
      <InlineMessage tone="error" title={PREPARATION_MESSAGES.unknownStatus}>
        {operation && (
          <p>
            {PREPARATION_MESSAGES.lastStage}:{' '}
            {operation.stage.replace(/\.+$/, '')}
          </p>
        )}
        <Button
          variant="secondary"
          loading={statusQuery.isFetching}
          onClick={() => void statusQuery.refetch()}
        >
          {PREPARATION_MESSAGES.retryStatusRead}
        </Button>
      </InlineMessage>
    ) : null}
  </>
);
