import type { ReactNode } from 'react';

/** The dialog owns its full-width footer boundary and inset. */
export function PreparationReviewActions({
  status,
  busy,
  children,
}: {
  status: ReactNode;
  busy?: boolean;
  children: ReactNode;
}) {
  return (
    <div
      className="flex min-h-(--control-height-default) min-w-0 flex-wrap items-center justify-between gap-3"
      aria-busy={busy || undefined}
    >
      <span
        role="status"
        aria-live="polite"
        className="min-w-0 text-sm leading-5 text-neutral-600"
      >
        {status}
      </span>
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        {children}
      </div>
    </div>
  );
}
