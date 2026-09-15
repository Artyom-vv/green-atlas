import { tv, type VariantProps } from '@green/ui';
import type { ComponentProps, FC } from 'react';

const card = tv({
  base: 'rounded-card grid min-w-0 shrink-0 gap-2 border border-solid p-3',
  variants: {
    tone: {
      neutral: 'border-neutral-200 bg-white',
      muted: 'border-neutral-200 bg-neutral-100',
      info: 'border-blue-200 bg-blue-100',
      warning: 'border-warning/30 bg-warning-soft',
      error: 'border-error/30 bg-error-soft',
      success: 'border-green-200 bg-green-100',
    },
  },
  defaultVariants: { tone: 'neutral' },
});

export interface AssistantCardProps
  extends ComponentProps<'section'>, VariantProps<typeof card> {}

export const AssistantCard: FC<AssistantCardProps> = ({
  tone = 'neutral',
  className,
  ...props
}) => <section className={card({ tone, className })} {...props} />;
