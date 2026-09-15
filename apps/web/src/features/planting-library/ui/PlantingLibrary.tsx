import { ControlProvider } from '@green/ui';
import type { FC } from 'react';
import { FormProvider } from 'react-hook-form';
import { usePlantingLibraryForm } from '../model/usePlantingLibraryForm';
import type { PlantingLibraryProps } from './PlantingLibrary.types';
import { PlantingLibraryBody } from './PlantingLibraryBody';
import { PlantingLibraryFooter } from './PlantingLibraryFooter';

export type { PlantingLibraryProps } from './PlantingLibrary.types';

const PlantingLibraryContent: FC<PlantingLibraryProps> = (props) => (
  <ControlProvider size="compact">
    <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-3">
      <PlantingLibraryBody {...props} />
      <footer className="shrink-0 border-t border-neutral-200 pt-3">
        <PlantingLibraryFooter {...props} />
      </footer>
    </div>
  </ControlProvider>
);

const OwnedPlantingLibrary: FC<PlantingLibraryProps> = (props) => {
  const form = usePlantingLibraryForm(props.initialIds);
  return (
    <FormProvider {...form}>
      <PlantingLibraryContent {...props} />
    </FormProvider>
  );
};

export const PlantingLibrary: FC<PlantingLibraryProps> = (props) =>
  props.form ? (
    <FormProvider {...props.form}>
      <PlantingLibraryContent {...props} />
    </FormProvider>
  ) : (
    <OwnedPlantingLibrary {...props} />
  );
