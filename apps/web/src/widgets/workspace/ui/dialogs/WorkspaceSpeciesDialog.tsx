import { SpeciesAssignmentPanel } from '@/features/species-assignment/ui/SpeciesAssignmentPanel';
import { errorMessage as message } from '@/shared/errors/errorMessage';
import { Dialog } from '@green/ui';
import type { FC } from 'react';
import type { WorkspaceReadyModel } from '../../model/useWorkspaceModel';

export interface WorkspaceSpeciesDialogProps extends Pick<
  WorkspaceReadyModel,
  | 'selectedObjects'
  | 'speciesAssignmentOpen'
  | 'reviewOpen'
  | 'speciesCatalogBrowsing'
  | 'setSpeciesAssignmentOpen'
  | 'setSpeciesCatalogBrowsing'
  | 'editor'
  | 'speciesShortlistQuery'
  | 'previewChanges'
  | 'previewSpeciesAssignment'
> {}

export const WorkspaceSpeciesDialogPropsFor = (
  model: WorkspaceSpeciesDialogProps,
): WorkspaceSpeciesDialogProps => ({
  selectedObjects: model.selectedObjects,
  speciesAssignmentOpen: model.speciesAssignmentOpen,
  reviewOpen: model.reviewOpen,
  speciesCatalogBrowsing: model.speciesCatalogBrowsing,
  setSpeciesAssignmentOpen: model.setSpeciesAssignmentOpen,
  setSpeciesCatalogBrowsing: model.setSpeciesCatalogBrowsing,
  editor: model.editor,
  speciesShortlistQuery: model.speciesShortlistQuery,
  previewChanges: model.previewChanges,
  previewSpeciesAssignment: model.previewSpeciesAssignment,
});

export const WorkspaceSpeciesDialog: FC<WorkspaceSpeciesDialogProps> = ({
  selectedObjects,
  speciesAssignmentOpen,
  reviewOpen,
  speciesCatalogBrowsing,
  setSpeciesAssignmentOpen,
  setSpeciesCatalogBrowsing,
  editor,
  speciesShortlistQuery,
  previewChanges,
  previewSpeciesAssignment,
}) => {
  if (selectedObjects.length === 0) return null;
  return (
    <Dialog
      open={speciesAssignmentOpen && !reviewOpen}
      title="Назначить породу"
      size={speciesCatalogBrowsing ? 'wide' : 'form'}
      stableHeight
      keepMounted
      onClose={() => setSpeciesAssignmentOpen(false)}
    >
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <SpeciesAssignmentPanel
          header={null}
          onCatalogModeChange={setSpeciesCatalogBrowsing}
          onSelectKind={(kind) =>
            editor.select(
              selectedObjects.flatMap((object) =>
                object.kind === kind && object.id ? [object.id] : [],
              ),
              'replace',
            )
          }
          objects={selectedObjects}
          shortlist={speciesShortlistQuery.data}
          loading={speciesShortlistQuery.isLoading}
          previewing={previewChanges.isPending}
          error={
            speciesShortlistQuery.error
              ? message(speciesShortlistQuery.error)
              : undefined
          }
          onAssign={previewSpeciesAssignment}
          onCancel={() => setSpeciesAssignmentOpen(false)}
        />
      </div>
    </Dialog>
  );
};
