import type { WorkspaceReadyModel } from '../model/useWorkspaceModel';
export interface WorkspaceRecommendationToolProps extends Pick<
  WorkspaceReadyModel,
  | 'applyChanges'
  | 'automaticPreview'
  | 'changeMapMode'
  | 'editor'
  | 'growthHorizon'
  | 'previewChanges'
  | 'previewRecommendation'
  | 'previewAutomatic'
  | 'project'
  | 'recommendationForm'
  | 'recommendationPreview'
  | 'sceneOpen'
  | 'selectPatternZones'
  | 'selectedPatternZoneIds'
  | 'setGrowthHorizon'
  | 'setRecommendationOpen'
  | 'speciesNames'
> {}
export const workspaceRecommendationPropsFor = (
  model: WorkspaceRecommendationToolProps,
): WorkspaceRecommendationToolProps => ({
  applyChanges: model.applyChanges,
  automaticPreview: model.automaticPreview,
  changeMapMode: model.changeMapMode,
  editor: model.editor,
  growthHorizon: model.growthHorizon,
  previewChanges: model.previewChanges,
  previewRecommendation: model.previewRecommendation,
  previewAutomatic: model.previewAutomatic,
  project: model.project,
  recommendationForm: model.recommendationForm,
  recommendationPreview: model.recommendationPreview,
  sceneOpen: model.sceneOpen,
  selectPatternZones: model.selectPatternZones,
  selectedPatternZoneIds: model.selectedPatternZoneIds,
  setGrowthHorizon: model.setGrowthHorizon,
  setRecommendationOpen: model.setRecommendationOpen,
  speciesNames: model.speciesNames,
});
