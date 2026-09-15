import { usePlantingLibraryForm } from '@/features/planting-library/model/usePlantingLibraryForm';
import { PlantingLibraryBody } from '@/features/planting-library/ui/PlantingLibraryBody';
import { PlantingLibraryFooter } from '@/features/planting-library/ui/PlantingLibraryFooter';
import { ControlProvider, Dialog } from '@green/ui';
import type { FC } from 'react';
import { FormProvider } from 'react-hook-form';
import type { WorkspaceReadyModel } from '../../model/useWorkspaceModel';

export interface WorkspaceLibraryDialogProps extends Pick<
  WorkspaceReadyModel,
  | 'libraryOpen'
  | 'setLibraryOpen'
  | 'planObjects'
  | 'project'
  | 'speciesNames'
  | 'selectedIds'
  | 'selectFromExplorer'
  | 'mapViewport'
  | 'setSpeciesAssignmentOpen'
> {}

export const WorkspaceLibraryDialogPropsFor = (
  model: WorkspaceLibraryDialogProps,
): WorkspaceLibraryDialogProps => ({
  libraryOpen: model.libraryOpen,
  setLibraryOpen: model.setLibraryOpen,
  planObjects: model.planObjects,
  project: model.project,
  speciesNames: model.speciesNames,
  selectedIds: model.selectedIds,
  selectFromExplorer: model.selectFromExplorer,
  mapViewport: model.mapViewport,
  setSpeciesAssignmentOpen: model.setSpeciesAssignmentOpen,
});

export const WorkspaceLibraryDialog: FC<WorkspaceLibraryDialogProps> = (
  props,
) => props.libraryOpen && <WorkspaceLibraryContent {...props} />;

const WorkspaceLibraryContent: FC<WorkspaceLibraryDialogProps> = ({
  setLibraryOpen,
  planObjects,
  project,
  speciesNames,
  selectedIds,
  selectFromExplorer,
  mapViewport,
  setSpeciesAssignmentOpen,
}) => {
  const form = usePlantingLibraryForm(selectedIds);
  return (
    <FormProvider {...form}>
      <ControlProvider size="compact">
        <Dialog
          open
          title="Посадки проекта"
          size="wide"
          stableHeight
          onClose={() => setLibraryOpen(false)}
          footer={
            <PlantingLibraryFooter
              objects={planObjects}
              names={speciesNames}
              onSelect={(ids) => {
                selectFromExplorer(ids, false);
                mapViewport.current?.fitObjects(ids);
                setLibraryOpen(false);
              }}
              onSpecies={(ids) => {
                selectFromExplorer(ids, false);
                setSpeciesAssignmentOpen(true);
                setLibraryOpen(false);
              }}
            />
          }
        >
          <PlantingLibraryBody
            objects={planObjects}
            zones={project.planting_zones ?? []}
            names={speciesNames}
          />
        </Dialog>
      </ControlProvider>
    </FormProvider>
  );
};
