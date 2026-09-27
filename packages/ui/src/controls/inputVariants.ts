import { tv } from 'tailwind-variants';
import { controlSizes } from '../foundations/utils';

export const fieldControl = tv({
  base: 'w-full min-w-0 shrink-0 rounded-(--radius-control) border border-solid border-neutral-300 bg-white px-3 text-sm text-neutral-800 outline-hidden transition-colors placeholder:text-neutral-400 hover:border-neutral-500 focus-visible:border-transparent focus-visible:outline-2 focus-visible:outline-(--focus) focus-visible:outline-solid disabled:cursor-not-allowed disabled:bg-neutral-100 disabled:text-neutral-500 aria-[invalid=true]:border-(--error)',
});

export const input = tv({
  extend: fieldControl,
  variants: { size: controlSizes },
  defaultVariants: { size: 'default' },
});

export const textarea = tv({
  extend: fieldControl,
  base: 'min-h-19 resize-y py-2.5 leading-5',
});
