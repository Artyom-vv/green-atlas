import type { WorkspaceReadyModel } from '../model/useWorkspaceModel';

export interface WorkspaceResourcesProps extends Pick<
  WorkspaceReadyModel,
  | 'activeLayerId'
  | 'changePreview'
  | 'editorBusy'
  | 'focusZones'
  | 'hasUnsavedWork'
  | 'layers'
  | 'leaveWorkspace'
  | 'navigateWorkspace'
  | 'openRightPanel'
  | 'planLocked'
  | 'planObjects'
  | 'project'
  | 'projectId'
  | 'selectFromExplorer'
  | 'selectPatternZones'
  | 'selectedIds'
  | 'selectedPatternZoneIds'
  | 'setActiveLayerId'
  | 'setIdeRightTab'
  | 'setLibraryOpen'
  | 'setPanel'
  | 'setVisibility'
  | 'speciesNames'
  | 'visibility'
  | 'zoneDrawingMode'
> {}
export const WorkspaceResourcesPropsFor = (model: WorkspaceReadyModel) => ({
  activeLayerId: model.activeLayerId,
  changePreview: model.changePreview,
  editorBusy: model.editorBusy,
  focusZones: model.focusZones,
  hasUnsavedWork: model.hasUnsavedWork,
  layers: model.layers,
  leaveWorkspace: model.leaveWorkspace,
  navigateWorkspace: model.navigateWorkspace,
  openRightPanel: model.openRightPanel,
  planLocked: model.planLocked,
  planObjects: model.planObjects,
  project: model.project,
  projectId: model.projectId,
  selectFromExplorer: model.selectFromExplorer,
  selectPatternZones: model.selectPatternZones,
  selectedIds: model.selectedIds,
  selectedPatternZoneIds: model.selectedPatternZoneIds,
  setActiveLayerId: model.setActiveLayerId,
  setIdeRightTab: model.setIdeRightTab,
  setLibraryOpen: model.setLibraryOpen,
  setPanel: model.setPanel,
  setVisibility: model.setVisibility,
  speciesNames: model.speciesNames,
  visibility: model.visibility,
  zoneDrawingMode: model.zoneDrawingMode,
});
