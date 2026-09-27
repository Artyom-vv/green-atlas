import { type ComponentPropsWithRef, type FC, type ReactNode } from 'react';
import { cx } from '../foundations/utils';

export interface FieldGridProps extends ComponentPropsWithRef<'div'> {
  minWidth?: number;
  children: ReactNode;
}

export const FieldGrid: FC<FieldGridProps> = ({
  minWidth = 140,
  children,
  className,
  style,
  ...props
}) => (
  <div
    className={cx('grid min-w-0 gap-4', className)}
    style={
      {
        '--field-grid-min': `${minWidth}px`,
        gridTemplateColumns:
          'repeat(auto-fit, minmax(min(100%, var(--field-grid-min)), 1fr))',
        ...style,
      } as React.CSSProperties
    }
    {...props}
  >
    {children}
  </div>
);
