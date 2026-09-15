import { useForm } from 'react-hook-form';
import type { PlantingLibraryFormValues } from './plantingLibrary';
export function usePlantingLibraryForm(initialIds: string[] = []) {
  return useForm<PlantingLibraryFormValues>({
    defaultValues: {
      query: '',
      kind: 'all',
      state: 'all',
      zoneId: 'all',
      groupId: 'all',
      selectedIds: initialIds.slice(),
    },
    shouldUnregister: false,
  });
}
