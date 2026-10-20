import { Popover as BasePopover } from '@base-ui/react/popover';
import type { ComponentProps, FC } from 'react';
import { tv, type VariantProps } from 'tailwind-variants';
import { cx } from '../foundations/utils';

const popup = tv({
  base: 'rounded-card max-h-(--available-height) max-w-full overflow-y-auto border border-solid border-neutral-300 bg-white p-4 text-sm leading-5 text-neutral-800 outline-none',
  variants: {
    size: {
      content: '',
      compact: 'w-popover-compact',
      default: 'w-popover-default',
    },
  },
  defaultVariants: { size: 'content' },
});

export type PopoverSize = NonNullable<VariantProps<typeof popup>['size']>;

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
  alignOffset?: number;
  size?: PopoverSize;
}

export const PopoverPopup: FC<PopoverPopupProps> = ({
  children,
  className,
  side = 'bottom',
  align = 'start',
  sideOffset = 4,
  alignOffset = 0,
  size = 'content',
  ...props
}) => (
  <BasePopover.Portal>
    <BasePopover.Positioner
      side={side}
      align={align}
      sideOffset={sideOffset}
      alignOffset={alignOffset}
      className="z-menu max-w-(--available-width)"
    >
      <BasePopover.Popup
        {...props}
        className={(state) =>
          cx(
            popup({ size }),
            typeof className === 'function' ? className(state) : className,
          )
        }
      >
        {children}
      </BasePopover.Popup>
    </BasePopover.Positioner>
  </BasePopover.Portal>
);
