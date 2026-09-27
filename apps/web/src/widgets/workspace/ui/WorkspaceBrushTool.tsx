import {
  brushBindingPropsFor,
  type BrushBindingOptions,
} from './brushToolBindings';
import type { FC } from 'react';
import { BrushToolPanel } from '@/features/placement/ui/BrushToolPanel';
import type { WorkspaceReadyModel } from '../model/useWorkspaceModel';
import { brushToolBindings } from './brushToolBindings';
export interface WorkspaceBrushToolProps
  extends
    BrushBindingOptions,
    Pick<
      WorkspaceReadyModel,
      | 'applyChanges'
      | 'brushDrawing'
      | 'brushForm'
      | 'brushOperation'
      | 'brushPreview'
      | 'brushStrokes'
      | 'brushWidth'
      | 'selectedPatternZoneIds'
      | 'setBrushOperation'
      | 'setBrushWidth'
      | 'speciesQuery'
    > {}
export const WorkspaceBrushToolPropsFor = (
  model: WorkspaceBrushToolProps,
): WorkspaceBrushToolProps => ({
  ...brushBindingPropsFor(model),
  applyChanges: model.applyChanges,
  brushDrawing: model.brushDrawing,
  brushForm: model.brushForm,
  brushOperation: model.brushOperation,
  brushPreview: model.brushPreview,
  brushStrokes: model.brushStrokes,
  brushWidth: model.brushWidth,
  selectedPatternZoneIds: model.selectedPatternZoneIds,
  setBrushOperation: model.setBrushOperation,
  setBrushWidth: model.setBrushWidth,
  speciesQuery: model.speciesQuery,
});
export const WorkspaceBrushTool: FC<WorkspaceBrushToolProps> = (props) => {
  const {
    applyChanges,
    brushDrawing,
    brushForm,
    brushOperation,
    brushPreview,
    brushStrokes,
    brushWidth,
    previewBrush,
    project,
    selectedPatternZoneIds,
    setBrushOperation,
    setBrushWidth,
    speciesQuery,
  } = props;
  return (
    <BrushToolPanel
      {...brushToolBindings(props)}
      species={speciesQuery.data ?? []}
      applying={applyChanges.isPending}
      form={brushForm}
      strokes={brushStrokes}
      zones={project.planting_zones ?? []}
      zoneIds={selectedPatternZoneIds}
      width={brushWidth}
      operation={brushOperation}
      preview={brushPreview}
      loading={brushDrawing || previewBrush.isPending || applyChanges.isPending}
      onWidth={setBrushWidth}
      onOperation={setBrushOperation}
    />
  );
};
