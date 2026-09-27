import { Tooltip as BaseTooltip } from '@base-ui/react/tooltip';
import type { FC, ReactNode } from 'react';

interface TooltipContentProps {
  id?: string;
  children: ReactNode;
}

export const TooltipContent: FC<TooltipContentProps> = ({ id, children }) => (
  <BaseTooltip.Portal>
    <BaseTooltip.Positioner sideOffset={8} className="z-tooltip">
      <BaseTooltip.Popup
        id={id}
        role="tooltip"
        className="rounded-control max-w-64 bg-neutral-800 px-2 py-1 text-xs leading-4 text-white shadow-lg"
      >
        {children}
      </BaseTooltip.Popup>
    </BaseTooltip.Positioner>
  </BaseTooltip.Portal>
);
