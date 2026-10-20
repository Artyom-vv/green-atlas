import type { MapViewportOptions } from '@/widgets/map/model/mapViewportOptions';
import type { WorkspaceReadyModel } from '../useWorkspaceModel';
import { zoneCollection } from '@/features/workspace/zoneSelection';

export interface ViewportPresentationProps extends Pick<
  WorkspaceReadyModel,
  | 'changePreview'
  | 'cadSource'
  | 'onCadRenderState'
  | 'onGeometryRenderState'
  | 'draftZones'
  | 'editorBusy'
  | 'focusGeometry'
  | 'growthHorizon'
  | 'handleMapExtent'
  | 'hiddenLayerNames'
  | 'initialExtent'
  | 'issues'
  | 'mapGeometryDelivery'
  | 'mapPanActive'
  | 'mapRenderMode'
  | 'metadataOnlyIds'
  | 'moveLiveCheck'
  | 'placementPreview'
  | 'candidateInspection'
  | 'patternPreview'
  | 'previewPattern'
  | 'planLocked'
  | 'planComparison'
  | 'planObjects'
  | 'previewChanges'
  | 'previewDraft'
  | 'projectHasPlan'
  | 'reviewZones'
  | 'selectedPatternZoneIds'
  | 'tool'
> {
  mapScenarioIssues?: WorkspaceReadyModel['mapScenarioIssues'];
  mapScenarioObjects?: WorkspaceReadyModel['mapScenarioObjects'];
}
export const viewportPresentationPropsFor = (
  props: ViewportPresentationProps,
): ViewportPresentationProps => ({
  changePreview: props.changePreview,
  cadSource: props.cadSource,
  onCadRenderState: props.onCadRenderState,
  onGeometryRenderState: props.onGeometryRenderState,
  draftZones: props.draftZones,
  editorBusy: props.editorBusy,
  focusGeometry: props.focusGeometry,
  growthHorizon: props.growthHorizon,
  handleMapExtent: props.handleMapExtent,
  hiddenLayerNames: props.hiddenLayerNames,
  initialExtent: props.initialExtent,
  issues: props.issues,
  mapScenarioIssues: props.mapScenarioIssues,
  mapScenarioObjects: props.mapScenarioObjects,
  mapGeometryDelivery: props.mapGeometryDelivery,
  mapPanActive: props.mapPanActive,
  mapRenderMode: props.mapRenderMode,
  metadataOnlyIds: props.metadataOnlyIds,
  moveLiveCheck: props.moveLiveCheck,
  placementPreview: props.placementPreview,
  candidateInspection: props.candidateInspection,
  patternPreview: props.patternPreview,
  previewPattern: props.previewPattern,
  planLocked: props.planLocked,
  planComparison: props.planComparison,
  planObjects: props.planObjects,
  previewChanges: props.previewChanges,
  previewDraft: props.previewDraft,
  projectHasPlan: props.projectHasPlan,
  reviewZones: props.reviewZones,
  selectedPatternZoneIds: props.selectedPatternZoneIds,
  tool: props.tool,
});

export const viewportPresentationBindings = ({
  changePreview,
  cadSource,
  onCadRenderState,
  onGeometryRenderState,
  draftZones,
  editorBusy,
  focusGeometry,
  growthHorizon,
  handleMapExtent,
  hiddenLayerNames,
  initialExtent,
  issues,
  mapScenarioIssues,
  mapScenarioObjects,
  mapGeometryDelivery,
  mapPanActive,
  mapRenderMode,
  metadataOnlyIds,
  moveLiveCheck,
  placementPreview,
  candidateInspection,
  patternPreview,
  previewPattern,
  planLocked,
  planComparison,
  planObjects,
  previewChanges,
  previewDraft,
  projectHasPlan,
  reviewZones,
  selectedPatternZoneIds,
  tool,
}: ViewportPresentationProps): Pick<
  MapViewportOptions,
  | 'changeDraft'
  | 'cadSource'
  | 'onCadRenderState'
  | 'onGeometryRenderState'
  | 'metadataOnlyIds'
  | 'editPending'
  | 'interactionDisabled'
  | 'planComparison'
  | 'renderMode'
  | 'geometry'
  | 'geometryRevision'
  | 'initialExtent'
  | 'objects'
  | 'planIssues'
  | 'growthHorizon'
  | 'draftPlantingZones'
  | 'hiddenLayerNames'
  | 'highlightedPlantingZoneIds'
  | 'focusGeometry'
  | 'placementPreview'
  | 'inspectionPreview'
  | 'searchDomains'
  | 'onProbeCoordinate'
  | 'changePreview'
  | 'liveMoveValidation'
  | 'tool'
  | 'onExtentChange'
> => ({
  cadSource,
  onCadRenderState,
  onGeometryRenderState,
  changeDraft:
    previewDraft?.previewId === changePreview?.id
      ? previewDraft?.draft
      : undefined,
  metadataOnlyIds,
  editPending: previewChanges.isPending,
  interactionDisabled: editorBusy || planLocked,
  planComparison: planComparison.mode,
  renderMode: mapRenderMode,
  geometry: mapGeometryDelivery?.feature_collection as
    Record<string, unknown> | undefined,
  geometryRevision: mapGeometryDelivery?.loadedGeometryVersion,
  initialExtent,
  objects: mapScenarioObjects ?? planObjects,
  planIssues: mapScenarioIssues ?? issues,
  growthHorizon,
  draftPlantingZones: !projectHasPlan ? draftZones : reviewZones,
  hiddenLayerNames,
  highlightedPlantingZoneIds: selectedPatternZoneIds,
  focusGeometry: projectHasPlan ? focusGeometry : zoneCollection(draftZones),
  placementPreview,
  inspectionPreview: candidateInspection.marker,
  searchDomains:
    patternPreview?.search_domains ?? previewPattern.progress?.search_domains,
  onProbeCoordinate:
    candidateInspection.picking && !planComparison.active
      ? candidateInspection.probe
      : undefined,
  changePreview,
  liveMoveValidation: moveLiveCheck,
  tool: mapPanActive ? 'pan' : tool,
  onExtentChange: handleMapExtent,
});
