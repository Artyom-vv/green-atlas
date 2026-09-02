import type { ReactNode } from 'react';

/** Small native disclosure used for optional inspector settings */
export function Accordion({ title, children, open = false, className = '' }: {
  title: string;
  children: ReactNode;
  open?: boolean;
  className?: string;
}) {
  return <details className={`panel-accordion ${className}`.trim()} open={open}>
    <summary>{title}</summary>
    <div className="panel-accordion__content">{children}</div>
  </details>;
}
