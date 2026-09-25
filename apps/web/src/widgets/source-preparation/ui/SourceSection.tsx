import type { ReactNode } from 'react';

export function SourceSection({
  number,
  title,
  children,
}: {
  number: string;
  title: string;
  children: ReactNode;
}) {
  return (
    <section
      aria-label={title}
      className="grid gap-3 border-t border-neutral-200 pt-5 first:border-t-0 first:pt-0 md:grid-cols-[8rem_minmax(0,1fr)] md:gap-6"
    >
      <header className="flex items-baseline gap-2 md:block">
        <span className="text-xs text-neutral-400 tabular-nums">{number}</span>
        <h2 className="m-0 text-sm font-semibold text-neutral-800 md:mt-1">
          {title}
        </h2>
      </header>
      <div className="min-w-0 space-y-3">{children}</div>
    </section>
  );
}
