import { Popover as BasePopover } from '@base-ui/react/popover';
import type { ComponentProps, FC } from 'react';
import { cx } from '../foundations/utils';

export const Popover = BasePopover.Root;

export const PopoverTrigger = BasePopover.Trigger;

export const PopoverClose = BasePopover.Close;

export const PopoverTitle = BasePopover.Title;

export const PopoverDescription = BasePopover.Description;

export interface PopoverPopupProps extends ComponentProps<
  typeof BasePopover.Popup
> {
  side?: ComponentProps<typeof BasePopover.Positioner>['side'];
  align?: ComponentProps<typeof BasePopover.Positioner>['align'];
  sideOffset?: number;
}

export const PopoverPopup: FC<PopoverPopupProps> = ({
  children,
  className,
  side = 'bottom',
  align = 'start',
  sideOffset = 4,
  ...props
}) => (
  <BasePopover.Portal>
    <BasePopover.Positioner
      side={side}
      align={align}
      sideOffset={sideOffset}
      className="z-menu max-w-(--available-width)"
    >
      <BasePopover.Popup
        {...props}
        className={(state) =>
          cx(
            'rounded-card max-h-(--available-height) max-w-full overflow-y-auto border border-solid border-neutral-300 bg-white p-4 text-sm text-neutral-800 shadow-lg outline-none',
            typeof className === 'function' ? className(state) : className,
          )
        }
      >
        {children}
      </BasePopover.Popup>
    </BasePopover.Positioner>
  </BasePopover.Portal>
);
