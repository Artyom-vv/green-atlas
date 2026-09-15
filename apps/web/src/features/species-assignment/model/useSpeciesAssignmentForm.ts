import { useForm } from 'react-hook-form';
import type { PlanObjectCreate } from '@green/api-client';

export interface SpeciesAssignmentFormValues {
  revisionId: string;
  sizeClass: Exclude<
    NonNullable<PlanObjectCreate['size_class']>,
    'unspecified'
  >;
}
export function useSpeciesAssignmentForm(
  initial: Partial<SpeciesAssignmentFormValues> = {},
) {
  return useForm<SpeciesAssignmentFormValues>({
    defaultValues: { revisionId: '', sizeClass: 'standard', ...initial },
    shouldUnregister: false,
  });
}
