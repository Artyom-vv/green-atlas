import type { PlanObject, PlantingZoneAssignment } from '@green/api-client';

/** Coordinates are project metres. A single point still needs street context. */
export function placementPreviewFrame(objects: Pick<PlanObject, 'x' | 'y'>[]): PlantingZoneAssignment['geometry'] | undefined {
  const points = objects.filter(item => Number.isFinite(item.x) && Number.isFinite(item.y));
  if (!points.length) return undefined;
  const xs = points.map(item => item.x), ys = points.map(item => item.y);
  const left = Math.min(...xs) - 40, right = Math.max(...xs) + 40;
  const bottom = Math.min(...ys) - 40, top = Math.max(...ys) + 40;
  return { type: 'Polygon', coordinates: [[[left, bottom], [right, bottom], [right, top], [left, top], [left, bottom]]] };
}
