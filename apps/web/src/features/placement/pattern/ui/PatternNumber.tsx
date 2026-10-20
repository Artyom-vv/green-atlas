import type { FC } from 'react';
import { useController, useFormContext } from 'react-hook-form';
import { NumberInput } from '@green/ui';
import type { PatternFormValues } from '../model/patternForm';

type NumberFieldName = {
  [Key in keyof PatternFormValues]-?: PatternFormValues[Key] extends number
    ? Key
    : never;
}[keyof PatternFormValues];

interface PatternNumberProps {
  name: NumberFieldName;
  label: string;
  min: number;
  max: number;
  step?: number;
  describedBy?: string;
  invalid?: boolean;
}

export const PatternNumber: FC<PatternNumberProps> = ({
  name,
  label,
  min,
  max,
  step = 1,
  describedBy,
  invalid,
}) => {
  const { control } = useFormContext<PatternFormValues>();
  const { field } = useController({ name, control, rules: { min, max } });
  return (
    <NumberInput
      ref={field.ref}
      name={field.name}
      value={field.value}
      onValueChange={field.onChange}
      onBlur={field.onBlur}
      aria-label={label}
      min={min}
      max={max}
      step={step}
      aria-describedby={describedBy}
      aria-invalid={invalid || undefined}
    />
  );
};
