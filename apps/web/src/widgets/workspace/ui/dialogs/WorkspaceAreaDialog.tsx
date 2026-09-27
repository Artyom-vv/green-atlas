import { assignmentFromGeometry } from '@/entities/planting-zone/model/plantingZones';
import { Button, Dialog, FormActions } from '@green/ui';
import type { FC } from 'react';
import type { WorkspaceAreaDialogProps } from './WorkspaceAreaDialog.props';

export { WorkspaceAreaDialogPropsFor } from './WorkspaceAreaDialog.props';
export type { WorkspaceAreaDialogProps } from './WorkspaceAreaDialog.props';

export const WorkspaceAreaDialog: FC<WorkspaceAreaDialogProps> = ({
  inspectorView,
  mapAreaTarget,
  project,
  pendingZone,
  planLocked,
  editorBusy,
  setMapAreaTarget,
  activateTool,
  setSelectedPatternZoneIds,
  setPendingZone,
  setZoneReviewOpen,
}) => {
  if (!(inspectorView === 'area' && mapAreaTarget && project.plan)) return null;
  const isProjectZone = Boolean(mapAreaTarget.plantingZoneId);
  const canCreateZone =
    mapAreaTarget.selectable && Boolean(mapAreaTarget.geometry);
  const placeInZone = () => {
    const zoneId = mapAreaTarget.plantingZoneId;
    setMapAreaTarget(undefined);
    activateTool('pattern_fill');
    if (zoneId) setSelectedPatternZoneIds([zoneId]);
  };
  const createZone = () => {
    if (!mapAreaTarget.geometry) return;
    const zone = assignmentFromGeometry(
      mapAreaTarget.geometry,
      (project.planting_zones?.length ?? 0) + 1,
      mapAreaTarget.label,
      mapAreaTarget.sourceId,
    );
    setPendingZone({ zone, purpose: 'place' });
    setZoneReviewOpen(true);
    setMapAreaTarget(undefined);
  };
  return (
    <Dialog
      open={!pendingZone}
      title={mapAreaTarget.label}
      onClose={() => setMapAreaTarget(undefined)}
      footer={
        (isProjectZone || canCreateZone) && (
          <FormActions layout="equal">
            <Button
              variant="primary"
              disabled={planLocked || editorBusy}
              onClick={isProjectZone ? placeInZone : createZone}
            >
              {isProjectZone ? 'Разместить здесь' : 'Сделать рабочим участком'}
            </Button>
          </FormActions>
        )
      }
    >
      <div className="grid min-w-0 gap-4">
        <p className="m-0">{mapAreaTarget.detail}</p>
        <p className="m-0 text-xs leading-4 text-neutral-600">
          {isProjectZone ? 'Участок проекта' : 'Объект исходного DXF'}
        </p>
      </div>
    </Dialog>
  );
};
