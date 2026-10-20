import { tv } from 'tailwind-variants';

/** A section disclosure and its sibling action share one visible row contract. */
export const sectionRow = tv({
  base: 'min-h-section-row flex w-full min-w-0 shrink-0 cursor-pointer items-center justify-between gap-3 border-0 bg-transparent px-4 py-3 text-left text-sm leading-5 font-medium text-neutral-700 hover:bg-neutral-100 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-blue-600 focus-visible:outline-solid disabled:cursor-not-allowed disabled:text-neutral-500 disabled:hover:bg-transparent min-[720px]:px-6',
});
