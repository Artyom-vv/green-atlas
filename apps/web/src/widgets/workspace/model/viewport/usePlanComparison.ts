import type { PlanComparisonMode } from '@/entities/editor/model/planComparison';
import { planComparisonPresentation } from '@/entities/editor/model/planComparison';
import { useCallback } from 'react';

interface Options {
  mode: PlanComparisonMode;
  setMode: (mode: PlanComparisonMode) => void;
  available: boolean;
  blocked: boolean;
}

/** Only the view changes; entering or leaving never applies or discards a draft. */
export function usePlanComparison({
  mode,
  setMode,
  available,
  blocked,
}: Options) {
  const currentMode = available ? mode : 'editing';
  const change = useCallback(
    (next: PlanComparisonMode) => {
      if (next === 'editing' || (available && !blocked)) setMode(next);
    },
    [available, blocked, setMode],
  );
  return {
    mode: currentMode,
    ...planComparisonPresentation(currentMode),
    canEnter: available && !blocked,
    change,
  };
}
