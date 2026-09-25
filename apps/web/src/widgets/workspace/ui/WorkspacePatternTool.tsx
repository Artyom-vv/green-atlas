import {
  patternBindingPropsFor,
  type PatternBindingOptions,
} from './patternToolBindings';
import { PatternToolPanel } from '@/features/placement';
import { errorMessage as message } from '@/shared/errors/errorMessage';
import type { FC } from 'react';
import { patternToolBindings } from './patternToolBindings';
import type { WorkspaceReadyModel } from '../model/useWorkspaceModel';

export interface WorkspacePatternToolProps
  extends
    PatternBindingOptions,
    Pick<
      WorkspaceReadyModel,
      | 'growthHorizon'
      | 'candidateInspection'
      | 'patternForm'
      | 'placementAreaDrawing'
      | 'placementMasksQuery'
      | 'rowAxisSource'
      | 'rowDrawingPoints'
      | 'rowInputMode'
      | 'savePlacementZone'
      | 'selectPatternZones'
      | 'selectedPatternZoneIds'
      | 'setGrowthHorizon'
      | 'speciesQuery'
      | 'tool'
      | 'zoneSpeciesShortlistQuery'
    > {}
export const WorkspacePatternToolPropsFor = (
  model: WorkspacePatternToolProps,
): WorkspacePatternToolProps => ({
  ...patternBindingPropsFor(model),
  growthHorizon: model.growthHorizon,
  candidateInspection: model.candidateInspection,
  patternForm: model.patternForm,
  placementAreaDrawing: model.placementAreaDrawing,
  placementMasksQuery: model.placementMasksQuery,
  rowAxisSource: model.rowAxisSource,
  rowDrawingPoints: model.rowDrawingPoints,
  rowInputMode: model.rowInputMode,
  savePlacementZone: model.savePlacementZone,
  selectPatternZones: model.selectPatternZones,
  selectedPatternZoneIds: model.selectedPatternZoneIds,
  setGrowthHorizon: model.setGrowthHorizon,
  speciesQuery: model.speciesQuery,
  tool: model.tool,
  zoneSpeciesShortlistQuery: model.zoneSpeciesShortlistQuery,
});
export const WorkspacePatternTool: FC<WorkspacePatternToolProps> = (props) => {
  const {
    applyChanges,
    growthHorizon,
    patternForm,
    patternPreview,
    placementAreaDrawing,
    placementMasksQuery,
    previewPattern,
    project,
    rowAxis,
    rowAxisSource,
    rowDrawingPoints,
    rowInputMode,
    savePlacementZone,
    selectPatternZones,
    selectedPatternZoneIds,
    setGrowthHorizon,
    speciesQuery,
    tool,
    zoneSpeciesShortlistQuery,
  } = props;
  return (
    <PatternToolPanel
      {...patternToolBindings(props)}
      calculating={previewPattern.isPending}
      guided={tool !== 'pattern_row'}
      mode={tool === 'pattern_row' ? 'row' : 'fill'}
      zones={project.planting_zones ?? []}
      species={speciesQuery.data ?? []}
      shortlist={zoneSpeciesShortlistQuery.data}
      shortlistLoading={zoneSpeciesShortlistQuery.isLoading}
      placementMasks={placementMasksQuery.data}
      axis={rowAxis}
      axisSource={rowAxisSource}
      axisMode={rowInputMode}
      axisDrawingPoints={rowDrawingPoints}
      form={patternForm}
      selectedZoneIds={selectedPatternZoneIds}
      drawingZone={placementAreaDrawing}
      preview={patternPreview}
      preparation={previewPattern.progress}
      inspection={props.candidateInspection}
      growthHorizon={growthHorizon}
      onGrowthHorizon={setGrowthHorizon}
      loading={
        previewPattern.isPending ||
        applyChanges.isPending ||
        savePlacementZone.isPending
      }
      error={
        previewPattern.error
          ? message(previewPattern.error)
          : zoneSpeciesShortlistQuery.error
          ? message(zoneSpeciesShortlistQuery.error)
          : placementMasksQuery.error
            ? message(placementMasksQuery.error)
            : undefined
      }
      onSelectedZoneIdsChange={selectPatternZones}
    />
  );
};
