import type { RecommendationPanelProps } from '@/features/recommendation/ui/RecommendationPanel';
import type {
  BuildingScreenDraft,
  RecommendationDraft,
} from '@/features/recommendation/model/recommendationForm';
import type { WorkspaceRecommendationToolProps } from './WorkspaceRecommendationTool.props';

interface RecommendationBindingOptions extends Pick<
  WorkspaceRecommendationToolProps,
  | 'sceneOpen'
  | 'changeMapMode'
  | 'previewRecommendation'
  | 'project'
  | 'setRecommendationOpen'
  | 'editor'
> {}

type RecommendationBindings = Pick<
  RecommendationPanelProps,
  | 'onChooseComposition'
  | 'onCancelCalculation'
  | 'onScreenPreview'
  | 'onPreview'
  | 'onCancel'
>;

export function recommendationToolBindings(
  options: RecommendationBindingOptions,
): RecommendationBindings {
  const preview = (draft: RecommendationDraft | BuildingScreenDraft) => {
    if (options.sceneOpen) options.changeMapMode('2d');
    options.previewRecommendation.mutate({
      ...draft,
      base_plan_version: options.project.plan!.version,
    });
  };
  return {
    onChooseComposition: () => options.setRecommendationOpen(false),
    onCancelCalculation: () => options.previewRecommendation.reset(),
    onScreenPreview: preview,
    onPreview: preview,
    onCancel: () => {
      options.previewRecommendation.reset();
      options.setRecommendationOpen(false);
      options.editor.setTool('select');
    },
  };
}
