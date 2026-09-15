import type { MapViewportOptions } from '@/widgets/map/model/mapViewportOptions';
import type { WorkspaceReadyModel } from '../useWorkspaceModel';

export interface ViewportBrushProps extends Pick<
  WorkspaceReadyModel,
  | 'brushOperation'
  | 'brushSettings'
  | 'brushStrokes'
  | 'brushWidth'
  | 'brushZones'
  | 'editorBusy'
  | 'openRightPanel'
  | 'previewBrush'
  | 'previewChanges'
  | 'selectedPatternZoneIds'
  | 'setBrushDrawing'
  | 'setBrushStrokes'
  | 'setIdeRightTab'
> {}
export const viewportBrushPropsFor = (
  props: ViewportBrushProps,
): ViewportBrushProps => ({
  brushOperation: props.brushOperation,
  brushSettings: props.brushSettings,
  brushStrokes: props.brushStrokes,
  brushWidth: props.brushWidth,
  brushZones: props.brushZones,
  editorBusy: props.editorBusy,
  openRightPanel: props.openRightPanel,
  previewBrush: props.previewBrush,
  previewChanges: props.previewChanges,
  selectedPatternZoneIds: props.selectedPatternZoneIds,
  setBrushDrawing: props.setBrushDrawing,
  setBrushStrokes: props.setBrushStrokes,
  setIdeRightTab: props.setIdeRightTab,
});

export const viewportBrushBindings = ({
  brushOperation,
  brushSettings,
  brushStrokes,
  brushWidth,
  brushZones,
  editorBusy,
  openRightPanel,
  previewBrush,
  previewChanges,
  selectedPatternZoneIds,
  setBrushDrawing,
  setBrushStrokes,
  setIdeRightTab,
}: ViewportBrushProps): Pick<
  MapViewportOptions,
  | 'brushSettings'
  | 'brushZones'
  | 'onBrushGesture'
  | 'brushStrokes'
  | 'brushEnabled'
  | 'brushWidthM'
  | 'brushOperation'
  | 'onDrawBrush'
> => ({
  brushSettings,
  brushZones,
  onBrushGesture: (active) => {
    setBrushDrawing(active);
    if (active) {
      previewBrush.reset();
      previewChanges.reset();
    }
  },
  brushStrokes,
  brushEnabled:
    selectedPatternZoneIds.length > 0 &&
    !editorBusy &&
    (brushOperation === 'subtract' ||
      ((brushSettings.composition === 'shrubs' ||
        Boolean(brushSettings.treeSpeciesId)) &&
        (brushSettings.composition === 'trees' ||
          Boolean(brushSettings.shrubSpeciesId)))),
  brushWidthM: brushWidth,
  brushOperation,
  onDrawBrush: (stroke) => {
    setBrushStrokes((current) => [...current, stroke]);
    previewBrush.reset();
    previewChanges.reset();
    openRightPanel();
    setIdeRightTab('tool');
  },
});
