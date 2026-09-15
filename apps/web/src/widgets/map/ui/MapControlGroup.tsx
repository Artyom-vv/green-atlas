import { ControlProvider, Toolbar, cx } from '@green/ui';
import type { ComponentProps, FC } from 'react';

export interface MapControlGroupProps extends ComponentProps<typeof Toolbar> {
  orientation?: 'horizontal' | 'vertical';
}

export const MapControlGroup: FC<MapControlGroupProps> = ({
  children,
  orientation = 'horizontal',
  label = 'Управление картой',
  className,
}) => (
  <ControlProvider size="compact">
    <Toolbar
      orientation={orientation}
      label={label}
      className={cx(
        'rounded-card shadow-sm',
        orientation === 'vertical' ? 'flex-col flex-nowrap' : undefined,
        className,
      )}
    >
      {children}
    </Toolbar>
  </ControlProvider>
);
