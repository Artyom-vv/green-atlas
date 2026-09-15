import { X } from 'lucide-react';
import type { FC, ReactNode } from 'react';
import { tv } from 'tailwind-variants';
import { IconButton } from '../controls';
import type { Tone } from '../foundations';

const message = tv({
  base: 'flex min-w-0 items-start justify-between gap-3 border-l-2 bg-neutral-100 p-3 text-xs leading-[18px] text-neutral-700',
  variants: {
    tone: {
      success: 'border-(--success)',
      warning: 'border-(--warning)',
      error: 'border-(--error)',
      info: 'border-(--primary)',
    },
  },
});

export interface InlineMessageProps {
  tone?: Exclude<Tone, 'neutral'>;
  title?: ReactNode;
  children: ReactNode;
  onDismiss?: () => void;
}

export const InlineMessage: FC<InlineMessageProps> = ({
  tone = 'info',
  title,
  children,
  onDismiss,
}) => (
  <div
    className={message({ tone })}
    data-tone={tone}
    role={tone === 'error' ? 'alert' : 'status'}
  >
    <div className="min-w-0">
      {!!title && (
        <strong className="mb-0.5 block text-(--ink-900)">{title}</strong>
      )}
      <div>{children}</div>
    </div>
    {!!onDismiss && (
      <IconButton
        icon={X}
        label="Закрыть"
        variant="ghost"
        controlSize="compact"
        onClick={onDismiss}
      />
    )}
  </div>
);
