import { tv, type VariantProps } from 'tailwind-variants';

export const dialog = tv({
  slots: {
    popup:
      'rounded-dialog flex max-h-[calc(100dvh-48px)] w-full max-w-[520px] min-w-0 flex-col border border-solid border-neutral-300 bg-white font-sans text-sm leading-5 text-neutral-800 shadow-xl outline-none',
    body: 'min-h-0 overflow-y-auto overscroll-y-contain p-4 [scrollbar-gutter:stable]',
  },
  variants: {
    size: {
      default: {},
      form: { popup: 'max-w-[620px]' },
      wide: { popup: 'max-w-[960px]' },
    },
    stable: {
      compact: {
        popup: 'h-[min(560px,calc(100dvh-48px))]',
        body: 'flex flex-1 flex-col overflow-hidden [scrollbar-gutter:auto]',
      },
      true: {
        popup: 'h-[min(760px,calc(100dvh-48px))]',
        body: 'flex flex-1 flex-col overflow-hidden [scrollbar-gutter:auto]',
      },
    },
  },
  defaultVariants: { size: 'default', stable: false },
});

export type DialogSize = NonNullable<VariantProps<typeof dialog>['size']>;
