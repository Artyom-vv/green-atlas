import type { WorkspaceReadyModel } from '../model/useWorkspaceModel';
import type { BrushToolPanelProps } from '@/features/placement/ui/BrushToolPanel';

export interface BrushBindingOptions extends Pick<
  WorkspaceReadyModel,
  | 'selectPatternZones'
  | 'previewBrush'
  | 'previewChanges'
  | 'project'
  | 'setReviewOpen'
  | 'setBrushStrokes'
  | 'editor'
> {}
export const brushBindingPropsFor = (
  model: BrushBindingOptions,
): BrushBindingOptions => ({
  selectPatternZones: model.selectPatternZones,
  previewBrush: model.previewBrush,
  previewChanges: model.previewChanges,
  project: model.project,
  setReviewOpen: model.setReviewOpen,
  setBrushStrokes: model.setBrushStrokes,
  editor: model.editor,
});

type BrushBindings = Pick<
  BrushToolPanelProps,
  'onZoneIdsChange' | 'onPreview' | 'onApply' | 'onClear' | 'onCancel'
>;

export function brushToolBindings(options: BrushBindingOptions): BrushBindings {
  const resetPreview = () => {
    options.previewBrush.reset();
    options.previewChanges.reset();
  };
  const clear = () => {
    resetPreview();
    options.setBrushStrokes([]);
  };
  return {
    onZoneIdsChange: (ids) => {
      options.selectPatternZones(ids);
      resetPreview();
    },
    onPreview: (draft) =>
      options.previewBrush.mutate({
        ...draft,
        base_plan_version: options.project.plan!.version,
      }),
    onApply: () => options.setReviewOpen(true),
    onClear: clear,
    onCancel: () => {
      clear();
      options.editor.setTool('select');
    },
  };
}
