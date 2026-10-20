export type PlanComparisonMode = 'editing' | 'before' | 'after';

/** Comparison changes presentation only. Saved plan data and drafts keep their owners. */
export function planComparisonPresentation(mode: PlanComparisonMode) {
  return {
    active: mode !== 'editing',
    savedPlantingsVisible: mode !== 'before',
    editingOverlaysVisible: mode === 'editing',
  };
}
