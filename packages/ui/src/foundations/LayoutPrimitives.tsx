import type { ComponentPropsWithRef, CSSProperties, FC } from 'react';
import { tv } from 'tailwind-variants';
import { cx } from './utils';
const divider = tv({
  base: 'block shrink-0 bg-(--border)',
  variants: {
    orientation: {
      horizontal: 'h-px w-full',
      vertical: 'h-5 w-px',
    },
  },
  defaultVariants: { orientation: 'horizontal' },
});

export interface DividerProps {
  orientation?: 'horizontal' | 'vertical';
}

export const Divider: FC<DividerProps> = ({ orientation = 'horizontal' }) => (
  <span className={divider({ orientation })} aria-hidden="true" />
);

export interface StackProps extends ComponentPropsWithRef<'div'> {
  gap?: number;
}

export const Stack: FC<StackProps> = ({
  gap = 4,
  className,
  style,
  ...props
}) => (
  <div
    className={cx('flex min-w-0 flex-col', className)}
    style={
      {
        '--stack-gap': `calc(var(--spacing) * ${gap})`,
        gap: `var(--stack-gap)`,
        ...style,
      } as CSSProperties
    }
    {...props}
  />
);

export interface InlineProps extends ComponentPropsWithRef<'div'> {
  gap?: number;
  align?: CSSProperties['alignItems'];
}

export const Inline: FC<InlineProps> = ({
  gap = 3,
  align = 'center',
  className,
  style,
  ...props
}) => (
  <div
    className={cx('flex min-w-0 flex-wrap', className)}
    style={
      {
        '--inline-gap': `calc(var(--spacing) * ${gap})`,
        gap: 'var(--inline-gap)',
        alignItems: align,
        ...style,
      } as CSSProperties
    }
    {...props}
  />
);

export interface GridProps extends ComponentPropsWithRef<'div'> {
  min?: number;
  gap?: number;
}

export const Grid: FC<GridProps> = ({
  min = 220,
  gap = 4,
  className,
  style,
  ...props
}) => (
  <div
    className={cx('grid min-w-0', className)}
    style={
      {
        '--grid-min': `${min}px`,
        '--grid-gap': `calc(var(--spacing) * ${gap})`,
        gridTemplateColumns:
          'repeat(auto-fit, minmax(min(100%, var(--grid-min)), 1fr))',
        gap: 'var(--grid-gap)',
        ...style,
      } as CSSProperties
    }
    {...props}
  />
);
