import { Tooltip as BaseTooltip } from '@base-ui/react/tooltip';
import type { FC, ReactElement, ReactNode } from 'react';
import { useId, useState } from 'react';
import { TooltipContent } from './TooltipContent';
export interface TooltipProps {
  content: string;
  children?: ReactNode;
  /** Attach the tooltip to the actual focusable control instead of a wrapper. */
  render?: ReactElement;
}
export const Tooltip: FC<TooltipProps> = ({ content, children, render }) => {
  const [open, setOpen] = useState(false);
  const tooltipId = useId();
  return (
    <BaseTooltip.Root open={open} onOpenChange={setOpen}>
      <BaseTooltip.Trigger
        render={
          render ?? (
            <span
              className="inline-flex"
              data-tooltip={content}
              aria-describedby={open ? tooltipId : undefined}
            />
          )
        }
      >
        {children}
      </BaseTooltip.Trigger>
      <TooltipContent id={tooltipId}>{content}</TooltipContent>
    </BaseTooltip.Root>
  );
};
