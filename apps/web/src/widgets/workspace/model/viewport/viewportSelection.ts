import type { MapViewportOptions } from '@/widgets/map/model/mapViewportOptions';
import type { WorkspaceReadyModel } from '../useWorkspaceModel';

export interface ViewportSelectionProps extends Pick<
  WorkspaceReadyModel,
  | 'editor'
  | 'handleCoordinate'
  | 'handleMapArea'
  | 'handlePointerCoordinate'
  | 'openRightPanel'
  | 'previewSelectionMoveLive'
  | 'projectHasPlan'
  | 'selectedIds'
  | 'setActiveLayerId'
  | 'setIdeRightTab'
  | 'setMapAreaTarget'
  | 'setMapHoverTarget'
  | 'setMapInspectTarget'
  | 'setPanel'
> {}
export const viewportSelectionPropsFor = (
  props: ViewportSelectionProps,
): ViewportSelectionProps => ({
  editor: props.editor,
  handleCoordinate: props.handleCoordinate,
  handleMapArea: props.handleMapArea,
  handlePointerCoordinate: props.handlePointerCoordinate,
  openRightPanel: props.openRightPanel,
  previewSelectionMoveLive: props.previewSelectionMoveLive,
  projectHasPlan: props.projectHasPlan,
  selectedIds: props.selectedIds,
  setActiveLayerId: props.setActiveLayerId,
  setIdeRightTab: props.setIdeRightTab,
  setMapAreaTarget: props.setMapAreaTarget,
  setMapHoverTarget: props.setMapHoverTarget,
  setMapInspectTarget: props.setMapInspectTarget,
  setPanel: props.setPanel,
});

export const viewportSelectionBindings = ({
  editor,
  handleCoordinate,
  handleMapArea,
  handlePointerCoordinate,
  openRightPanel,
  previewSelectionMoveLive,
  projectHasPlan,
  selectedIds,
  setActiveLayerId,
  setIdeRightTab,
  setMapAreaTarget,
  setMapHoverTarget,
  setMapInspectTarget,
  setPanel,
}: ViewportSelectionProps): Pick<
  MapViewportOptions,
  | 'selectedIds'
  | 'onMapArea'
  | 'onMapHover'
  | 'onMapInspect'
  | 'onSelect'
  | 'onSelectMany'
  | 'onCoordinate'
  | 'onTranslateSelectionEnd'
  | 'onMoveCoordinate'
  | 'onPointerCoordinate'
> => ({
  selectedIds,
  onMapArea: handleMapArea,
  onMapHover: setMapHoverTarget,
  onMapInspect: setMapInspectTarget,
  onSelect: (id, mode = 'replace') => {
    if (!projectHasPlan) return;
    if (!id) {
      if (mode === 'replace') editor.clearSelection();
      return;
    }
    editor.select([id], mode);
    setMapHoverTarget(undefined);
    setMapInspectTarget(undefined);
    setMapAreaTarget(undefined);
    setActiveLayerId(undefined);
    setPanel(null);
    openRightPanel();
    setIdeRightTab('inspector');
  },
  onSelectMany: (ids, mode) => {
    if (!projectHasPlan) return;
    editor.select(ids, mode);
    editor.setTool('select');
    setActiveLayerId(undefined);
    setPanel(null);
    openRightPanel();
    setIdeRightTab('inspector');
  },
  onCoordinate: handleCoordinate,
  onTranslateSelectionEnd: handleCoordinate,
  onMoveCoordinate: previewSelectionMoveLive,
  onPointerCoordinate: handlePointerCoordinate,
});
