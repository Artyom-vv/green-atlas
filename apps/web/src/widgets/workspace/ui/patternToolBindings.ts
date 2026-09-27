import type { WorkspaceReadyModel } from '../model/useWorkspaceModel';
import { rowSketchFrame } from '@/entities/planting';
import type { PatternToolPanelProps } from '@/features/placement/ui/PatternToolPanel';
import type { PatternPreviewRequest } from '@green/api-client';

export interface PatternBindingOptions extends Pick<
  WorkspaceReadyModel,
  | 'previewPattern'
  | 'previewChanges'
  | 'mapViewport'
  | 'setRowInputMode'
  | 'setRowDrawingPoints'
  | 'rowAxis'
  | 'rowSettings'
  | 'setRowAxis'
  | 'beginPlacementZoneDrawing'
  | 'placementAreaDrawing'
  | 'cancelZoneDrawing'
  | 'editor'
  | 'sceneOpen'
  | 'changeMapMode'
  | 'project'
  | 'patternPreview'
  | 'applyChanges'
  | 'setRowAxisSource'
> {}
export const patternBindingPropsFor = (
  model: PatternBindingOptions,
): PatternBindingOptions => ({
  previewPattern: model.previewPattern,
  previewChanges: model.previewChanges,
  mapViewport: model.mapViewport,
  setRowInputMode: model.setRowInputMode,
  setRowDrawingPoints: model.setRowDrawingPoints,
  rowAxis: model.rowAxis,
  rowSettings: model.rowSettings,
  setRowAxis: model.setRowAxis,
  beginPlacementZoneDrawing: model.beginPlacementZoneDrawing,
  placementAreaDrawing: model.placementAreaDrawing,
  cancelZoneDrawing: model.cancelZoneDrawing,
  editor: model.editor,
  sceneOpen: model.sceneOpen,
  changeMapMode: model.changeMapMode,
  project: model.project,
  patternPreview: model.patternPreview,
  applyChanges: model.applyChanges,
  setRowAxisSource: model.setRowAxisSource,
});

type PatternBindings = Pick<
  PatternToolPanelProps,
  | 'onCancelCalculation'
  | 'onAxisModeChange'
  | 'onFinishAxis'
  | 'onFitAxis'
  | 'onReverseAxis'
  | 'onDrawZone'
  | 'onPreview'
  | 'onApply'
  | 'onResetPreview'
  | 'onCancel'
>;

/** Connects one mounted form to the map and existing preview controllers. */
export function patternToolBindings(
  options: PatternBindingOptions,
): PatternBindings {
  const resetPreview = () => {
    options.previewPattern.reset();
    options.previewChanges.reset();
  };
  return {
    onCancelCalculation: () => options.previewPattern.reset(),
    onAxisModeChange: (mode) => {
      options.mapViewport.current?.abortRowDrawing();
      options.setRowInputMode(mode);
      options.setRowDrawingPoints(0);
      resetPreview();
    },
    onFinishAxis: () => options.mapViewport.current?.finishRowDrawing(),
    onFitAxis: () => {
      if (options.rowAxis)
        options.mapViewport.current?.fitGeometry(
          rowSketchFrame(options.rowAxis, options.rowSettings),
        );
    },
    onReverseAxis: () => {
      if (options.rowAxis)
        options.setRowAxis({
          ...options.rowAxis,
          coordinates: [...options.rowAxis.coordinates].reverse(),
        });
      resetPreview();
    },
    onDrawZone: options.beginPlacementZoneDrawing,
    onPreview: (draft) => {
      if (options.sceneOpen) options.changeMapMode('2d');
      options.previewPattern.mutate({
        ...draft,
        base_plan_version: options.project.plan!.version,
      } as PatternPreviewRequest);
    },
    onApply: () => {
      if (options.patternPreview?.change_set?.can_apply)
        options.applyChanges.mutate(options.patternPreview.change_set);
    },
    onResetPreview: resetPreview,
    onCancel: () => {
      if (options.placementAreaDrawing) {
        options.cancelZoneDrawing();
        return;
      }
      resetPreview();
      options.setRowAxis(undefined);
      options.setRowAxisSource(undefined);
      options.editor.setTool('select');
    },
  };
}
