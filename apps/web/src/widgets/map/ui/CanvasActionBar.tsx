import { ControlProvider, FormActions, tv } from '@green/ui';
import type { ComponentProps, FC } from 'react';

const actionBar = tv({
  base: 'rounded-card absolute bottom-11 z-40 max-w-[calc(100%-2rem)] border border-solid border-neutral-300 bg-white p-2 text-xs shadow-sm',
  variants: {
    align: {
      center: 'left-1/2 -translate-x-1/2 justify-center',
      start: 'left-18 max-w-[calc(100%-6rem)] justify-start',
    },
  },
  defaultVariants: { align: 'center' },
});

export interface CanvasActionBarProps extends ComponentProps<
  typeof FormActions
> {
  align?: 'start' | 'center';
}
/** A canvas action surface owns placement; its caller owns the task and commands. */
export const CanvasActionBar: FC<CanvasActionBarProps> = ({
  align = 'center',
  className,
  ...props
}) => (
  <ControlProvider size="compact">
    <FormActions {...props} className={actionBar({ align, className })} />
  </ControlProvider>
);
