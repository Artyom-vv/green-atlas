import { Button as BaseButton } from '@base-ui/react/button';
import type { ReactNode } from 'react';
import { tv } from 'tailwind-variants';
import { useControlSize } from '../foundations/ControlProvider';
import { controlSizes, type ControlSize } from '../foundations/utils';
import { Toolbar } from '../layout/Toolbar';
import { buttonVariants } from './buttonVariants';

const segmentedControl = tv({
  base: 'h-(--control-height) shrink-0 flex-nowrap gap-0 p-0.5',
  variants: { size: controlSizes },
});

export interface SegmentOption<Value extends string> {
  value: Value;
  label: ReactNode;
  disabled?: boolean;
}

export interface SegmentedControlProps<Value extends string> {
  label: string;
  value: Value;
  options: readonly SegmentOption<Value>[];
  onChange: (value: Value) => void;
  controlSize?: ControlSize;
  className?: string;
}

/** The compound control owns one outer height, including its border and inset. */
export function SegmentedControl<Value extends string>({
  label,
  value,
  options,
  onChange,
  controlSize,
  className,
}: SegmentedControlProps<Value>) {
  const size = useControlSize(controlSize);
  const segment = buttonVariants({ variant: 'ghost', size });
  return (
    <Toolbar label={label} className={segmentedControl({ size, className })}>
      {options.map((option) => (
        <BaseButton
          key={option.value}
          type="button"
          data-slot="segment"
          data-size={size}
          aria-pressed={value === option.value}
          disabled={option.disabled}
          onClick={() => onChange(option.value)}
          className={segment.root({
            className:
              'h-full min-h-0 flex-1 px-2 py-0 aria-pressed:bg-blue-100 aria-pressed:text-blue-700',
          })}
        >
          <span className={segment.label()}>{option.label}</span>
        </BaseButton>
      ))}
    </Toolbar>
  );
}
