import { Button } from '@green/ui';
import type { FC } from 'react';
import type { WorkspaceReadyModel } from '../../model/useWorkspaceModel';

export interface CanvasPreviewActionsProps extends Pick<
  WorkspaceReadyModel,
  | 'brushPreview'
  | 'changePreview'
  | 'patternPreview'
  | 'recommendationPreview'
  | 'reviewOpen'
  | 'setReviewOpen'
> {}
export const CanvasPreviewActionsPropsFor = (
  props: CanvasPreviewActionsProps,
) => ({
  brushPreview: props.brushPreview,
  changePreview: props.changePreview,
  patternPreview: props.patternPreview,
  recommendationPreview: props.recommendationPreview,
  reviewOpen: props.reviewOpen,
  setReviewOpen: props.setReviewOpen,
});
export const CanvasPreviewActions: FC<CanvasPreviewActionsProps> = ({
  brushPreview,
  changePreview,
  patternPreview,
  recommendationPreview,
  reviewOpen,
  setReviewOpen,
}) => (
  <>
    {changePreview &&
      !patternPreview &&
      !brushPreview &&
      !recommendationPreview &&
      !reviewOpen && (
        <div
          className="rounded-card absolute bottom-4 left-1/2 z-40 flex max-w-[calc(100%-2rem)] -translate-x-1/2 flex-wrap items-center gap-3 border border-neutral-300 bg-white p-3 text-xs shadow-sm"
          role="status"
        >
          <span>
            {changePreview.can_apply
              ? 'Изменения готовы'
              : 'Изменение не прошло проверку'}
          </span>
          <Button variant="secondary" onClick={() => setReviewOpen(true)}>
            {changePreview.can_apply
              ? 'Проверить и применить'
              : 'Посмотреть причину'}
          </Button>
        </div>
      )}
  </>
);
