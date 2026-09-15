import type { FC, ReactNode } from 'react';
import { Button, InlineMessage } from '@green/ui';

export interface CommandRecoveryProps {
  title: ReactNode;
  children?: ReactNode;
  actionLabel?: ReactNode;
  loading?: boolean;
  onRetry: () => void;
}

/** Re-reading a command result is a distinct action from repeating its write. */
export const CommandRecovery: FC<CommandRecoveryProps> = ({
  title,
  children,
  actionLabel = 'Обновить данные',
  loading,
  onRetry,
}) => (
  <InlineMessage tone="warning" title={title}>
    <div className="grid min-w-0 gap-2">
      {children}
      <Button variant="secondary" loading={loading} onClick={onRetry}>
        {actionLabel}
      </Button>
    </div>
  </InlineMessage>
);
