import type { MapViewportOptions } from '@/widgets/map/model/mapViewportOptions';
import type { WorkspaceReadyModel } from '../useWorkspaceModel';

export interface ViewportPresentationProps extends Pick<
  WorkspaceReadyModel,
  | 'assistant'
  | 'assistantPreview'
  | 'assistantZonePreview'
  | 'changePreview'
  | 'cadSource'
  | 'onCadRenderState'
  | 'draftZones'
  | 'editorBusy'
  | 'focusGeometry'
  | 'growthHorizon'
  | 'handleMapExtent'
  | 'hiddenLayerNames'
  | 'initialExtent'
  | 'mapGeometryDelivery'
  | 'mapPanActive'
  | 'mapRenderMode'
  | 'metadataOnlyIds'
  | 'moveLiveCheck'
  | 'placementPreview'
  | 'planLocked'
  | 'planObjects'
  | 'previewChanges'
  | 'previewDraft'
  | 'projectHasPlan'
  | 'reviewZones'
  | 'selectedPatternZoneIds'
  | 'tool'
> {}
export const viewportPresentationPropsFor = (
  props: ViewportPresentationProps,
): ViewportPresentationProps => ({
  assistant: props.assistant,
  assistantPreview: props.assistantPreview,
  assistantZonePreview: props.assistantZonePreview,
  changePreview: props.changePreview,
  cadSource: props.cadSource,
  onCadRenderState: props.onCadRenderState,
  draftZones: props.draftZones,
  editorBusy: props.editorBusy,
  focusGeometry: props.focusGeometry,
  growthHorizon: props.growthHorizon,
  handleMapExtent: props.handleMapExtent,
  hiddenLayerNames: props.hiddenLayerNames,
  initialExtent: props.initialExtent,
  mapGeometryDelivery: props.mapGeometryDelivery,
  mapPanActive: props.mapPanActive,
  mapRenderMode: props.mapRenderMode,
  metadataOnlyIds: props.metadataOnlyIds,
  moveLiveCheck: props.moveLiveCheck,
  placementPreview: props.placementPreview,
  planLocked: props.planLocked,
  planObjects: props.planObjects,
  previewChanges: props.previewChanges,
  previewDraft: props.previewDraft,
  projectHasPlan: props.projectHasPlan,
  reviewZones: props.reviewZones,
  selectedPatternZoneIds: props.selectedPatternZoneIds,
  tool: props.tool,
});

export const viewportPresentationBindings = ({
  assistant,
  assistantPreview,
  assistantZonePreview,
  changePreview,
  cadSource,
  onCadRenderState,
  draftZones,
  editorBusy,
  focusGeometry,
  growthHorizon,
  handleMapExtent,
  hiddenLayerNames,
  initialExtent,
  mapGeometryDelivery,
  mapPanActive,
  mapRenderMode,
  metadataOnlyIds,
  moveLiveCheck,
  placementPreview,
  planLocked,
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
  | 'metadataOnlyIds'
  | 'editPending'
  | 'interactionDisabled'
  | 'renderMode'
  | 'geometry'
  | 'geometryRevision'
  | 'initialExtent'
  | 'objects'
  | 'growthHorizon'
  | 'draftPlantingZones'
  | 'hiddenLayerNames'
  | 'highlightedPlantingZoneIds'
  | 'focusGeometry'
  | 'placementPreview'
  | 'changePreview'
  | 'zoneChangePreview'
  | 'liveMoveValidation'
  | 'tool'
  | 'onExtentChange'
> => ({
  cadSource,
  onCadRenderState,
  changeDraft: assistantPreview
    ? assistant.assistantMode === 'autonomous'
      ? undefined
      : assistant.proposal?.draft
    : previewDraft?.previewId === changePreview?.id
      ? previewDraft?.draft
      : undefined,
  metadataOnlyIds,
  editPending: previewChanges.isPending,
  interactionDisabled: editorBusy || planLocked,
  renderMode: mapRenderMode,
  geometry: mapGeometryDelivery?.feature_collection as
    Record<string, unknown> | undefined,
  geometryRevision: mapGeometryDelivery?.loadedGeometryVersion,
  initialExtent,
  objects: planObjects,
  growthHorizon,
  draftPlantingZones: !projectHasPlan ? draftZones : reviewZones,
  hiddenLayerNames,
  highlightedPlantingZoneIds: selectedPatternZoneIds,
  focusGeometry,
  placementPreview,
  changePreview: assistantPreview ?? changePreview,
  zoneChangePreview: assistantZonePreview,
  liveMoveValidation: moveLiveCheck,
  tool: mapPanActive ? 'pan' : tool,
  onExtentChange: handleMapExtent,
});
