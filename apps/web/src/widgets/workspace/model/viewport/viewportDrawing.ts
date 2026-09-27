import type { MapViewportOptions } from '@/widgets/map/model/mapViewportOptions';
import type { WorkspaceReadyModel } from '../useWorkspaceModel';
import { assignmentFromGeometry } from '@/entities/planting-zone/model/plantingZones';

export interface ViewportDrawingProps extends Pick<
  WorkspaceReadyModel,
  | 'addMapArea'
  | 'draftZones'
  | 'editor'
  | 'planLocked'
  | 'project'
  | 'projectHasPlan'
  | 'setPendingZone'
  | 'finishZoneDrawing'
  | 'setZoneReviewOpen'
  | 'zoneDrawingSession'
> {}
export const viewportDrawingPropsFor = (
  props: ViewportDrawingProps,
): ViewportDrawingProps => ({
  addMapArea: props.addMapArea,
  draftZones: props.draftZones,
  editor: props.editor,
  planLocked: props.planLocked,
  project: props.project,
  projectHasPlan: props.projectHasPlan,
  setPendingZone: props.setPendingZone,
  finishZoneDrawing: props.finishZoneDrawing,
  setZoneReviewOpen: props.setZoneReviewOpen,
  zoneDrawingSession: props.zoneDrawingSession,
});

export const viewportDrawingBindings = ({
  addMapArea,
  draftZones,
  editor,
  planLocked,
  project,
  projectHasPlan,
  setPendingZone,
  finishZoneDrawing,
  setZoneReviewOpen,
  zoneDrawingSession,
}: ViewportDrawingProps): Pick<MapViewportOptions, 'onDrawArea'> => ({
  onDrawArea: (geometry) => {
    if (projectHasPlan && zoneDrawingSession && !planLocked) {
      const completed = finishZoneDrawing(zoneDrawingSession);
      if (!completed) return;
      const previous =
        completed.zone ??
        (project.planting_zones ?? []).find(
          (zone) => zone.id === completed.target,
        );
      const zone = previous
        ? { ...previous, geometry }
        : assignmentFromGeometry(
            geometry,
            (project.planting_zones?.length ?? 0) + 1,
            `Участок ${(project.planting_zones?.length ?? 0) + 1}`,
          );
      setPendingZone({
        zone,
        purpose: completed.purpose,
        ...(completed.nextTool && { nextTool: completed.nextTool }),
      });
      setZoneReviewOpen(true);
      return;
    }
    if (projectHasPlan || planLocked) return;
    addMapArea(geometry, `Ручной участок ${draftZones.length + 1}`);
    editor.setTool('select');
  },
});
