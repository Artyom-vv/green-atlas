import { useNumberStepperDraft } from './useNumberStepperDraft';
import { Minus, Plus } from 'lucide-react';
import type { FC } from 'react';
import { useControlSize } from '../foundations/ControlProvider';
import { Icon as IconContent } from '../foundations/Icon';
import { type ControlSize } from '../foundations/utils';
import { numberStepper } from './numberStepperVariants';
export interface NumberStepperProps {
  value: number;
  onChange: (value: number) => void;
  min?: number;
  max?: number;
  step?: number;
  controlSize?: ControlSize;
  label: string;
  disabled?: boolean;
  className?: string;
}
export const NumberStepper: FC<NumberStepperProps> = ({
  value,
  onChange,
  min = 0,
  max = Number.MAX_SAFE_INTEGER,
  step = 1,
  controlSize,
  label,
  disabled = false,
  className,
}) => {
  const size = useControlSize(controlSize);
  const styles = numberStepper({ size });
  const draft = useNumberStepperDraft({ value, min, max, onChange });
  return (
    <div
      className={styles.root({ className })}
      data-size={size}
      role="group"
      aria-label={label}
    >
      <button
        className={styles.button()}
        type="button"
        aria-label={`Уменьшить: ${label}`}
        disabled={disabled || value <= min}
        onClick={() => draft.change(-step)}
      >
        <IconContent icon={Minus} />
      </button>
      <input
        className={styles.input()}
        type="number"
        inputMode="decimal"
        aria-label={label}
        disabled={disabled}
        min={min}
        max={max}
        step={step}
        {...draft.inputProps}
        onFocus={(event) => event.currentTarget.select()}
      />
      <button
        className={styles.button()}
        type="button"
        aria-label={`Увеличить: ${label}`}
        disabled={disabled || value >= max}
        onClick={() => draft.change(step)}
      >
        <IconContent icon={Plus} />
      </button>
    </div>
  );
};
