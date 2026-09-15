import type { FC } from 'react';
import { useFieldControl } from '../forms/fieldContext';
import { useControlSize } from '../foundations/ControlProvider';
import { cx } from '../foundations/utils';
import { input } from './inputVariants';
import type { NativeNumberInputProps } from './numberInput.types';
export const NumberInputControl: FC<NativeNumberInputProps> = ({
  unit,
  className,
  controlSize,
  ...props
}) => {
  const size = useControlSize(controlSize);
  const fieldProps = useFieldControl(props);
  return (
    <span className="relative block min-w-0 shrink-0">
      {!!unit && (
        <span className="pointer-events-none absolute top-1/2 right-3 z-[1] -translate-y-1/2 font-mono text-xs text-neutral-500">
          {unit}
        </span>
      )}
      <input
        type="number"
        className={cx(
          input({ size }),
          'h-(--control-height) font-mono tabular-nums',
          unit && 'pr-10',
          className,
        )}
        data-size={size}
        {...fieldProps}
      />
    </span>
  );
};
