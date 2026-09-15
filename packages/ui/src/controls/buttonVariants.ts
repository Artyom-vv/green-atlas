import { tv, type VariantProps } from 'tailwind-variants';
import { controlSizes } from '../foundations/utils';

export const buttonVariants = tv({
  slots: {
    root: 'rounded-control inline-flex max-w-full shrink-0 cursor-pointer items-center justify-center gap-2 border border-solid px-3 text-center leading-4 font-medium transition-colors select-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-(--focus) disabled:cursor-not-allowed disabled:opacity-40',
    label: 'min-w-0 whitespace-nowrap',
  },
  variants: {
    variant: {
      primary: {
        root: 'border-blue-600 bg-blue-600 text-white hover:border-blue-700 hover:bg-blue-700',
      },
      secondary: {
        root: 'border-neutral-300 bg-white text-neutral-800 hover:border-neutral-500',
      },
      ghost: {
        root: 'border-transparent bg-transparent text-neutral-700 hover:bg-neutral-100',
      },
      danger: {
        root: 'border-error/40 text-error hover:border-error hover:bg-error-soft bg-white',
      },
    },
    size: {
      compact: { root: [controlSizes.compact, 'py-1.5'] },
      default: { root: [controlSizes.default, 'py-2'] },
      large: { root: [controlSizes.large, 'py-2.5'] },
    },
    iconOnly: { true: { root: 'size-(--control-height) p-0' } },
    active: {
      true: {
        root: 'border-blue-600 bg-blue-600 text-white hover:bg-blue-700',
      },
    },
  },
  defaultVariants: { variant: 'secondary', size: 'default' },
});

export type ButtonVariant = NonNullable<
  VariantProps<typeof buttonVariants>['variant']
>;
