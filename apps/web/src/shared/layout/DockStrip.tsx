import { tv } from '@green/ui';
import type {
  ComponentPropsWithoutRef,
  CSSProperties,
  FC,
  ReactNode,
} from 'react';
import { DOCK_STRIP_HEIGHT } from './dockLayout';

const dockStrip = tv({
  base: 'flex h-(--dock-strip-height) min-h-(--dock-strip-height) min-w-0 shrink-0 items-center gap-1 px-2',
  variants: {
    edge: {
      top: 'border-t border-neutral-200',
      bottom: 'border-b border-neutral-200',
      none: '',
    },
  },
});

interface DockStripProps extends ComponentPropsWithoutRef<'div'> {
  actions?: ReactNode;
  edge?: 'top' | 'bottom' | 'none';
}

/** Compact dock controls share one row; only the adjoining content scrolls. */
export const DockStrip: FC<DockStripProps> = ({
  children,
  actions,
  edge = 'bottom',
  className,
  style,
  ...props
}) => (
  <div
    {...props}
    className={dockStrip({ edge, className })}
    style={
      {
        '--dock-strip-height': `${DOCK_STRIP_HEIGHT}px`,
        ...style,
      } as CSSProperties
    }
  >
    {children}
    {actions !== undefined && (
      <div className="ml-auto flex shrink-0 items-center gap-1">{actions}</div>
    )}
  </div>
);
