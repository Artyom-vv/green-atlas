import type { FC } from 'react';
import type { WorkspaceReadyModel } from '../../model/useWorkspaceModel';
import { SceneResourceDiagnostics } from '@/widgets/scene/ui/SceneResourceDiagnostics';
import { OperationErrorDialog } from '@/widgets/workbench/ui/dialogs/OperationErrorDialog';
import { LeaveWorkspaceDialog } from '@/widgets/workbench/ui/dialogs/LeaveWorkspaceDialog';
import { FinishPlacementDialog } from '@/widgets/workbench/ui/dialogs/FinishPlacementDialog';
import {
  WorkspaceAreaDialog,
  WorkspaceAreaDialogPropsFor,
  type WorkspaceAreaDialogProps,
} from './WorkspaceAreaDialog';
import {
  WorkspaceDeletePlantingsDialog,
  WorkspaceDeletePlantingsDialogPropsFor,
  type WorkspaceDeletePlantingsDialogProps,
} from './WorkspaceDeletePlantingsDialog';
import {
  WorkspaceDeleteZoneDialog,
  WorkspaceDeleteZoneDialogPropsFor,
  type WorkspaceDeleteZoneDialogProps,
} from './WorkspaceDeleteZoneDialog';
import {
  WorkspaceLibraryDialog,
  WorkspaceLibraryDialogPropsFor,
  type WorkspaceLibraryDialogProps,
} from './WorkspaceLibraryDialog';
import {
  WorkspacePlanReviewDialog,
  WorkspacePlanReviewDialogPropsFor,
  type WorkspacePlanReviewDialogProps,
} from './WorkspacePlanReviewDialog';
import {
  WorkspaceReleaseDialog,
  WorkspaceReleaseDialogPropsFor,
  type WorkspaceReleaseDialogProps,
} from './WorkspaceReleaseDialog';
import {
  WorkspaceSpeciesDialog,
  WorkspaceSpeciesDialogPropsFor,
  type WorkspaceSpeciesDialogProps,
} from './WorkspaceSpeciesDialog';
import {
  WorkspaceToolCancelDialog,
  WorkspaceToolCancelDialogPropsFor,
  type WorkspaceToolCancelDialogProps,
} from './WorkspaceToolCancelDialog';
import {
  WorkspaceZoneManagerDialog,
  WorkspaceZoneManagerDialogPropsFor,
  type WorkspaceZoneManagerDialogProps,
} from './WorkspaceZoneManagerDialog';
import {
  WorkspaceZoneReviewDialog,
  WorkspaceZoneReviewDialogPropsFor,
  type WorkspaceZoneReviewDialogProps,
} from './WorkspaceZoneReviewDialog';

export interface WorkspaceDialogsProps
  extends
    WorkspaceAreaDialogProps,
    WorkspaceDeletePlantingsDialogProps,
    WorkspaceDeleteZoneDialogProps,
    WorkspaceLibraryDialogProps,
    WorkspacePlanReviewDialogProps,
    WorkspaceReleaseDialogProps,
    WorkspaceSpeciesDialogProps,
    WorkspaceToolCancelDialogProps,
    WorkspaceZoneManagerDialogProps,
    WorkspaceZoneReviewDialogProps,
    Pick<
      WorkspaceReadyModel,
      | 'operationError'
      | 'leaveWorkspace'
      | 'project'
      | 'dismissedOperationError'
      | 'reviewOpen'
      | 'deleteSelectionOpen'
      | 'zonePendingDelete'
      | 'setDismissedOperationError'
      | 'reloadAfterConflict'
      | 'navigationBlocker'
      | 'navigationPending'
      | 'pendingScene'
      | 'setPendingScene'
    > {}

export const WorkspaceDialogsPropsFor = (
  model: WorkspaceDialogsProps,
): WorkspaceDialogsProps => ({
  ...WorkspaceAreaDialogPropsFor(model),
  ...WorkspaceDeletePlantingsDialogPropsFor(model),
  ...WorkspaceDeleteZoneDialogPropsFor(model),
  ...WorkspaceLibraryDialogPropsFor(model),
  ...WorkspacePlanReviewDialogPropsFor(model),
  ...WorkspaceReleaseDialogPropsFor(model),
  ...WorkspaceSpeciesDialogPropsFor(model),
  ...WorkspaceToolCancelDialogPropsFor(model),
  ...WorkspaceZoneManagerDialogPropsFor(model),
  ...WorkspaceZoneReviewDialogPropsFor(model),
  operationError: model.operationError,
  leaveWorkspace: model.leaveWorkspace,
  project: model.project,
  dismissedOperationError: model.dismissedOperationError,
  reviewOpen: model.reviewOpen,
  deleteSelectionOpen: model.deleteSelectionOpen,
  zonePendingDelete: model.zonePendingDelete,
  setDismissedOperationError: model.setDismissedOperationError,
  reloadAfterConflict: model.reloadAfterConflict,
  navigationBlocker: model.navigationBlocker,
  navigationPending: model.navigationPending,
  pendingScene: model.pendingScene,
  setPendingScene: model.setPendingScene,
});

export const WorkspaceDialogs: FC<WorkspaceDialogsProps> = (model) => {
  const {
    operationError,
    dismissedOperationError,
    reviewOpen,
    deleteSelectionOpen,
    zonePendingDelete,
    setDismissedOperationError,
    reloadAfterConflict,
    navigationBlocker,
    navigationPending,
    pendingScene,
    setPendingScene,
  } = model;
  return (
    <div className="text-sm leading-5 text-neutral-800 [&_[hidden]]:hidden">
      <SceneResourceDiagnostics />

      <WorkspaceAreaDialog {...WorkspaceAreaDialogPropsFor(model)} />
      <WorkspaceZoneReviewDialog
        {...WorkspaceZoneReviewDialogPropsFor(model)}
      />
      <WorkspaceLibraryDialog {...WorkspaceLibraryDialogPropsFor(model)} />
      <WorkspaceZoneManagerDialog
        {...WorkspaceZoneManagerDialogPropsFor(model)}
      />
      <WorkspaceSpeciesDialog {...WorkspaceSpeciesDialogPropsFor(model)} />
      <WorkspacePlanReviewDialog
        {...WorkspacePlanReviewDialogPropsFor(model)}
      />
      <OperationErrorDialog
        open={Boolean(
          operationError &&
          operationError !== dismissedOperationError &&
          !reviewOpen &&
          !deleteSelectionOpen &&
          !zonePendingDelete,
        )}
        error={operationError}
        onClose={() => setDismissedOperationError(operationError)}
        onReload={reloadAfterConflict}
        onPrepare={model.project.source_review
          ? () => model.leaveWorkspace(`/projects/${model.project.id}/setup`)
          : undefined}
      />
      <WorkspaceReleaseDialog {...WorkspaceReleaseDialogPropsFor(model)} />
      <LeaveWorkspaceDialog
        open={navigationBlocker.state === 'blocked'}
        pending={navigationPending}
        onStay={() => navigationBlocker.reset?.()}
        onLeave={() => navigationBlocker.proceed?.()}
      />
      <FinishPlacementDialog
        open={pendingScene}
        onClose={() => setPendingScene(false)}
      />
      <WorkspaceToolCancelDialog
        {...WorkspaceToolCancelDialogPropsFor(model)}
      />
      <WorkspaceDeletePlantingsDialog
        {...WorkspaceDeletePlantingsDialogPropsFor(model)}
      />
      <WorkspaceDeleteZoneDialog
        {...WorkspaceDeleteZoneDialogPropsFor(model)}
      />
    </div>
  );
};
