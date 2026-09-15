import { Menu as BaseMenu } from '@base-ui/react/menu';
import type { ComponentProps, FC } from 'react';
import { cx } from '../foundations/utils';

export interface MenuPopupProps extends ComponentProps<typeof BaseMenu.Popup> {
  side?: ComponentProps<typeof BaseMenu.Positioner>['side'];
  align?: ComponentProps<typeof BaseMenu.Positioner>['align'];
  sideOffset?: number;
}

export const MenuPopup: FC<MenuPopupProps> = ({
  children,
  className,
  side = 'bottom',
  align = 'start',
  sideOffset = 4,
  ...props
}) => (
  <BaseMenu.Portal>
    <BaseMenu.Positioner
      side={side}
      align={align}
      sideOffset={sideOffset}
      className="z-menu"
    >
      <BaseMenu.Popup
        {...props}
        className={(state) =>
          cx(
            'rounded-card max-h-(--available-height) min-w-40 overflow-y-auto border border-solid border-neutral-300 bg-white p-1 text-sm text-neutral-800 shadow-lg outline-none',
            typeof className === 'function' ? className(state) : className,
          )
        }
      >
        {children}
      </BaseMenu.Popup>
    </BaseMenu.Positioner>
  </BaseMenu.Portal>
);
