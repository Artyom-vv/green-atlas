import type { StoreApi } from 'zustand/vanilla';
import type { EditorSession } from './editorStore';
import type { EditorViewState, StateUpdate } from './editorTypes';

export interface EditorViewActions {
  setGrowthHorizon: (horizon: number) => void;
  setPlanComparison: (mode: EditorViewState['planComparison']) => void;
  showScene: (viewState?: EditorViewState['sceneInitialViewState']) => void;
  hideScene: (viewState?: EditorViewState['sceneInitialViewState']) => void;
  setResourcesTab: (tab: EditorViewState['resourcesTab']) => void;
  openSourceLayers: () => void;
  setPanel: (update: StateUpdate<EditorViewState['panel']>) => void;
  setIdeRightTab: (update: StateUpdate<EditorViewState['ideRightTab']>) => void;
  setLeftOpen: (update: StateUpdate<boolean>) => void;
  setRightOpen: (update: StateUpdate<boolean>) => void;
  setResultsOpen: (update: StateUpdate<boolean>) => void;
  setResultsTab: (update: StateUpdate<EditorViewState['resultsTab']>) => void;
  setActiveLayerId: (
    update: StateUpdate<EditorViewState['activeLayerId']>,
  ) => void;
  setVisibility: (update: StateUpdate<EditorViewState['visibility']>) => void;
}

function resolveUpdate<Value>(
  update: StateUpdate<Value>,
  current: Value,
): Value {
  return typeof update === 'function'
    ? (update as (current: Value) => Value)(current)
    : update;
}

/** Updaters read the current store snapshot, never a captured React render. */
export function createEditorViewActions(
  set: StoreApi<EditorSession>['setState'],
): EditorViewActions {
  return {
    setGrowthHorizon: (growthHorizon) => set({ growthHorizon }),
    setPlanComparison: (planComparison) => set({ planComparison }),
    showScene: (viewState) =>
      set((state) => ({
        scenePhase: 'visible',
        sceneInitialViewState: viewState ?? state.sceneInitialViewState,
      })),
    hideScene: (viewState) =>
      set((state) => ({
        scenePhase: state.scenePhase === 'unmounted' ? 'unmounted' : 'hidden',
        sceneInitialViewState: viewState ?? state.sceneInitialViewState,
      })),
    setResourcesTab: (resourcesTab) => set({ resourcesTab }),
    openSourceLayers: () => set({ resourcesTab: 'layers', leftOpen: true }),
    setPanel: (update) =>
      set((state) => ({ panel: resolveUpdate(update, state.panel) })),
    setIdeRightTab: (update) =>
      set((state) => ({
        ideRightTab: resolveUpdate(update, state.ideRightTab),
      })),
    setLeftOpen: (update) =>
      set((state) => ({ leftOpen: resolveUpdate(update, state.leftOpen) })),
    setRightOpen: (update) =>
      set((state) => ({ rightOpen: resolveUpdate(update, state.rightOpen) })),
    setResultsOpen: (update) =>
      set((state) => ({
        resultsOpen: resolveUpdate(update, state.resultsOpen),
      })),
    setResultsTab: (update) =>
      set((state) => ({ resultsTab: resolveUpdate(update, state.resultsTab) })),
    setActiveLayerId: (update) =>
      set((state) => ({
        activeLayerId: resolveUpdate(update, state.activeLayerId),
      })),
    setVisibility: (update) =>
      set((state) => ({ visibility: resolveUpdate(update, state.visibility) })),
  };
}
