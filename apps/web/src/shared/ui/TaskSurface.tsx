import type { FC, ReactNode, Ref } from 'react';
import { ControlProvider, ScrollArea, SurfaceHeader, cx } from '@green/ui';

export interface PlacementToolSurfaceProps {
  title: ReactNode;
  label?: string;
  children: ReactNode;
  footer?: ReactNode;
  steps?: ReactNode;
  showHeader?: boolean;
  bodyRef?: Ref<HTMLDivElement>;
  bodyTabIndex?: number;
  bodyClassName?: string;
  className?: string;
}

/** Task content scrolls independently of its pinned header and actions. */
export const PlacementToolSurface: FC<PlacementToolSurfaceProps> = ({
  title,
  label,
  children,
  footer,
  steps,
  showHeader = true,
  bodyRef,
  bodyTabIndex,
  bodyClassName,
  className,
}) => (
  <ControlProvider size="compact">
    <section
      className={cx(
        '@container flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-white text-sm text-neutral-800 [&_[hidden]]:hidden',
        className,
      )}
      aria-label={label}
    >
      {showHeader && (
        <SurfaceHeader>
          <h2 className="m-0 text-sm leading-5 font-semibold">{title}</h2>
        </SurfaceHeader>
      )}
      {steps ? (
        <div className="shrink-0 border-0 border-b border-solid border-neutral-200 p-4">
          {steps}
        </div>
      ) : null}
      <ScrollArea
        className="flex-1"
        viewportProps={{
          ref: bodyRef,
          ...(bodyTabIndex === undefined ? {} : { tabIndex: bodyTabIndex }),
        }}
        viewportClassName="scroll-py-4"
        contentClassName={cx(
          'grid min-w-0 content-start gap-4 p-4',
          bodyClassName,
        )}
      >
        {children}
      </ScrollArea>
      {footer ? (
        <footer className="shrink-0 border-0 border-t border-solid border-neutral-200 p-4">
          {footer}
        </footer>
      ) : null}
    </section>
  </ControlProvider>
);
