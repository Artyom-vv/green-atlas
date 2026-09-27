import type { FC, ReactNode } from 'react';

export interface EmptyStateProps {
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
}

export const EmptyState: FC<EmptyStateProps> = ({
  title,
  description,
  action,
}) => (
  <div className="flex min-h-36 min-w-0 flex-col items-center justify-center p-6 text-center text-neutral-500">
    <strong className="text-sm font-semibold text-(--ink-900)">{title}</strong>
    {!!description && (
      <p className="my-1.5 mb-4 max-w-[420px]">{description}</p>
    )}
    {action}
  </div>
);
