import { tv } from 'tailwind-variants';
import { sectionRow } from './sectionRowVariants';

export const disclosure = tv({
  slots: {
    root: 'min-w-0',
    header: 'flex min-w-0 items-center gap-2',
    trigger:
      'flex w-full shrink-0 cursor-pointer items-center justify-between gap-3 border-0 bg-transparent px-3 py-1.5 text-left leading-[18px] font-medium text-neutral-700 focus-visible:outline-2 focus-visible:outline-(--focus) disabled:cursor-not-allowed',
    content: 'min-w-0',
  },
  variants: {
    variant: {
      section: {
        root: 'border-0 border-b border-solid border-neutral-200 bg-white',
        trigger: sectionRow({ className: 'aria-expanded:bg-neutral-100' }),
        content: 'border-0 px-4 pt-2 pb-5 min-[720px]:px-6',
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
        root: 'rounded-dialog overflow-hidden border border-solid border-neutral-200 bg-white',
        trigger:
          'min-h-16 px-4 py-4 hover:bg-neutral-100 focus-visible:-outline-offset-2',
        content: 'border-0 border-t border-solid border-neutral-200 p-4',
      },
    },
    contentVariant: {
      default: {},
      flush: { content: 'p-0 min-[720px]:px-0' },
    },
    stickyHeader: {
      true: {
        root: 'overflow-visible',
        header: 'sticky top-0 z-10 rounded-t-[inherit] bg-white',
      },
    },
    hasActions: {
      true: {
        header: 'grid grid-cols-[minmax(0,1fr)_auto_auto] gap-3',
        trigger: 'col-span-full row-start-1 grid grid-cols-subgrid',
      },
    },
    headerVariant: {
      default: {},
      inset: { trigger: 'rounded-control px-2 hover:bg-neutral-100' },
      'trailing-actions': {
        trigger: 'rounded-control pr-0 pl-2 hover:bg-neutral-100',
      },
    },
  },
  compoundVariants: [
    {
      headerVariant: 'trailing-actions',
      hasActions: true,
      class: {
        header: 'grid-cols-[minmax(0,1fr)_1rem_2rem] gap-2 pr-2',
      },
    },
  ],
  defaultVariants: {
    variant: 'framed',
    contentVariant: 'default',
    stickyHeader: false,
    hasActions: false,
    headerVariant: 'default',
  },
});
