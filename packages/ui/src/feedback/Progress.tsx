import type { FC } from 'react';
import { cx } from '../foundations/utils';
export interface ProgressProps {
  value?: number;
  label?: string;
}
export const Progress: FC<ProgressProps> = ({ value, label }) => {
  const normalized =
    value === undefined ? undefined : Math.max(0, Math.min(100, value));
  return (
    <div className="grid min-w-0 gap-1.5">
      <div className="flex justify-between text-xs text-neutral-500">
        <span>{label}</span>
        {normalized !== undefined && <span>{normalized}%</span>}
      </div>
      <div
        className={cx(
          'h-[3px] overflow-hidden bg-neutral-200',
          normalized === undefined && '',
        )}
        role="progressbar"
        aria-label={label ?? 'Выполнение операции'}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={normalized}
      >
        <span
          className={cx(
            'block h-full bg-(--primary)',
            normalized === undefined &&
              'w-[35%] animate-[progress-indeterminate_1.2s_ease-in-out_infinite] motion-reduce:animate-none',
          )}
          style={
            normalized === undefined ? undefined : { width: `${normalized}%` }
          }
        />
      </div>
    </div>
  );
};
