import type { ComponentPropsWithRef } from 'react';
import type { ControlSize } from '../foundations/utils';
export interface ComboboxOption {
  value: string;
  label: string;
  description?: string;
}
export interface ComboboxProps extends Pick<
  ComponentPropsWithRef<'input'>,
  | 'id'
  | 'ref'
  | 'name'
  | 'aria-label'
  | 'aria-describedby'
  | 'aria-invalid'
  | 'required'
  | 'onBlur'
> {
  value?: string;
  options: ComboboxOption[];
  placeholder?: string;
  emptyLabel?: string;
  disabled?: boolean;
  controlSize?: ControlSize;
  onChange: (value: string) => void;
  className?: string;
}
