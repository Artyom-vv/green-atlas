import { PlantingZoneManager } from '@/features/planting-zones/ui/PlantingZoneManager';
import { errorMessage as message } from '@/shared/errors/errorMessage';
import { Dialog } from '@green/ui';
import type { FC } from 'react';
import type { WorkspaceReadyModel } from '../../model/useWorkspaceModel';

export interface WorkspaceZoneManagerDialogProps extends Pick<
  WorkspaceReadyModel,
  | 'project'
  | 'panel'
  | 'zoneDrawingMode'
  | 'zonePendingDelete'
  | 'pendingZone'
  | 'setPanel'
  | 'draftZones'
  | 'selectedPatternZoneIds'
  | 'selectPatternZones'
  | 'plantingZoneUsage'
  | 'saveManagedZones'
  | 'focusZones'
  | 'setDraftZones'
  | 'sceneOpen'
  | 'changeMapMode'
  | 'setSelectedPatternZoneIds'
  | 'beginZoneDrawing'
  | 'cancelZoneDrawing'
  | 'setZonePendingDelete'
> {}

export const WorkspaceZoneManagerDialogPropsFor = (
  model: WorkspaceZoneManagerDialogProps,
): WorkspaceZoneManagerDialogProps => ({
  project: model.project,
  panel: model.panel,
  zoneDrawingMode: model.zoneDrawingMode,
  zonePendingDelete: model.zonePendingDelete,
  pendingZone: model.pendingZone,
  setPanel: model.setPanel,
  draftZones: model.draftZones,
  selectedPatternZoneIds: model.selectedPatternZoneIds,
  selectPatternZones: model.selectPatternZones,
  plantingZoneUsage: model.plantingZoneUsage,
  saveManagedZones: model.saveManagedZones,
  focusZones: model.focusZones,
  setDraftZones: model.setDraftZones,
  sceneOpen: model.sceneOpen,
  changeMapMode: model.changeMapMode,
  setSelectedPatternZoneIds: model.setSelectedPatternZoneIds,
  beginZoneDrawing: model.beginZoneDrawing,
  cancelZoneDrawing: model.cancelZoneDrawing,
  setZonePendingDelete: model.setZonePendingDelete,
});

export const WorkspaceZoneManagerDialog: FC<
  WorkspaceZoneManagerDialogProps
> = ({
  project,
  panel,
  zoneDrawingMode,
  zonePendingDelete,
  pendingZone,
  setPanel,
  draftZones,
  selectedPatternZoneIds,
  selectPatternZones,
  plantingZoneUsage,
  saveManagedZones,
  focusZones,
  setDraftZones,
  sceneOpen,
  changeMapMode,
  setSelectedPatternZoneIds,
  beginZoneDrawing,
  cancelZoneDrawing,
  setZonePendingDelete,
}) => {
  if (!(project.plan && panel === 'zones')) return null;
  return (
    <Dialog
      open={!zoneDrawingMode && !zonePendingDelete && !pendingZone}
      title="Рабочие участки"
      size="wide"
      stableHeight
      keepMounted
      onClose={() => setPanel(null)}
    >
      <PlantingZoneManager
        zones={draftZones}
        activeIds={selectedPatternZoneIds}
        onSelectionChange={selectPatternZones}
        zoneUsage={plantingZoneUsage}
        drawing={Boolean(zoneDrawingMode)}
        saving={saveManagedZones.isPending}
        error={
          saveManagedZones.error ? message(saveManagedZones.error) : undefined
        }
        onFocus={(zone) => {
          focusZones([zone]);
          setPanel(null);
        }}
        onRename={(zone, label) => {
          if (label === zone.label) return;
          const zones = draftZones.map((item) =>
            item.id === zone.id ? { ...item, label } : item,
          );
          setDraftZones(zones);
          saveManagedZones.mutate({ zones, focusId: zone.id });
        }}
        onRedraw={(zone) => {
          if (sceneOpen) changeMapMode('2d');
          if (!zone.id) return;
          setSelectedPatternZoneIds([zone.id]);
          beginZoneDrawing(zone.id);
        }}
        onDelete={setZonePendingDelete}
        onDraw={() => {
          if (sceneOpen) changeMapMode('2d');
          beginZoneDrawing('new');
        }}
        onCancelDraw={cancelZoneDrawing}
      />
    </Dialog>
  );
};
