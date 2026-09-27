import { tv } from 'tailwind-variants';
export const resizeHandle = tv({
  slots: {
    root: 'group z-10 flex shrink-0 touch-none items-center justify-center outline-hidden select-none hover:bg-blue-100 focus-visible:bg-blue-100 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-blue-500 focus-visible:outline-solid',
    grip: 'rounded-full bg-neutral-300 group-hover:bg-blue-500 group-focus-visible:bg-blue-500',
  },
  variants: {
    orientation: {
      vertical: { root: 'w-2 cursor-col-resize', grip: 'h-8 w-0.5' },
      horizontal: { root: 'h-2 cursor-row-resize', grip: 'h-0.5 w-8' },
    },
  },
});
