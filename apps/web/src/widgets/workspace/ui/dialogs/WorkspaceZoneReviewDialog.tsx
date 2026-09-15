import { ZoneReview } from '@/entities/planting-zone/ui/ZoneReview';
import { errorMessage as message } from '@/shared/errors/errorMessage';
import { Button, Dialog, FormActions } from '@green/ui';
import type { FC } from 'react';
import type { WorkspaceReadyModel } from '../../model/useWorkspaceModel';

export interface WorkspaceZoneReviewDialogProps extends Pick<
  WorkspaceReadyModel,
  | 'pendingZone'
  | 'zoneReviewOpen'
  | 'zoneReviewQuery'
  | 'editorBusy'
  | 'project'
  | 'redrawZone'
  | 'cancelZoneReview'
  | 'setZoneReviewOpen'
  | 'saveManagedZones'
  | 'savePlacementZone'
> {}

export const WorkspaceZoneReviewDialogPropsFor = (
  model: WorkspaceZoneReviewDialogProps,
): WorkspaceZoneReviewDialogProps => ({
  pendingZone: model.pendingZone,
  zoneReviewOpen: model.zoneReviewOpen,
  zoneReviewQuery: model.zoneReviewQuery,
  editorBusy: model.editorBusy,
  project: model.project,
  redrawZone: model.redrawZone,
  cancelZoneReview: model.cancelZoneReview,
  setZoneReviewOpen: model.setZoneReviewOpen,
  saveManagedZones: model.saveManagedZones,
  savePlacementZone: model.savePlacementZone,
});

export const WorkspaceZoneReviewDialog: FC<WorkspaceZoneReviewDialogProps> = ({
  pendingZone,
  zoneReviewOpen,
  zoneReviewQuery,
  editorBusy,
  project,
  redrawZone,
  cancelZoneReview,
  setZoneReviewOpen,
  saveManagedZones,
  savePlacementZone,
}) => {
  if (!pendingZone) return null;
  return (
    <Dialog
      open={zoneReviewOpen}
      title={
        zoneReviewQuery.data?.can_save
          ? 'Сохранить рабочий участок?'
          : 'Проверка границ участка'
      }
      onClose={() => setZoneReviewOpen(false)}
      footer={
        <FormActions layout="equal" minItemWidth="12rem">
          <Button
            variant="secondary"
            disabled={editorBusy}
            onClick={redrawZone}
          >
            Перерисовать
          </Button>
          <Button
            variant="secondary"
            disabled={editorBusy}
            onClick={cancelZoneReview}
          >
            Отменить
          </Button>
          <Button
            variant="primary"
            disabled={
              !zoneReviewQuery.data?.can_save || zoneReviewQuery.isFetching
            }
            loading={saveManagedZones.isPending || savePlacementZone.isPending}
            onClick={() => {
              if (pendingZone.purpose === 'place')
                savePlacementZone.mutate({
                  zone: pendingZone.zone,
                  nextTool: pendingZone.nextTool,
                });
              else
                saveManagedZones.mutate({
                  zones: [
                    ...(project.planting_zones ?? []).filter(
                      (zone) => zone.id !== pendingZone.zone.id,
                    ),
                    pendingZone.zone,
                  ],
                  focusId: pendingZone.zone.id,
                });
            }}
          >
            Сохранить участок
          </Button>
        </FormActions>
      }
    >
      <ZoneReview
        zone={pendingZone.zone}
        zones={project.planting_zones ?? []}
        preview={zoneReviewQuery.data}
      />
      {Boolean(zoneReviewQuery.error) && (
        <>
          <p role="alert">{message(zoneReviewQuery.error)}</p>
          <Button
            variant="secondary"
            onClick={() => void zoneReviewQuery.refetch()}
          >
            Повторить проверку
          </Button>
        </>
      )}
    </Dialog>
  );
};
