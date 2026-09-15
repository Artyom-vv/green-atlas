import type { FC, ReactNode } from 'react';
import { tv } from 'tailwind-variants';

const navigationKeys = {
  horizontal: { previous: 'ArrowLeft', next: 'ArrowRight' },
  vertical: { previous: 'ArrowUp', next: 'ArrowDown' },
} as const;
export type ToolbarOrientation = keyof typeof navigationKeys;

const toolbar = tv({
  base: 'rounded-control inline-flex min-w-0 gap-1 border border-solid border-neutral-300 bg-white p-1',
  variants: {
    orientation: {
      horizontal: 'flex-row flex-wrap items-center',
      vertical: 'flex-col items-stretch',
    },
  },
});

export interface ToolbarProps {
  children: ReactNode;
  className?: string;
  label?: string;
  orientation?: ToolbarOrientation;
}
export const Toolbar: FC<ToolbarProps> = ({
  children,
  className,
  label = 'Панель инструментов',
  orientation = 'horizontal',
}) => (
  <div
    className={toolbar({ orientation, className })}
    role="toolbar"
    aria-label={label}
    aria-orientation={orientation}
    onKeyDown={(event) => {
      const keys = navigationKeys[orientation];
      if (![keys.previous, keys.next, 'Home', 'End'].includes(event.key))
        return;
      const target = event.target;
      if (
        !(target instanceof HTMLElement) ||
        target.closest('input, textarea, select, [contenteditable="true"]') ||
        target.closest('[role="toolbar"]') !== event.currentTarget
      )
        return;
      const controls = [
        ...event.currentTarget.querySelectorAll<HTMLButtonElement>(
          'button:not(:disabled):not([aria-disabled="true"])',
        ),
      ].filter(
        (button) => button.closest('[role="toolbar"]') === event.currentTarget,
      );
      const current = controls.indexOf(
        document.activeElement as HTMLButtonElement,
      );
      if (!controls.length || current < 0) return;
      event.preventDefault();
      const next =
        event.key === 'Home'
          ? 0
          : event.key === 'End'
            ? controls.length - 1
            : event.key === keys.next
              ? (current + 1) % controls.length
              : (current - 1 + controls.length) % controls.length;
      controls[next]?.focus();
    }}
  >
    {children}
  </div>
);
