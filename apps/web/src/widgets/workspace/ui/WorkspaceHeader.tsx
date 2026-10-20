import {
  useWorkspaceRecognitionActivity,
  type WorkspaceRecognitionStatus,
} from '@/features/source-preparation';
import type { WorkspaceReadStatusProps } from '@/features/workspace/ui/WorkspaceReadStatus';
import { EditorHeader } from '@/widgets/workbench/ui/EditorHeader';
import { Button, IconButton, Toolbar, Tooltip } from '@green/ui';
import {
  CircleHelp,
  Layers,
  ListChecks,
  Package,
  Redo2,
  ScanLine,
  TriangleAlert,
  Undo2,
} from 'lucide-react';
import type { FC } from 'react';
import { useSceneActivity } from '@/widgets/scene/model/SceneActivityContext';
import type { WorkspaceReadyModel } from '../model/useWorkspaceModel';
import { HeaderOperationIcon } from './HeaderOperationIcon';
import { workspaceFeedback } from '../model/workspaceFeedback';
import {
  HeaderPlanStatus,
  type HeaderPersistenceState,
} from './HeaderPlanStatus';

export interface WorkspaceHeaderProps extends Pick<
  WorkspaceReadyModel,
  | 'cadDisplayWarning'
  | 'changePreview'
  | 'createRelease'
  | 'editorBusy'
  | 'historyQuery'
  | 'leaveWorkspace'
  | 'project'
  | 'projectHasPlan'
  | 'redoChange'
  | 'savingPlan'
  | 'setReleaseOpen'
  | 'undoChange'
> {
  saveStatus: string;
  persistenceState: HeaderPersistenceState;
  writeConfirmed: boolean;
  readStatus: WorkspaceReadStatusProps;
  preparationIssue?: string;
  feedback?: ReturnType<typeof workspaceFeedback>;
}
export const WorkspaceHeaderPropsFor = (
  model: WorkspaceReadyModel,
): WorkspaceHeaderProps => ({
  cadDisplayWarning: model.cadDisplayWarning,
  changePreview: model.changePreview,
  createRelease: model.createRelease,
  editorBusy: model.editorBusy,
  historyQuery: model.historyQuery,
  leaveWorkspace: model.leaveWorkspace,
  project: model.project,
  projectHasPlan: model.projectHasPlan,
  redoChange: model.redoChange,
  savingPlan: model.savingPlan,
  saveStatus: model.planPersistence.status,
  persistenceState:
    model.planPersistence.writeOutcome === 'writing'
      ? 'writing'
      : model.planPersistence.writeOutcome === 'unknown'
        ? 'unknown'
        : model.planPersistence.readRecovery
          ? 'recovery'
          : model.planPersistence.hasDraft
            ? 'draft'
            : 'saved',
  writeConfirmed: model.planPersistence.writeOutcome === 'confirmed',
  preparationIssue: model.preparationIssue,
  feedback: workspaceFeedback({
    placementPending:
      model.previewPattern.isPending ||
      model.previewAutomatic.isPending ||
      model.previewRecommendation.isPending ||
      model.previewChanges.isPending ||
      model.previewBrush.isPending,
    placementPhase: model.previewPattern.phase,
    localToolVisible:
      model.rightOpen &&
      model.ideRightTab === 'tool' &&
      ['pattern_fill', 'pattern_row', 'brush'].includes(model.tool),
    localWriteVisible:
      model.reviewOpen ||
      model.deleteSelectionOpen ||
      Boolean(model.zonePendingDelete) ||
      model.zoneReviewOpen ||
      Boolean(model.pendingZone),
    savingPlan: model.savingPlan,
    releasePending: model.createRelease.isPending,
    releaseVisible: model.releaseOpen,
  }),
  readStatus: {
    issue: model.projectQuery.isError
      ? 'Не удалось прочитать текущую версию проекта'
      : undefined,
    reading: model.projectQuery.isFetching,
    onRetry: () => {
      void model.projectQuery.refetch();
    },
  },
  setReleaseOpen: model.setReleaseOpen,
  undoChange: model.undoChange,
});
export const WorkspaceHeader: FC<WorkspaceHeaderProps> = (props) =>
  props.project.layers?.some((layer) => layer.mapping_confirmed !== true) ? (
    <ObservedWorkspaceHeader {...props} />
  ) : (
    <WorkspaceHeaderContent {...props} />
  );

function ObservedWorkspaceHeader(props: WorkspaceHeaderProps) {
  const recognition = useWorkspaceRecognitionActivity(props.project);
  return <WorkspaceHeaderContent {...props} recognition={recognition} />;
}

function WorkspaceHeaderContent({
  changePreview,
  createRelease,
  editorBusy,
  historyQuery,
  leaveWorkspace,
  project,
  projectHasPlan,
  redoChange,
  savingPlan,
  saveStatus,
  persistenceState,
  writeConfirmed,
  readStatus,
  preparationIssue,
  setReleaseOpen,
  undoChange,
  recognition,
  feedback,
}: WorkspaceHeaderProps & { recognition?: WorkspaceRecognitionStatus }) {
  const scene = useSceneActivity();
  const needsPreparation = Boolean(preparationIssue || project.source_review);
  const sourceDetail = [
    recognition?.label,
    preparationIssue ||
      (project.source_review ? 'Расчёт ещё не подготовлен' : undefined),
  ]
    .filter(Boolean)
    .join('. ');
  const sourceAction = needsPreparation
    ? 'Подготовить расчёт'
    : 'Открыть назначения слоёв';
  const sourceRunning = recognition?.state === 'running';
  const sourceGlyph =
    preparationIssue || recognition?.state === 'failed'
      ? TriangleAlert
      : recognition?.state === 'unknown'
        ? CircleHelp
        : sourceRunning
          ? ScanLine
          : needsPreparation
            ? ListChecks
            : Layers;
  const operationPending =
    sourceRunning ||
    (feedback?.headerWrite ?? (savingPlan || persistenceState === 'writing')) ||
    feedback?.headerPlacement ||
    (feedback?.headerRelease ?? createRelease.isPending) ||
    (readStatus.reading &&
      Boolean(
        readStatus.issue ||
        persistenceState === 'unknown' ||
        persistenceState === 'recovery',
      ));

  return (
    <EditorHeader
      name={project.name}
      onBack={() => leaveWorkspace('/projects')}
      variant="editor"
      pending={operationPending}
      progressSlotRef={scene?.setProgressSlot}
    >
      <div
        ref={scene?.setHeaderSlot}
        aria-label="Состояние вида"
        className="flex min-w-8 shrink-0 items-center gap-2"
      />
      {(recognition || needsPreparation || Boolean(project.layers?.length)) && (
        <div
          role="group"
          aria-label="Исходные данные"
          className="flex items-center"
        >
          <span
            role="status"
            aria-live="polite"
            aria-atomic="true"
            className="sr-only"
          >
            {sourceDetail}
          </span>
          <span className="inline-flex" aria-busy={sourceRunning || undefined}>
            <IconButton
              icon={
                <HeaderOperationIcon
                  icon={sourceGlyph}
                  pending={sourceRunning}
                />
              }
              variant="ghost"
              controlSize="compact"
              label={
                sourceRunning
                  ? `${sourceDetail}. ${sourceAction}`
                  : `${sourceAction}. ${sourceDetail}`
              }
              className={
                sourceRunning
                  ? preparationIssue
                    ? 'text-warning'
                    : 'text-blue-600'
                  : needsPreparation || recognition?.state === 'failed'
                    ? 'text-warning'
                    : 'text-neutral-600'
              }
              onClick={() =>
                leaveWorkspace(
                  `/projects/${project.id}/setup${needsPreparation ? '' : '?section=layers&edit=assignments'}`,
                )
              }
            />
          </span>
        </div>
      )}
      <Toolbar
        label="История изменений"
        className="shrink-0 flex-nowrap rounded-none border-0 bg-transparent p-0"
      >
        <IconButton
          icon={Undo2}
          label="Отменить"
          variant="ghost"
          disabled={
            !historyQuery.data?.can_undo || editorBusy || Boolean(changePreview)
          }
          onClick={() => undoChange.mutate()}
        />
        <IconButton
          icon={Redo2}
          label="Повторить"
          variant="ghost"
          disabled={
            !historyQuery.data?.can_redo || editorBusy || Boolean(changePreview)
          }
          onClick={() => redoChange.mutate()}
        />
      </Toolbar>
      <div
        role="group"
        aria-label="Состояние плана и выпуск"
        className="flex items-center gap-2 border-l border-neutral-200 pl-4"
      >
        <HeaderPlanStatus
          state={savingPlan ? 'writing' : persistenceState}
          label={savingPlan ? 'Сохраняем…' : saveStatus}
          writeConfirmed={writeConfirmed}
          quiet={feedback?.quietPersistence}
          activityLabel={
            feedback?.headerPlacement ? feedback.placementLabel : undefined
          }
          readIssue={readStatus.issue}
          reading={
            readStatus.reading &&
            Boolean(
              readStatus.issue ||
              persistenceState === 'unknown' ||
              persistenceState === 'recovery',
            )
          }
          onRetry={readStatus.onRetry}
        />
        <Tooltip
          content={
            createRelease.isPending
              ? 'Собираем пакет… Открыть состояние выпуска'
              : 'Выпуск CAD-комплекта'
          }
        >
          <span
            className="inline-flex"
            aria-busy={createRelease.isPending || undefined}
          >
            <Button
              startIcon={
                <HeaderOperationIcon
                  icon={Package}
                  pending={feedback?.headerRelease ?? createRelease.isPending}
                />
              }
              variant="primary"
              controlSize="compact"
              aria-label={
                createRelease.isPending
                  ? 'Собираем пакет… Открыть состояние выпуска'
                  : 'Выпуск'
              }
              disabled={
                !projectHasPlan || (editorBusy && !createRelease.isPending)
              }
              onClick={() => setReleaseOpen(true)}
            >
              Выпуск
            </Button>
          </span>
        </Tooltip>
      </div>
    </EditorHeader>
  );
}
