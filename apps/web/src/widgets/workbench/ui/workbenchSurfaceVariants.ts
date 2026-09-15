import { tv } from '@green/ui';

export const dock = tv({
  base: 'relative flex min-h-0 min-w-0 flex-col border-neutral-300 bg-white',
  variants: {
    side: {
      resources: 'border-r',
      right: 'border-l',
      results: 'h-(--ide-results-height) shrink-0',
    },
  },
});

export const rightPanel = tv({
  base: 'flex h-full min-h-0 min-w-0 flex-col overflow-hidden',
});
