import { tv } from 'tailwind-variants';
export const resizeHandle = tv({
  slots: {
    root: 'group z-10 flex shrink-0 touch-none items-center justify-center outline-hidden select-none hover:bg-blue-100 focus-visible:bg-blue-100 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-blue-500 focus-visible:outline-solid active:bg-blue-100',
    grip: 'rounded-full bg-neutral-300 group-hover:bg-blue-500 group-focus-visible:bg-blue-500 group-active:bg-blue-500',
  },
  variants: {
    orientation: {
      vertical: { root: 'w-2 cursor-col-resize', grip: 'h-8 w-0.5' },
      horizontal: { root: 'h-2 cursor-row-resize', grip: 'h-0.5 w-8' },
    },
    appearance: {
      grip: {},
      boundary: {
        root: 'relative focus-visible:outline-none',
        grip: 'absolute rounded-none bg-neutral-200',
      },
    },
  },
  compoundVariants: [
    {
      orientation: 'horizontal',
      appearance: 'boundary',
      class: {
        grip: 'inset-x-0 top-1/2 h-px w-full group-focus-visible:h-0.5',
      },
    },
    {
      orientation: 'vertical',
      appearance: 'boundary',
      class: {
        grip: 'inset-y-0 left-1/2 h-full w-px group-focus-visible:w-0.5',
      },
    },
  ],
  defaultVariants: { appearance: 'grip' },
});
