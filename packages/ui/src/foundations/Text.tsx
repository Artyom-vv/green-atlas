import type { FC, HTMLAttributes } from 'react';
import { tv, type VariantProps } from 'tailwind-variants';
import { cx } from './utils';
export type Tone = 'neutral' | 'success' | 'warning' | 'error' | 'info';

const text = tv({
  base: 'm-0 text-(--ink-900)',
  variants: {
    variant: {
      body: 'text-sm leading-5',
      label: 'text-xs leading-4 font-medium',
      caption: 'text-xs leading-4 text-(--ink-500)',
      heading: 'text-base leading-5 font-semibold',
      pageHeading: 'text-xl leading-7 font-semibold',
    },
    tone: {
      default: '',
      muted: 'text-(--ink-500)',
      primary: 'text-(--primary)',
    },
    mono: { true: 'font-mono tabular-nums', false: '' },
  },
  defaultVariants: { variant: 'body', tone: 'default', mono: false },
});

export type TextVariant = NonNullable<VariantProps<typeof text>['variant']>;

export interface TextProps extends HTMLAttributes<HTMLElement> {
  as?: 'span' | 'p' | 'strong' | 'small' | 'h1' | 'h2' | 'h3';
  variant?: TextVariant;
  tone?: NonNullable<VariantProps<typeof text>['tone']>;
  mono?: boolean;
}

export const Text: FC<TextProps> = ({
  as: Tag = 'span',
  variant = 'body',
  tone = 'default',
  mono = false,
  className,
  ...props
}) => (
  <Tag className={cx(text({ variant, tone, mono }), className)} {...props} />
);
