import type { MapViewportOptions } from '@/widgets/map/model/mapViewportOptions';
import type { WorkspaceReadyModel } from '../useWorkspaceModel';

export interface ViewportRowProps extends Pick<
  WorkspaceReadyModel,
  | 'openRightPanel'
  | 'patternPreview'
  | 'previewChanges'
  | 'previewPattern'
  | 'rowAxis'
  | 'rowInputMode'
  | 'rowSettings'
  | 'setIdeRightTab'
  | 'setRowAxis'
  | 'setRowAxisSource'
  | 'setRowDrawingPoints'
  | 'setRowInputMode'
> {}
export const viewportRowPropsFor = (
  props: ViewportRowProps,
): ViewportRowProps => ({
  openRightPanel: props.openRightPanel,
  patternPreview: props.patternPreview,
  previewChanges: props.previewChanges,
  previewPattern: props.previewPattern,
  rowAxis: props.rowAxis,
  rowInputMode: props.rowInputMode,
  rowSettings: props.rowSettings,
  setIdeRightTab: props.setIdeRightTab,
  setRowAxis: props.setRowAxis,
  setRowAxisSource: props.setRowAxisSource,
  setRowDrawingPoints: props.setRowDrawingPoints,
  setRowInputMode: props.setRowInputMode,
});

export const viewportRowBindings = ({
  openRightPanel,
  patternPreview,
  previewChanges,
  previewPattern,
  rowAxis,
  rowInputMode,
  rowSettings,
  setIdeRightTab,
  setRowAxis,
  setRowAxisSource,
  setRowDrawingPoints,
  setRowInputMode,
}: ViewportRowProps): Pick<
  MapViewportOptions,
  | 'rowResultReady'
  | 'rowAxis'
  | 'rowSettings'
  | 'rowInputMode'
  | 'onRowDrawingPoints'
  | 'onDrawAxis'
> => ({
  rowResultReady: Boolean(patternPreview),
  rowAxis,
  rowSettings,
  rowInputMode,
  onRowDrawingPoints: setRowDrawingPoints,
  onDrawAxis: (geometry, source) => {
    setRowInputMode('ready');
    setRowAxis(geometry);
    setRowAxisSource(source);
    previewPattern.reset();
    previewChanges.reset();
    openRightPanel();
    setIdeRightTab('tool');
  },
});
