import { tv } from 'tailwind-variants';
import { controlSizes } from '../foundations/utils';
export const numberStepper = tv({
  slots: {
    root: 'inline-flex h-(--control-height) shrink-0 overflow-hidden rounded-(--radius-control) border border-solid border-neutral-300 bg-white',
    button:
      'inline-flex h-full w-(--control-height) cursor-pointer items-center justify-center border-0 bg-transparent text-neutral-700 hover:bg-neutral-100 disabled:cursor-not-allowed disabled:text-neutral-400',
    input:
      'h-full w-[calc(var(--control-height)+24px)] min-w-0 border-0 border-x border-neutral-200 bg-transparent px-1 text-center font-mono text-[13px] tabular-nums outline-hidden focus-visible:relative focus-visible:z-[1] focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-(--focus) focus-visible:outline-solid disabled:cursor-not-allowed disabled:text-neutral-500',
  },
  variants: {
    size: {
      compact: { root: controlSizes.compact },
      default: { root: controlSizes.default },
      large: { root: controlSizes.large },
    },
  },
});
