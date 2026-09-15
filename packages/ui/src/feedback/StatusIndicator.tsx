import type { FC, ReactNode } from 'react';
import { tv } from 'tailwind-variants';
import type { Tone } from '../foundations';

const status = tv({
  slots: {
    root: 'inline-flex items-center gap-2 text-xs leading-4 text-neutral-700',
    marker: 'size-2 rounded-full',
  },
  variants: {
    tone: {
      neutral: { marker: 'bg-neutral-400' },
      success: { marker: 'bg-(--success)' },
      warning: { marker: 'bg-(--warning)' },
      error: { marker: 'bg-(--error)' },
      info: { marker: 'bg-(--primary)' },
    },
  },
});

export interface StatusIndicatorProps {
  tone?: Tone;
  label: ReactNode;
  value?: ReactNode;
}

export const StatusIndicator: FC<StatusIndicatorProps> = ({
  tone = 'neutral',
  label,
  value,
}) => {
  const styles = status({ tone });
  return (
    <span className={styles.root()} data-tone={tone}>
      <span className={styles.marker()} aria-hidden="true" />
      <span>{label}</span>
      {value !== undefined && (
        <strong className="ml-auto font-mono font-normal text-(--ink-900)">
          {value}
        </strong>
      )}
    </span>
  );
};
