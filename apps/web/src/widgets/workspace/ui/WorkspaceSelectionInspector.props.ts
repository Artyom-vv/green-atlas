import type { WorkspaceReadyModel } from '../model/useWorkspaceModel';
export interface WorkspaceSelectionInspectorProps extends Pick<
  WorkspaceReadyModel,
  | 'inspectorView'
  | 'selectedObject'
  | 'planLocked'
  | 'editorBusy'
  | 'previewSelectionLock'
  | 'sceneOpen'
  | 'sceneReview'
  | 'mapViewport'
  | 'selectedIds'
  | 'changeMapMode'
  | 'activateTool'
  | 'speciesNames'
  | 'growthHorizon'
  | 'setGrowthHorizon'
  | 'setSpeciesAssignmentOpen'
  | 'setDeleteSelectionOpen'
  | 'selectedObjects'
  | 'issues'
> {}
export const workspaceSelectionInspectorPropsFor = (
  model: WorkspaceSelectionInspectorProps,
): WorkspaceSelectionInspectorProps => ({
  inspectorView: model.inspectorView,
  selectedObject: model.selectedObject,
  planLocked: model.planLocked,
  editorBusy: model.editorBusy,
  previewSelectionLock: model.previewSelectionLock,
  sceneOpen: model.sceneOpen,
  sceneReview: model.sceneReview,
  mapViewport: model.mapViewport,
  selectedIds: model.selectedIds,
  changeMapMode: model.changeMapMode,
  activateTool: model.activateTool,
  speciesNames: model.speciesNames,
  growthHorizon: model.growthHorizon,
  setGrowthHorizon: model.setGrowthHorizon,
  setSpeciesAssignmentOpen: model.setSpeciesAssignmentOpen,
  setDeleteSelectionOpen: model.setDeleteSelectionOpen,
  selectedObjects: model.selectedObjects,
  issues: model.issues,
});
