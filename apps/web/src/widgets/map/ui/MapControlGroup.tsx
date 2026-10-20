import { Toolbar, cx } from '@green/ui';
import { CanvasControlGroup } from '@/shared/ui/CanvasControls';
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
  <CanvasControlGroup
    orientation={orientation}
    label={label}
    className={cx(
      orientation === 'vertical' ? 'flex-col flex-nowrap' : undefined,
      className,
    )}
  >
    {children}
  </CanvasControlGroup>
);
