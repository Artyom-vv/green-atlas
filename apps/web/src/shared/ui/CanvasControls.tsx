import { Button, ControlProvider, Toolbar, cx } from '@green/ui';
import type { ComponentProps, HTMLAttributes, ReactNode } from 'react';

/** One surface and one set of outer anchors for controls over either renderer. */
export const canvasSurfaceClassName =
  'rounded-control border border-solid border-neutral-300 bg-white shadow-none';

/** Visible control bounds and shared pointer/focus states inside canvas groups. */
export const canvasControlClassName =
  'rounded-control border border-solid border-neutral-300 bg-white text-neutral-700 hover:border-neutral-500 hover:bg-neutral-100 focus-visible:border-neutral-300 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-(--focus)';

export function CanvasControlGroup({
  className,
  ...props
}: ComponentProps<typeof Toolbar>) {
  return (
    <ControlProvider size="compact">
      <Toolbar
        {...props}
        className={cx(
          canvasSurfaceClassName,
          'flex-nowrap',
          props.orientation === 'vertical' &&
            'w-canvas-rail max-h-full overflow-y-auto [scrollbar-gutter:auto] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden',
          className,
        )}
      />
    </ControlProvider>
  );
}

const positions = {
  topLeft:
    'top-canvas-inset bottom-canvas-inset left-canvas-inset flex items-start',
  topRight:
    'top-canvas-inset right-canvas-inset left-[calc(var(--spacing-canvas-inset)+var(--spacing-canvas-rail)+var(--spacing-canvas-gap))] grid justify-items-end gap-canvas-gap',
  bottomRight:
    'top-[calc(var(--spacing-canvas-inset)+var(--spacing-canvas-rail)+var(--spacing-canvas-gap))] right-canvas-inset bottom-canvas-inset flex flex-col items-end justify-end gap-canvas-gap @max-[440px]/canvas:top-[calc(var(--spacing-canvas-inset)+2*var(--spacing-canvas-rail)+var(--spacing-canvas-gap))]',
  bottomCenter:
    'right-[calc(var(--spacing-canvas-inset)+var(--spacing-canvas-rail)+var(--spacing-canvas-gap))] bottom-canvas-inset left-[calc(var(--spacing-canvas-inset)+var(--spacing-canvas-rail)+var(--spacing-canvas-gap))] flex justify-center',
} as const;

export function CanvasControlSlot({
  position,
  children,
  className,
  ...props
}: HTMLAttributes<HTMLDivElement> & {
  position: keyof typeof positions;
  children: ReactNode;
}) {
  return (
    <div
      {...props}
      className={cx(
        'pointer-events-none absolute z-40 min-w-0 [&>*]:pointer-events-auto',
        positions[position],
        className,
      )}
    >
      {children}
    </div>
  );
}

/** Flat canvas mode group: content-sized labels, shared surface and keyboard owner. */
export function CanvasModeSwitch<Value extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: Value;
  options: readonly {
    value: Value;
    label: string;
    disabled?: boolean;
    title?: string;
  }[];
  onChange: (value: Value) => void;
}) {
  return (
    <div
      role="group"
      aria-label={label}
      className="flex shrink-0 items-center gap-1"
    >
      {options.map((option) => (
        <Button
          key={option.value}
          variant="ghost"
          controlSize="compact"
          className="h-8 min-h-0 w-max min-w-8 shrink-0 border-0 px-2 py-0 aria-pressed:bg-neutral-200 aria-pressed:text-neutral-800 aria-pressed:hover:bg-neutral-300 [&>[data-slot=button-label]]:shrink-0"
          aria-pressed={value === option.value}
          disabled={option.disabled}
          title={option.title}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </Button>
      ))}
    </div>
  );
}
