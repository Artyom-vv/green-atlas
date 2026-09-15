import { useForm } from 'react-hook-form';
import {
  createPatternFormDefaults,
  type PatternFormValues,
} from './patternForm';

export function usePatternForm(initial?: Partial<PatternFormValues>) {
  return useForm<PatternFormValues>({
    defaultValues: createPatternFormDefaults(initial),
    shouldUnregister: false,
    mode: 'onChange',
  });
}
