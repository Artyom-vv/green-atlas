import type { WorkspaceReadyModel } from '../../model/useWorkspaceModel';

export interface WorkspaceAreaDialogProps extends Pick<
  WorkspaceReadyModel,
  | 'inspectorView'
  | 'mapAreaTarget'
  | 'project'
  | 'pendingZone'
  | 'planLocked'
  | 'editorBusy'
  | 'setMapAreaTarget'
  | 'activateTool'
  | 'setSelectedPatternZoneIds'
  | 'setPendingZone'
  | 'setZoneReviewOpen'
> {}

export const WorkspaceAreaDialogPropsFor = (
  model: WorkspaceAreaDialogProps,
): WorkspaceAreaDialogProps => ({
  inspectorView: model.inspectorView,
  mapAreaTarget: model.mapAreaTarget,
  project: model.project,
  pendingZone: model.pendingZone,
  planLocked: model.planLocked,
  editorBusy: model.editorBusy,
  setMapAreaTarget: model.setMapAreaTarget,
  activateTool: model.activateTool,
  setSelectedPatternZoneIds: model.setSelectedPatternZoneIds,
  setPendingZone: model.setPendingZone,
  setZoneReviewOpen: model.setZoneReviewOpen,
});
