import {
  createContext,
  useContext,
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { Disclosure } from '@green/ui';

const SectionsContext = createContext<{
  active: string | null;
  setActive: (number: string | null) => void;
} | null>(null);

/** Разделы остаются смонтированными: переключение сохраняет правки и проверки. */
export function SourceSections({
  defaultSection,
  reviewRequest,
  activeSection,
  onActiveChange,
  children,
}: {
  defaultSection: string | null;
  reviewRequest?: { section: string; sequence: number };
  activeSection?: string | null;
  onActiveChange?: (number: string | null) => void;
  children: ReactNode;
}) {
  const [localActive, setLocalActive] = useState(defaultSection);
  const controlled = activeSection !== undefined;
  const active = controlled ? activeSection : localActive;
  const setActive = useCallback(
    (number: string | null) => {
      if (!controlled) setLocalActive(number);
      onActiveChange?.(number);
    },
    [controlled, onActiveChange],
  );
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!reviewRequest) return;
    setActive(reviewRequest.section);
    const section =
      root.current?.querySelectorAll(':scope > section')[
        Number(reviewRequest.section) - 1
      ];
    const trigger = section?.querySelector<HTMLButtonElement>(
      ':scope > div > button',
    );
    trigger?.focus({ preventScroll: true });
    trigger?.scrollIntoView?.({ block: 'nearest' });
  }, [reviewRequest, setActive]);
  return (
    <SectionsContext.Provider value={{ active, setActive }}>
      <div
        ref={root}
        className="min-w-0 [&>section:nth-last-of-type(1)]:border-b-0"
        onKeyDown={(event) => {
          const buttons = Array.from(
            event.currentTarget.querySelectorAll<HTMLButtonElement>(
              ':scope > section > div > button',
            ),
          );
          const index = buttons.indexOf(event.target as HTMLButtonElement);
          if (index < 0) return;
          const next = {
            ArrowDown: (index + 1) % buttons.length,
            ArrowUp: (index - 1 + buttons.length) % buttons.length,
            Home: 0,
            End: buttons.length - 1,
          }[event.key];
          if (next === undefined) return;
          event.preventDefault();
          buttons[next]?.focus();
        }}
      >
        {children}
      </div>
    </SectionsContext.Provider>
  );
}

export function SourceSection({
  number,
  title,
  children,
  description,
  defaultOpen = true,
}: {
  number: string;
  title: string;
  children: ReactNode;
  description?: ReactNode;
  defaultOpen?: boolean;
}) {
  const sections = useContext(SectionsContext);
  return (
    <Disclosure
      variant="section"
      stickyHeader
      label={title}
      defaultOpen={defaultOpen}
      open={sections ? sections.active === number : undefined}
      onOpenChange={
        sections
          ? (open) => sections.setActive(open ? number : null)
          : undefined
      }
      title={
        <span className="flex flex-wrap items-baseline gap-x-6 gap-y-1">
          <span
            role="heading"
            aria-level={2}
            className="text-sm font-semibold text-neutral-800"
          >
            {title}
          </span>
          {description && (
            <span className="text-xs font-normal text-neutral-500">
              {description}
            </span>
          )}
        </span>
      }
      startIcon={
        <span
          aria-hidden="true"
          className="text-xs text-neutral-500 tabular-nums"
        >
          {number}
        </span>
      }
      contentClassName="space-y-4"
    >
      {children}
    </Disclosure>
  );
}
