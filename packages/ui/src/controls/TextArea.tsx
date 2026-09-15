import type { FC, Ref, TextareaHTMLAttributes } from 'react';
import { useFieldControl } from '../forms/fieldContext';
import { textarea } from './inputVariants';

export interface TextAreaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  ref?: Ref<HTMLTextAreaElement>;
}

export const TextArea: FC<TextAreaProps> = ({ className, ...props }) => {
  const fieldProps = useFieldControl(props);
  return <textarea className={textarea({ className })} {...fieldProps} />;
};
