import type { ComponentPropsWithRef, FC, ReactNode } from 'react';
import { tv } from 'tailwind-variants';
import { cx } from '../foundations/utils';
import { defaultHeader, type HeaderMeta } from './surfaceHeader';
const surface = tv({
  base: 'min-w-0 rounded-(--radius-panel) border border-solid border-(--border) bg-(--surface)',
});

export interface SurfaceProps
  extends Omit<ComponentPropsWithRef<'div'>, 'title'>, HeaderMeta {
  children: ReactNode;
}

export const Surface: FC<SurfaceProps> = ({
  header: headerOverride,
  title,
  description,
  actions,
  className,
  children,
  ...props
}) => (
  <div className={cx(surface(), className)} {...props}>
    {headerOverride === null
      ? null
      : headerOverride === undefined
        ? defaultHeader({ title, description, actions })
        : headerOverride}
    {children}
  </div>
);
