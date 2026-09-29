import type { WorkspaceReadyModel } from '../model/useWorkspaceModel';
export interface WorkspaceRecommendationToolProps extends Pick<
  WorkspaceReadyModel,
  | 'applyChanges'
  | 'automaticPreview'
  | 'buildingTargets'
  | 'changeMapMode'
  | 'editor'
  | 'growthHorizon'
  | 'previewChanges'
  | 'previewRecommendation'
  | 'previewAutomatic'
  | 'project'
  | 'recommendationForm'
  | 'recommendationOpen'
  | 'recommendationPreview'
  | 'sceneOpen'
  | 'selectPatternZones'
  | 'selectedPatternZoneIds'
  | 'setBuildingScreenActive'
  | 'setGrowthHorizon'
  | 'setRecommendationOpen'
  | 'speciesNames'
> {}
export const workspaceRecommendationPropsFor = (
  model: WorkspaceRecommendationToolProps,
): WorkspaceRecommendationToolProps => ({
  applyChanges: model.applyChanges,
  automaticPreview: model.automaticPreview,
  buildingTargets: model.buildingTargets,
  changeMapMode: model.changeMapMode,
  editor: model.editor,
  growthHorizon: model.growthHorizon,
  previewChanges: model.previewChanges,
  previewRecommendation: model.previewRecommendation,
  previewAutomatic: model.previewAutomatic,
  project: model.project,
  recommendationForm: model.recommendationForm,
  recommendationOpen: model.recommendationOpen,
  recommendationPreview: model.recommendationPreview,
  sceneOpen: model.sceneOpen,
  selectPatternZones: model.selectPatternZones,
  selectedPatternZoneIds: model.selectedPatternZoneIds,
  setBuildingScreenActive: model.setBuildingScreenActive,
  setGrowthHorizon: model.setGrowthHorizon,
  setRecommendationOpen: model.setRecommendationOpen,
  speciesNames: model.speciesNames,
});
