import { tv, type VariantProps } from 'tailwind-variants';

export const dialog = tv({
  slots: {
    popup:
      'rounded-dialog motion-safe:animate-surface-enter max-h-overlay-available-height max-w-dialog-default flex w-full min-w-0 flex-col border border-solid border-neutral-300 bg-white font-sans text-sm leading-5 text-neutral-800 outline-none',
    body: 'min-h-0 overflow-y-auto overscroll-y-contain p-4 [scrollbar-gutter:stable]',
  },
  variants: {
    bodyInset: {
      default: {},
      none: { popup: 'overflow-hidden', body: 'p-0' },
    },
    size: {
      default: {},
      form: { popup: 'max-w-dialog-form' },
      wide: { popup: 'max-w-dialog-wide' },
    },
    stable: {
      compact: {
        popup: 'h-dialog-compact-height',
        body: 'flex flex-1 flex-col overflow-hidden [scrollbar-gutter:auto]',
      },
      true: {
        popup: 'h-dialog-review-height',
        body: 'flex flex-1 flex-col overflow-hidden [scrollbar-gutter:auto]',
      },
    },
  },
  defaultVariants: { size: 'default', stable: false, bodyInset: 'default' },
});

export type DialogSize = NonNullable<VariantProps<typeof dialog>['size']>;
