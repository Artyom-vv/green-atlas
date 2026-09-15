import { ScrollArea as BaseScrollArea } from '@base-ui/react/scroll-area';
import type { ComponentPropsWithRef, FC } from 'react';
import { tv } from 'tailwind-variants';
import { cx } from '../foundations/utils';

const scrollbar = tv({
  base: 'absolute z-1 flex touch-none p-0.5 select-none data-[hidden]:hidden',
  variants: {
    orientation: {
      vertical: 'inset-y-0 right-0 w-2',
      horizontal: 'inset-x-0 bottom-0 h-2',
    },
  },
});

export interface ScrollAreaProps extends ComponentPropsWithRef<'div'> {
  viewportClassName?: string;
  contentClassName?: string;
  viewportProps?: Omit<ComponentPropsWithRef<'div'>, 'children' | 'className'>;
}

/** Only content belongs here; headers and actions remain sibling surfaces. */
export const ScrollArea: FC<ScrollAreaProps> = ({
  children,
  className,
  viewportClassName,
  contentClassName,
  viewportProps,
  ...props
}) => (
  <BaseScrollArea.Root
    {...props}
    data-slot="scroll-area"
    className={cx('relative min-h-0 min-w-0', className)}
  >
    <BaseScrollArea.Viewport
      {...viewportProps}
      data-slot="scroll-viewport"
      className={cx(
        'h-full w-full overscroll-contain rounded-[inherit] outline-none focus-visible:ring-2 focus-visible:ring-blue-400 focus-visible:ring-inset',
        viewportClassName,
      )}
    >
      <BaseScrollArea.Content className={cx('min-w-0', contentClassName)}>
        {children}
      </BaseScrollArea.Content>
    </BaseScrollArea.Viewport>
    {(['vertical', 'horizontal'] as const).map((orientation) => (
      <BaseScrollArea.Scrollbar
        key={orientation}
        orientation={orientation}
        className={scrollbar({ orientation })}
      >
        <BaseScrollArea.Thumb className="relative flex-1 cursor-grab rounded-full bg-neutral-400 hover:bg-neutral-500 active:cursor-grabbing" />
      </BaseScrollArea.Scrollbar>
    ))}
  </BaseScrollArea.Root>
);
