import type { RecommendationPanelProps } from '@/features/recommendation/ui/RecommendationPanel';
import type { RecommendationDraft } from '@/features/recommendation/model/recommendationForm';
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
  'onCancelCalculation' | 'onPreview' | 'onCancel'
>;

export function recommendationToolBindings(
  options: RecommendationBindingOptions,
): RecommendationBindings {
  const preview = (draft: RecommendationDraft) => {
    if (options.sceneOpen) options.changeMapMode('2d');
    options.previewRecommendation.mutate({
      ...draft,
      base_plan_version: options.project.plan!.version,
    });
  };
  return {
    onCancelCalculation: () => options.previewRecommendation.reset(),
    onPreview: preview,
    onCancel: () => {
      options.previewRecommendation.reset();
      options.setRecommendationOpen(false);
      options.editor.setTool('select');
    },
  };
}
