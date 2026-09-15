import type { ComponentPropsWithRef, FC, ReactNode } from 'react';
import { tv } from 'tailwind-variants';
import { cx } from '../foundations/utils';
import { defaultHeader, type HeaderMeta } from './surfaceHeader';
import { SurfaceBody } from './SurfaceParts';
const panel = tv({ base: 'min-w-0 bg-white' });

export interface PanelProps
  extends Omit<ComponentPropsWithRef<'section'>, 'title'>, HeaderMeta {
  children: ReactNode;
  /** Replaces the default padded body, for a surface that owns its scroll area. */
  body?: ReactNode;
}

export const Panel: FC<PanelProps> = ({
  header: headerOverride,
  title,
  description,
  actions,
  className,
  children,
  body,
  ...props
}) => (
  <section className={cx(panel(), className)} {...props}>
    {headerOverride === null
      ? null
      : headerOverride === undefined
        ? defaultHeader({ title, description, actions })
        : headerOverride}
    {body === undefined ? <SurfaceBody>{children}</SurfaceBody> : body}
  </section>
);
