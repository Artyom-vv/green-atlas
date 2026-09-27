import type { WorkspaceReadyModel } from '../model/useWorkspaceModel';
export interface WorkspaceSourceInspectorProps extends Pick<
  WorkspaceReadyModel,
  | 'inspectorView'
  | 'planLocked'
  | 'sourceWarnings'
  | 'sourceImportStatus'
  | 'openLeftPanel'
  | 'navigate'
  | 'projectId'
> {}
export const workspaceSourceInspectorPropsFor = (
  model: WorkspaceSourceInspectorProps,
): WorkspaceSourceInspectorProps => ({
  inspectorView: model.inspectorView,
  planLocked: model.planLocked,
  sourceWarnings: model.sourceWarnings,
  sourceImportStatus: model.sourceImportStatus,
  openLeftPanel: model.openLeftPanel,
  navigate: model.navigate,
  projectId: model.projectId,
});
