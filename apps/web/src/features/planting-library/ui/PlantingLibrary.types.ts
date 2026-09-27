import type { PlanObject, PlantingZoneAssignment } from '@green/api-client';
import type { UseFormReturn } from 'react-hook-form';
import type { PlantingLibraryFormValues } from '../model/plantingLibrary';

export interface PlantingLibraryDataProps {
  objects: PlanObject[];
  zones: PlantingZoneAssignment[];
  names: Map<string, string>;
}

export interface PlantingLibraryFooterProps extends Pick<
  PlantingLibraryDataProps,
  'objects' | 'names'
> {
  onSelect: (ids: string[]) => void;
  onSpecies: (ids: string[]) => void;
}

export interface PlantingLibraryProps
  extends PlantingLibraryDataProps, PlantingLibraryFooterProps {
  form?: UseFormReturn<PlantingLibraryFormValues>;
  initialIds?: string[];
}
