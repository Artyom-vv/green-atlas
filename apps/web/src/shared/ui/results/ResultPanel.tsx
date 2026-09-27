import type { FC, ReactNode } from 'react';
import { ControlProvider, ScrollArea, SurfaceHeader } from '@green/ui';

export interface ResultPanelProps {
  title: ReactNode;
  label?: string;
  count?: ReactNode;
  actions?: ReactNode;
  children?: ReactNode;
  /** Replaces the default scroll body, for content that owns its own scrollport. */
  body?: ReactNode;
}

/** A result header stays outside its content's scrollport. */
export const ResultPanel: FC<ResultPanelProps> = ({
  title,
  label,
  count,
  actions,
  children,
  body,
}) => (
  <ControlProvider size="compact">
    <section
      className="@container/results flex h-full min-h-0 min-w-0 flex-col text-xs leading-[18px] text-neutral-800"
      aria-label={label}
    >
      <SurfaceHeader className="flex-wrap gap-y-2">
        <div className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1">
          <h3 className="m-0 text-xs font-semibold">{title}</h3>
          {count !== undefined && (
            <span className="text-xs text-neutral-600 tabular-nums">
              {count}
            </span>
          )}
        </div>
        {actions && (
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            {actions}
          </div>
        )}
      </SurfaceHeader>
      {body === undefined ? (
        <ScrollArea className="flex-1" contentClassName="px-4 pb-4">
          {children}
        </ScrollArea>
      ) : (
        body
      )}
    </section>
  </ControlProvider>
);
