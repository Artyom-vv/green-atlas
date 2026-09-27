import { HistoryPanel } from '@/entities/plan-history/ui/HistoryPanel';
import { PlantingSchedule } from '@/entities/planting/ui/PlantingSchedule';
import { WorkspaceChecks } from '@/entities/validation/ui/WorkspaceChecks';
import type { WorkspaceReadyModel } from '../model/useWorkspaceModel';

export interface WorkspaceResultsProps extends Pick<
  WorkspaceReadyModel,
  | 'brushStrokes'
  | 'changePreview'
  | 'editorBusy'
  | 'hasUnsavedWork'
  | 'historyQuery'
  | 'issues'
  | 'planLocked'
  | 'planObjects'
  | 'redoChange'
  | 'rowAxis'
  | 'selectFromExplorer'
  | 'setSpeciesAssignmentOpen'
  | 'speciesNames'
  | 'undoChange'
> {}
export const workspaceResultsPropsFor = (model: WorkspaceReadyModel) => ({
  brushStrokes: model.brushStrokes,
  changePreview: model.changePreview,
  editorBusy: model.editorBusy,
  hasUnsavedWork: model.hasUnsavedWork,
  historyQuery: model.historyQuery,
  issues: model.issues,
  planLocked: model.planLocked,
  planObjects: model.planObjects,
  redoChange: model.redoChange,
  rowAxis: model.rowAxis,
  selectFromExplorer: model.selectFromExplorer,
  setSpeciesAssignmentOpen: model.setSpeciesAssignmentOpen,
  speciesNames: model.speciesNames,
  undoChange: model.undoChange,
});
export const workspaceResults = ({
  brushStrokes,
  changePreview,
  editorBusy,
  hasUnsavedWork,
  historyQuery,
  issues,
  planLocked,
  planObjects,
  redoChange,
  rowAxis,
  selectFromExplorer,
  setSpeciesAssignmentOpen,
  speciesNames,
  undoChange,
}: WorkspaceResultsProps) => ({
  checks: (
    <WorkspaceChecks
      objects={planObjects}
      disabled={
        planLocked ||
        editorBusy ||
        Boolean(changePreview) ||
        brushStrokes.length > 0 ||
        Boolean(rowAxis)
      }
      issues={issues}
      onLocate={selectFromExplorer}
      onAssign={(ids) => {
        selectFromExplorer(ids);
        setSpeciesAssignmentOpen(true);
      }}
    />
  ),
  schedule: (
    <PlantingSchedule
      objects={planObjects}
      speciesNames={speciesNames}
      onSelect={selectFromExplorer}
    />
  ),
  history: (
    <HistoryPanel
      history={historyQuery.data}
      busy={editorBusy || planLocked || hasUnsavedWork}
      onUndo={() => undoChange.mutate()}
      onRedo={() => redoChange.mutate()}
    />
  ),
});
