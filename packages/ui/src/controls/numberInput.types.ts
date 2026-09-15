import type { InputHTMLAttributes, Ref } from 'react';
import { type ControlSize } from '../foundations/utils';

export interface NativeNumberInputProps extends Omit<
  InputHTMLAttributes<HTMLInputElement>,
  'size' | 'type'
> {
  size?: number;
  unit?: string;
  controlSize?: ControlSize;
  ref?: Ref<HTMLInputElement>;
}

export interface CommittedNumberInputProps extends Omit<
  NativeNumberInputProps,
  'value' | 'defaultValue' | 'onChange' | 'min' | 'max'
> {
  value: number;
  onValueChange: (value: number) => void;
  min?: number;
  max?: number;
}

export type NumberInputProps =
  | (NativeNumberInputProps & { onValueChange?: never })
  | CommittedNumberInputProps;
