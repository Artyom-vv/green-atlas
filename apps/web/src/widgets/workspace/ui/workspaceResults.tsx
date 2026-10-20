import { HistoryPanel } from '@/entities/plan-history/ui/HistoryPanel';
import { PlantingSchedule } from '@/entities/planting/ui/PlantingSchedule';
import { WorkspaceChecks } from '@/entities/validation/ui/WorkspaceChecks';
import { groupWorkspaceIssues } from '@/entities/validation/model/issueGroups';
import { growthHorizonLabel } from '@/entities/planting-forecast';
import { Icon, IconButton, Tooltip } from '@green/ui';
import {
  CircleCheck,
  Clock3,
  LoaderCircle,
  Redo2,
  Sprout,
  TriangleAlert,
  Undo2,
} from 'lucide-react';
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
> {
  forecast?: {
    year?: number;
    pending: boolean;
    error?: string;
    retry: () => void;
  };
}
export const workspaceResultsPropsFor = (model: WorkspaceReadyModel) => ({
  brushStrokes: model.brushStrokes,
  changePreview: model.changePreview,
  editorBusy: model.editorBusy,
  hasUnsavedWork: model.hasUnsavedWork,
  historyQuery: model.historyQuery,
  issues: model.growthScenario.open ? model.mapScenarioIssues : model.issues,
  forecast: model.growthScenario.open
    ? {
        year: model.growthHorizon,
        pending: model.growthScenario.pending,
        error: model.growthScenario.error,
        retry: model.growthScenario.retry,
      }
    : model.growthScenario.currentPending || model.growthScenario.currentError
      ? {
          pending: model.growthScenario.currentPending,
          error: model.growthScenario.currentError,
          retry: model.growthScenario.retryCurrent,
        }
      : undefined,
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
  forecast,
  planLocked,
  planObjects,
  redoChange,
  rowAxis,
  selectFromExplorer,
  setSpeciesAssignmentOpen,
  speciesNames,
  undoChange,
}: WorkspaceResultsProps) => {
  const groups = groupWorkspaceIssues(issues, planObjects);
  const errors = groups.filter((group) => group.hasError).length;
  const checkSummary =
    forecast?.pending || forecast?.error ? '—' : String(groups.length);
  const checkDetail = forecast?.pending
    ? 'Проверяем прогноз роста'
    : forecast?.error
      ? forecast.error
      : !planObjects.length && !groups.length
        ? 'Пока нечего проверять'
        : groups.length
          ? `Причин ${groups.length}. Причин с ошибками ${errors}. Причин с предупреждениями ${groups.length - errors}`
          : 'Нет адресных замечаний';
  const history = historyQuery.data;
  const historyBusy = editorBusy || planLocked || hasUnsavedWork;
  return {
    actions: {
      checks: (
        <div className="inline-flex min-w-0 items-center">
          {forecast?.year !== undefined && (
            <span className="inline-flex h-8 shrink-0 items-center gap-2 px-2 text-xs text-neutral-600 tabular-nums">
              <Icon size={14} icon={<Clock3 />} />
              <span
                aria-label={`Прогноз роста: ${growthHorizonLabel(forecast.year)}`}
              >
                {growthHorizonLabel(forecast.year)}
              </span>
            </span>
          )}
          <Tooltip content={checkDetail}>
            <span
              tabIndex={0}
              aria-label={checkDetail}
              className="inline-flex h-8 shrink-0 items-center gap-2 px-2 text-xs text-neutral-600 tabular-nums"
            >
              <Icon
                size={14}
                className={
                  groups.length
                    ? errors
                      ? 'text-error'
                      : 'text-warning-strong'
                    : 'text-neutral-600'
                }
                icon={
                  forecast?.pending ? (
                    <LoaderCircle className="animate-spin motion-reduce:animate-none" />
                  ) : groups.length ? (
                    <TriangleAlert />
                  ) : planObjects.length ? (
                    <CircleCheck />
                  ) : (
                    <Sprout />
                  )
                }
              />
              {checkSummary}
            </span>
          </Tooltip>
        </div>
      ),
      schedule: (
        <span className="inline-flex h-8 shrink-0 items-center px-2 text-xs text-neutral-600 tabular-nums">
          Посадок {planObjects.length}
        </span>
      ),
      history: (
        <>
          <Tooltip
            content={
              history
                ? `Записей ${history.entries?.length ?? 0}`
                : 'История пока недоступна'
            }
          >
            <span
              tabIndex={0}
              className="inline-flex h-8 shrink-0 items-center px-2 text-xs text-neutral-600 tabular-nums"
            >
              {history
                ? `Записей ${history.entries?.length ?? 0}`
                : 'Недоступна'}
            </span>
          </Tooltip>
          <div className="flex items-center gap-1 border-l border-neutral-200 pl-1">
            <IconButton
              variant="ghost"
              controlSize="compact"
              icon={Undo2}
              label={
                history?.undo_label
                  ? `Отменить: ${history.undo_label}`
                  : 'Отменить'
              }
              disabled={!history?.can_undo || historyBusy}
              onClick={() => undoChange.mutate()}
            />
            <IconButton
              variant="ghost"
              controlSize="compact"
              icon={Redo2}
              label={
                history?.redo_label
                  ? `Повторить: ${history.redo_label}`
                  : 'Повторить'
              }
              disabled={!history?.can_redo || historyBusy}
              onClick={() => redoChange.mutate()}
            />
          </div>
        </>
      ),
    },
    checks: (
      <WorkspaceChecks
        embedded
        objects={planObjects}
        disabled={
          planLocked ||
          editorBusy ||
          Boolean(changePreview) ||
          brushStrokes.length > 0 ||
          Boolean(rowAxis)
        }
        issues={issues}
        forecast={forecast}
        onLocate={selectFromExplorer}
        onAssign={(ids) => {
          selectFromExplorer(ids);
          setSpeciesAssignmentOpen(true);
        }}
      />
    ),
    schedule: (
      <PlantingSchedule
        embedded
        objects={planObjects}
        speciesNames={speciesNames}
        onSelect={selectFromExplorer}
      />
    ),
    history: (
      <HistoryPanel
        embedded
        history={historyQuery.data}
        busy={editorBusy || planLocked || hasUnsavedWork}
        onUndo={() => undoChange.mutate()}
        onRedo={() => redoChange.mutate()}
      />
    ),
  };
};
