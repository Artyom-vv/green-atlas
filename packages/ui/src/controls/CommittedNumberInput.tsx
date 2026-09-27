import type { FC } from 'react';
import { useEffect, useRef, useState } from 'react';
import { NumberInputControl } from './NativeNumberInput';
import type { CommittedNumberInputProps } from './numberInput.types';

export const CommittedNumberInput: FC<CommittedNumberInputProps> = ({
  value,
  onValueChange,
  min = -Infinity,
  max = Infinity,
  onBlur,
  ...props
}) => {
  const [text, setText] = useState(String(value));
  const emittedValue = useRef<number | undefined>(undefined);
  useEffect(() => {
    if (value !== emittedValue.current) setText(String(value));
    emittedValue.current = undefined;
  }, [value]);
  const publish = (next: number) => {
    emittedValue.current = next;
    onValueChange(next);
  };
  return (
    <NumberInputControl
      {...props}
      value={text}
      min={Number.isFinite(min) ? min : undefined}
      max={Number.isFinite(max) ? max : undefined}
      onChange={(event) => {
        const next = event.target.value;
        setText(next);
        const number = Number(next);
        if (next && Number.isFinite(number) && number >= min && number <= max)
          publish(number);
      }}
      onBlur={(event) => {
        const number = text ? Number(text) : value;
        const next = Math.max(
          min,
          Math.min(max, Number.isFinite(number) ? number : value),
        );
        setText(String(next));
        if (next !== value) publish(next);
        onBlur?.(event);
      }}
    />
  );
};
