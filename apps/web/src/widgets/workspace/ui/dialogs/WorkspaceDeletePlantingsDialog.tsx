import { plantingCount } from '@/shared/format/countLabel';
import { errorMessage as message } from '@/shared/errors/errorMessage';
import { Button, Dialog, FormActions } from '@green/ui';
import { Trash2 } from 'lucide-react';
import type { FC } from 'react';
import type { WorkspaceReadyModel } from '../../model/useWorkspaceModel';

export interface WorkspaceDeletePlantingsDialogProps extends Pick<
  WorkspaceReadyModel,
  | 'deleteSelectionOpen'
  | 'selectedIds'
  | 'setDeleteSelectionOpen'
  | 'editorBusy'
  | 'deleteObjects'
  | 'createRelease'
> {}

export const WorkspaceDeletePlantingsDialogPropsFor = (
  model: WorkspaceDeletePlantingsDialogProps,
): WorkspaceDeletePlantingsDialogProps => ({
  deleteSelectionOpen: model.deleteSelectionOpen,
  selectedIds: model.selectedIds,
  setDeleteSelectionOpen: model.setDeleteSelectionOpen,
  editorBusy: model.editorBusy,
  deleteObjects: model.deleteObjects,
  createRelease: model.createRelease,
});

export const WorkspaceDeletePlantingsDialog: FC<
  WorkspaceDeletePlantingsDialogProps
> = ({
  deleteSelectionOpen,
  selectedIds,
  setDeleteSelectionOpen,
  editorBusy,
  deleteObjects,
  createRelease,
}) => {
  return (
    <Dialog
      open={deleteSelectionOpen}
      title={
        selectedIds.length > 1
          ? `Удалить ${plantingCount(selectedIds.length)}`
          : 'Удалить посадку'
      }
      onClose={() => setDeleteSelectionOpen(false)}
      footer={
        <FormActions layout="equal" minItemWidth="12rem">
          <Button
            variant="secondary"
            disabled={editorBusy}
            onClick={() => setDeleteSelectionOpen(false)}
          >
            Отмена
          </Button>
          <Button
            variant="danger"
            icon={Trash2}
            loading={deleteObjects.isPending}
            disabled={createRelease.isPending}
            onClick={() => deleteObjects.mutate(selectedIds)}
          >
            Удалить
          </Button>
        </FormActions>
      }
    >
      <p>Посадки исчезнут из текущей схемы</p>
      {Boolean(deleteObjects.error) && (
        <p role="alert">{message(deleteObjects.error)}</p>
      )}
    </Dialog>
  );
};
