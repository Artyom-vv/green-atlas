import type { ComponentPropsWithRef, FC, ReactNode } from 'react';
import { cx } from '../foundations/utils';
export interface ActionBarProps extends ComponentPropsWithRef<'footer'> {
  children: ReactNode;
}
export const ActionBar: FC<ActionBarProps> = ({
  children,
  className,
  ...props
}) => (
  <footer
    className={cx(
      'flex min-w-0 flex-wrap items-center justify-end gap-2 border-t border-(--border) bg-(--surface) px-4 py-3 md:px-8',
      className,
    )}
    {...props}
  >
    {children}
  </footer>
);
