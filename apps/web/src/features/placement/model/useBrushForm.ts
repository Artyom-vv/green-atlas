import { useForm } from 'react-hook-form';
import { createBrushFormDefaults, type BrushFormValues } from './brushForm';

export function useBrushForm(initial?: Partial<BrushFormValues>) {
  return useForm<BrushFormValues>({
    defaultValues: createBrushFormDefaults(initial),
    shouldUnregister: false,
    mode: 'onChange',
  });
}
