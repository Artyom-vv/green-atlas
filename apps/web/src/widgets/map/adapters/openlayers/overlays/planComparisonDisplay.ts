import type { PlanComparisonMode } from '@/entities/editor/model/planComparison';
import { planComparisonPresentation } from '@/entities/editor/model/planComparison';
import type BaseLayer from 'ol/layer/Base';

interface Layers {
  savedPlan: BaseLayer;
  editingOverlays: readonly BaseLayer[];
}

/** Hide layers without removing their features, changing the source or moving the camera. */
export function createPlanComparisonDisplay({
  savedPlan,
  editingOverlays,
}: Layers) {
  let restore: Array<{ layer: BaseLayer; visible: boolean }> | undefined;
  return {
    apply(mode: PlanComparisonMode) {
      const presentation = planComparisonPresentation(mode);
      if (!presentation.active) {
        for (const entry of restore ?? [])
          entry.layer.setVisible(entry.visible);
        restore = undefined;
        return;
      }
      restore ??= [savedPlan, ...editingOverlays].map((layer) => ({
        layer,
        visible: layer.getVisible(),
      }));
      savedPlan.setVisible(presentation.savedPlantingsVisible);
      for (const layer of editingOverlays) layer.setVisible(false);
    },
  };
}
