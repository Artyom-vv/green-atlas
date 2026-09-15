import type { FC } from 'react';
import { cx } from '../foundations/utils';
export interface StepProgressProps {
  steps: ReadonlyArray<{ id: string; label: string }>;
  current: number;
  label?: string;
  className?: string;
}
export const StepProgress: FC<StepProgressProps> = ({
  steps,
  current,
  label = 'Этапы настройки',
  className,
}) => {
  const active = Math.max(0, Math.min(current, Math.max(0, steps.length - 1)));
  const currentLabel = steps[active]?.label ?? '';
  return (
    <div
      className={cx('flex min-w-0 flex-col gap-2', className)}
      role="status"
      aria-label={`${label}: ${active + 1} из ${steps.length}, ${currentLabel}`}
    >
      <div className="flex w-full gap-1.5" aria-hidden="true">
        {steps.map((step, index) => (
          <span
            key={step.id}
            data-step-state={
              index < active
                ? 'complete'
                : index === active
                  ? 'current'
                  : 'pending'
            }
            className={cx(
              'h-[3px] min-w-0 flex-1 rounded-full bg-neutral-200',
              index < active && 'bg-blue-300',
              index === active && 'my-[-1px] h-[5px] bg-(--primary)',
            )}
          />
        ))}
      </div>
      <strong className="text-xs leading-4 wrap-anywhere text-neutral-700">
        {currentLabel}
      </strong>
    </div>
  );
};
