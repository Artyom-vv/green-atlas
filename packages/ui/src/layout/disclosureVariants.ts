import { tv } from 'tailwind-variants';

export const disclosure = tv({
  slots: {
    root: 'min-w-0',
    trigger:
      'flex w-full shrink-0 cursor-pointer items-center justify-between gap-3 border-0 bg-transparent px-3 py-1.5 text-left leading-[18px] font-medium text-neutral-700 focus-visible:outline-2 focus-visible:outline-(--focus) disabled:cursor-not-allowed',
    content: 'min-w-0',
  },
  variants: {
    variant: {
      section: {
        root: 'border-0 border-b border-solid border-neutral-200 bg-white',
        trigger:
          'min-h-14 px-4 py-3 hover:bg-neutral-50 focus-visible:-outline-offset-2 aria-expanded:bg-neutral-50 sm:px-6',
        content: 'border-0 px-4 pt-2 pb-5 sm:px-6',
      },
      framed: {
        root: 'rounded-control overflow-hidden border border-solid border-neutral-300 bg-white',
        trigger: 'hover:bg-neutral-100',
        content: 'border-0 border-t border-solid border-neutral-200 p-4',
      },
      plain: {
        trigger: 'px-0 hover:text-neutral-800',
        content: 'grid gap-3 py-3',
      },
      panel: {
        root: 'overflow-hidden rounded-xl border border-solid border-neutral-200 bg-white',
        trigger:
          'min-h-16 px-4 py-4 hover:bg-neutral-50 focus-visible:-outline-offset-2',
        content: 'border-0 border-t border-solid border-neutral-200 p-4',
      },
    },
  },
  defaultVariants: { variant: 'framed' },
});
