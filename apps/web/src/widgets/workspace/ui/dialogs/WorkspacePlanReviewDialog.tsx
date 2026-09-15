import { ChangeSetReviewPanel } from '@/features/plan-changes/ui/ChangeSetReviewPanel';
import { errorMessage as message } from '@/shared/errors/errorMessage';
import type { FC } from 'react';
import type { WorkspaceReadyModel } from '../../model/useWorkspaceModel';

export interface WorkspacePlanReviewDialogProps extends Pick<
  WorkspaceReadyModel,
  | 'changePreview'
  | 'reviewOpen'
  | 'applyChanges'
  | 'patternPreview'
  | 'recommendationPreview'
  | 'setReviewOpen'
  | 'previewChanges'
  | 'previewPattern'
  | 'previewBrush'
  | 'setBrushStrokes'
  | 'previewRecommendation'
> {}

export const WorkspacePlanReviewDialogPropsFor = (
  model: WorkspacePlanReviewDialogProps,
): WorkspacePlanReviewDialogProps => ({
  changePreview: model.changePreview,
  reviewOpen: model.reviewOpen,
  applyChanges: model.applyChanges,
  patternPreview: model.patternPreview,
  recommendationPreview: model.recommendationPreview,
  setReviewOpen: model.setReviewOpen,
  previewChanges: model.previewChanges,
  previewPattern: model.previewPattern,
  previewBrush: model.previewBrush,
  setBrushStrokes: model.setBrushStrokes,
  previewRecommendation: model.previewRecommendation,
});

export const WorkspacePlanReviewDialog: FC<WorkspacePlanReviewDialogProps> = ({
  changePreview,
  reviewOpen,
  applyChanges,
  patternPreview,
  recommendationPreview,
  setReviewOpen,
  previewChanges,
  previewPattern,
  previewBrush,
  setBrushStrokes,
  previewRecommendation,
}) => {
  if (!changePreview) return null;
  return (
    <ChangeSetReviewPanel
      open={reviewOpen}
      preview={changePreview}
      applying={applyChanges.isPending}
      error={applyChanges.error ? message(applyChanges.error) : undefined}
      unverifiedData={
        patternPreview?.unverified_data ??
        recommendationPreview?.data_gaps ??
        []
      }
      onInspect={() => {
        if (!applyChanges.isPending) setReviewOpen(false);
      }}
      onCancel={() => {
        if (applyChanges.isPending) return;
        setReviewOpen(false);
        previewChanges.reset();
        previewPattern.reset();
        previewBrush.reset();
        setBrushStrokes([]);
        previewRecommendation.reset();
        applyChanges.reset();
      }}
      onApply={() => applyChanges.mutate(changePreview)}
    />
  );
};
