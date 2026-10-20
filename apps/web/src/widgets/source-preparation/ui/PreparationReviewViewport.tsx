import type { ComponentPropsWithRef } from 'react';
import { tv } from '@green/ui';

const viewport = tv({
  base: 'rounded-control min-h-0 min-w-0 overflow-hidden border border-solid border-neutral-200 bg-neutral-100',
  variants: {
    size: {
      preview: 'h-preparation-preview',
      context: 'h-preparation-context-height',
      fill: 'flex-1',
    },
  },
  defaultVariants: { size: 'preview' },
});

/** Review owns the map's composition; SVG geometry remains in its renderer. */
export function PreparationReviewViewport({
  size = 'preview',
  className,
  ...props
}: ComponentPropsWithRef<'div'> & {
  size?: 'preview' | 'context' | 'fill';
}) {
  return (
    <div
      {...props}
      data-slot="preparation-review-viewport"
      className={viewport({ size, className })}
    />
  );
}
