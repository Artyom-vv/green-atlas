import type { ReactNode } from 'react';
import { Disclosure } from '@green/ui';

export function SourceSection({
  number,
  title,
  children,
  description,
  defaultOpen = true,
}: {
  number: string;
  title: string;
  children: ReactNode;
  description?: ReactNode;
  defaultOpen?: boolean;
}) {
  return (
    <Disclosure
      variant="panel"
      label={title}
      defaultOpen={defaultOpen}
      title={
        <span
          role="heading"
          aria-level={2}
          className="text-sm font-semibold text-neutral-800"
        >
          {title}
        </span>
      }
      description={description}
      startIcon={
        <span className="text-xs text-neutral-500 tabular-nums">{number}</span>
      }
      contentClassName="space-y-4"
    >
      {children}
    </Disclosure>
  );
}
