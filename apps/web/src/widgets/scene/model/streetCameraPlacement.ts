export type StreetCameraPoint = [number, number];

export interface StreetCameraPlacementState {
  step: 'position' | 'direction' | 'ready';
  position?: StreetCameraPoint;
  target?: StreetCameraPoint;
  snapped: boolean;
}

/** A direction aid in the actual source axes. This never invents a road centre. */
export function streetDirectionTarget(
  position: StreetCameraPoint,
  target: StreetCameraPoint,
  snap: boolean,
): StreetCameraPoint | undefined {
  const dx = target[0] - position[0];
  const dy = target[1] - position[1];
  const distance = Math.hypot(dx, dy);
  if (!Number.isFinite(distance) || distance < 0.5) return undefined;
  if (!snap) return [...target];
  const step = Math.PI / 4;
  const angle = Math.round(Math.atan2(dy, dx) / step) * step;
  return [
    position[0] + Math.cos(angle) * distance,
    position[1] + Math.sin(angle) * distance,
  ];
}
