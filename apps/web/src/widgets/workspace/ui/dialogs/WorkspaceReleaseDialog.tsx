import { ReleasePanel } from '@/features/project-release/ui/ReleasePanel';
import { downloadReleaseFile } from '@/features/project-release/api/releases';
import { errorMessage as message } from '@/shared/errors/errorMessage';
import { Button, Dialog, InlineMessage } from '@green/ui';
import type { FC } from 'react';
import type { WorkspaceReadyModel } from '../../model/useWorkspaceModel';

export interface WorkspaceReleaseDialogProps extends Pick<
  WorkspaceReadyModel,
  | 'releaseOpen'
  | 'setReleaseOpen'
  | 'releaseState'
  | 'project'
  | 'projectId'
  | 'release'
  | 'setGrowthHorizon'
  | 'createRelease'
> {}

export const WorkspaceReleaseDialogPropsFor = (
  model: WorkspaceReleaseDialogProps,
): WorkspaceReleaseDialogProps => ({
  releaseOpen: model.releaseOpen,
  setReleaseOpen: model.setReleaseOpen,
  releaseState: model.releaseState,
  project: model.project,
  projectId: model.projectId,
  release: model.release,
  setGrowthHorizon: model.setGrowthHorizon,
  createRelease: model.createRelease,
});

export const WorkspaceReleaseDialog: FC<WorkspaceReleaseDialogProps> = ({
  releaseOpen,
  setReleaseOpen,
  releaseState,
  project,
  projectId,
  release,
  setGrowthHorizon,
  createRelease,
}) => {
  return (
    <Dialog
      open={releaseOpen}
      title="Выпуск проекта"
      onClose={() => setReleaseOpen(false)}
    >
      {releaseState.restoring ? (
        <div className="grid gap-3">
          <p role="status">Восстанавливаем последний пакет…</p>
        </div>
      ) : releaseState.restoreError ? (
        <div className="grid gap-3">
          <InlineMessage tone="error">
            Не удалось загрузить последний пакет.{' '}
            {message(releaseState.restoreError)}
          </InlineMessage>
          <Button
            variant="secondary"
            onClick={() => void releaseState.retryRestore()}
          >
            Повторить загрузку пакета
          </Button>
        </div>
      ) : (
        project.plan && (
          <ReleasePanel
            key={projectId}
            plan={project.plan}
            geometryVersion={project.geometry_version}
            release={release}
            formMethods={releaseState.formMethods}
            formOpen={releaseState.formOpen}
            hasDraft={releaseState.hasDraft}
            storageAvailable={releaseState.storageAvailable}
            draftStale={releaseState.stale}
            draftContext={releaseState.draftContext}
            draftNotice={releaseState.notice}
            submissionUnknown={releaseState.submissionUnknown}
            onOpenForm={releaseState.openForm}
            onShowFiles={releaseState.showFiles}
            onClearDraft={releaseState.clearDraft}
            onReviewContext={releaseState.reviewContext}
            onGrowthHorizon={setGrowthHorizon}
            loading={createRelease.isPending}
            error={
              createRelease.error ? message(createRelease.error) : undefined
            }
            onCreate={() => createRelease.mutate()}
            onDownload={downloadReleaseFile}
          />
        )
      )}
    </Dialog>
  );
};
