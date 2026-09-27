import type { FC } from 'react';
import { ChoiceInput, type ChoiceInputProps } from './ChoiceInput';

export interface RadioProps extends Omit<ChoiceInputProps, 'type'> {}
export const Radio: FC<RadioProps> = (props) => (
  <ChoiceInput type="radio" {...props} />
);
