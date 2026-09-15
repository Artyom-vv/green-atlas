import type { ComponentPropsWithRef, FC, ReactNode } from 'react';
import { tv } from 'tailwind-variants';
import { cx } from '../foundations/utils';

const header = tv({
  base: 'flex min-h-12 shrink-0 items-center justify-between gap-4 border-b border-neutral-200 px-4 py-1.5',
});

export interface SurfaceHeaderProps extends ComponentPropsWithRef<'header'> {
  children: ReactNode;
}

export const SurfaceHeader: FC<SurfaceHeaderProps> = ({
  children,
  className,
  ...props
}) => (
  <header className={cx(header(), className)} {...props}>
    {children}
  </header>
);

export const PanelHeader = SurfaceHeader;

export interface SurfaceBodyProps extends ComponentPropsWithRef<'div'> {
  children: ReactNode;
}

export const SurfaceBody: FC<SurfaceBodyProps> = ({
  children,
  className,
  ...props
}) => (
  <div className={cx('min-w-0 p-4', className)} {...props}>
    {children}
  </div>
);

export const PanelBody = SurfaceBody;

export interface SurfaceActionsProps extends ComponentPropsWithRef<'div'> {
  children: ReactNode;
}

export const SurfaceActions: FC<SurfaceActionsProps> = ({
  children,
  className,
  ...props
}) => (
  <div
    className={cx(
      'flex min-w-0 flex-wrap items-center justify-end gap-2',
      className,
    )}
    {...props}
  >
    {children}
  </div>
);

export const PanelActions = SurfaceActions;
