import { errorMessage as message } from '@/shared/errors/errorMessage';
import { Button, Dialog, FormActions } from '@green/ui';
import { Trash2 } from 'lucide-react';
import type { FC } from 'react';
import type { WorkspaceReadyModel } from '../../model/useWorkspaceModel';

export interface WorkspaceDeleteZoneDialogProps extends Pick<
  WorkspaceReadyModel,
  | 'zonePendingDelete'
  | 'setZonePendingDelete'
  | 'saveManagedZones'
  | 'draftZones'
> {}

export const WorkspaceDeleteZoneDialogPropsFor = (
  model: WorkspaceDeleteZoneDialogProps,
): WorkspaceDeleteZoneDialogProps => ({
  zonePendingDelete: model.zonePendingDelete,
  setZonePendingDelete: model.setZonePendingDelete,
  saveManagedZones: model.saveManagedZones,
  draftZones: model.draftZones,
});

export const WorkspaceDeleteZoneDialog: FC<WorkspaceDeleteZoneDialogProps> = ({
  zonePendingDelete,
  setZonePendingDelete,
  saveManagedZones,
  draftZones,
}) => {
  return (
    <Dialog
      open={Boolean(zonePendingDelete)}
      title="Удалить рабочий участок"
      onClose={() => setZonePendingDelete(undefined)}
      footer={
        <FormActions layout="equal" minItemWidth="12rem">
          <Button
            variant="secondary"
            disabled={saveManagedZones.isPending}
            onClick={() => setZonePendingDelete(undefined)}
          >
            Отмена
          </Button>
          <Button
            variant="danger"
            icon={Trash2}
            loading={saveManagedZones.isPending}
            onClick={() => {
              if (!zonePendingDelete) return;
              saveManagedZones.mutate({
                zones: draftZones.filter(
                  (zone) => zone.id !== zonePendingDelete.id,
                ),
              });
            }}
          >
            Удалить
          </Button>
        </FormActions>
      }
    >
      <p>Участок можно удалить, если в нём ещё нет сохранённых посадок</p>
      {Boolean(saveManagedZones.error) && (
        <p role="alert">{message(saveManagedZones.error)}</p>
      )}
    </Dialog>
  );
};
