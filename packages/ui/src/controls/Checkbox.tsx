import type { FC, ReactNode } from 'react';
import { ChoiceInput, type ChoiceInputProps } from './ChoiceInput';

export interface CheckboxProps extends Omit<ChoiceInputProps, 'type'> {
  label: ReactNode;
}
export const Checkbox: FC<CheckboxProps> = (props) => (
  <ChoiceInput type="checkbox" {...props} />
);
