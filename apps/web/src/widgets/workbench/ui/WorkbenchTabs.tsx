import { Button } from '@green/ui';
import type { KeyboardEvent } from 'react';
interface WorkbenchTab {
  id: string;
  label: string;
}
interface WorkbenchTabsProps<T extends string> {
  id: string;
  label: string;
  tabs: ReadonlyArray<WorkbenchTab & { id: T }>;
  value: T;
  onChange: (value: T) => void;
  hidden?: boolean;
}
function moveTabFocus(event: KeyboardEvent<HTMLDivElement>) {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
  const tabs = [
    ...event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]'),
  ];
  const current = tabs.indexOf(document.activeElement as HTMLButtonElement);
  if (current < 0) return;
  const next =
    event.key === 'Home'
      ? 0
      : event.key === 'End'
        ? tabs.length - 1
        : (current + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) %
          tabs.length;
  event.preventDefault();
  // Manual activation lets a controlled owner guard a business transition.
  tabs[next]?.focus();
}
export function WorkbenchTabs<T extends string>({
  id,
  label,
  tabs,
  value,
  onChange,
  hidden = false,
}: WorkbenchTabsProps<T>) {
  return (
    <div
      role="tablist"
      aria-label={label}
      className="flex min-w-0 flex-wrap items-center gap-1"
      hidden={hidden}
      inert={hidden}
      onKeyDown={moveTabFocus}
    >
      {tabs.map((tab) => (
        <Button
          key={tab.id}
          id={`${id}-tab-${tab.id}`}
          className="border-0 px-2 text-xs aria-selected:bg-blue-100 aria-selected:text-blue-700"
          role="tab"
          aria-selected={value === tab.id}
          aria-controls={`${id}-${tab.id}`}
          tabIndex={value === tab.id ? 0 : -1}
          variant="ghost"
          controlSize="compact"
          onClick={() => onChange(tab.id)}
        >
          {tab.label}
        </Button>
      ))}
    </div>
  );
}
