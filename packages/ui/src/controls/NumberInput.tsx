import type { FC } from 'react';
import { CommittedNumberInput } from './CommittedNumberInput';
import { NumberInputControl } from './NativeNumberInput';
import type { NumberInputProps } from './numberInput.types';
export type { NumberInputProps } from './numberInput.types';
export const NumberInput: FC<NumberInputProps> = (props) =>
  props.onValueChange ? (
    <CommittedNumberInput {...props} />
  ) : (
    <NumberInputControl {...props} />
  );
