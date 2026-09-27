import type { WorkspaceReadyModel } from '../model/useWorkspaceModel';
export interface WorkspaceNewZonesProps extends Pick<
  WorkspaceReadyModel,
  | 'inspectorView'
  | 'project'
  | 'draftZones'
  | 'tool'
  | 'createManualPlan'
  | 'setDraftZones'
  | 'editor'
> {}
export const workspaceNewZonesPropsFor = (
  model: WorkspaceNewZonesProps,
): WorkspaceNewZonesProps => ({
  inspectorView: model.inspectorView,
  project: model.project,
  draftZones: model.draftZones,
  tool: model.tool,
  createManualPlan: model.createManualPlan,
  setDraftZones: model.setDraftZones,
  editor: model.editor,
});
