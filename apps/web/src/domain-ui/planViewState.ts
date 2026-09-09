export type PlanViewState = {
  center: [number, number];
  resolution: number;
  rotation: number;
  viewport: [number, number];
};

export const PLAN_VIEW_STATE_ATTRIBUTES = {
  centerX: 'data-plan-view-center-x',
  centerY: 'data-plan-view-center-y',
  resolution: 'data-plan-view-resolution',
  rotation: 'data-plan-view-rotation',
  viewportWidth: 'data-plan-view-viewport-width',
  viewportHeight: 'data-plan-view-viewport-height',
} as const;

/** Publishes the real camera bridge state for diagnostics and browser audits. */
export function writePlanViewStateAttributes(element: HTMLElement, state: PlanViewState) {
  if (!validPlanViewState(state)) return;
  element.setAttribute(PLAN_VIEW_STATE_ATTRIBUTES.centerX, String(Number(state.center[0].toFixed(6))));
  element.setAttribute(PLAN_VIEW_STATE_ATTRIBUTES.centerY, String(Number(state.center[1].toFixed(6))));
  element.setAttribute(PLAN_VIEW_STATE_ATTRIBUTES.resolution, String(Number(state.resolution.toFixed(9))));
  element.setAttribute(PLAN_VIEW_STATE_ATTRIBUTES.rotation, String(Number(state.rotation.toFixed(9))));
  element.setAttribute(PLAN_VIEW_STATE_ATTRIBUTES.viewportWidth, String(Number(state.viewport[0].toFixed(3))));
  element.setAttribute(PLAN_VIEW_STATE_ATTRIBUTES.viewportHeight, String(Number(state.viewport[1].toFixed(3))));
}

export function validPlanViewState(state: PlanViewState | undefined): state is PlanViewState {
  return Boolean(state
    && state.center.every(Number.isFinite)
    && Number.isFinite(state.resolution) && state.resolution > 0
    && Number.isFinite(state.rotation)
    && state.viewport.every((value) => Number.isFinite(value) && value > 0));
}

export function sourceCenterToWorld(center: [number, number], origin: readonly number[]): [number, number] {
  return [center[0] - (origin[0] ?? 0), -(center[1] - (origin[1] ?? 0))];
}

export function worldCenterToSource(worldX: number, worldZ: number, origin: readonly number[]): [number, number] {
  return [worldX + (origin[0] ?? 0), -worldZ + (origin[1] ?? 0)];
}
