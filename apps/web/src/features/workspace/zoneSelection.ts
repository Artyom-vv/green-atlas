import type { PlantingZoneAssignment } from '@green/api-client';

/** Selection is a work scope, never an incidental side effect of camera focus. */
export function placementZoneSelection(selected: string[], available: string[]) {
  const valid = [...new Set(selected)].filter(id => available.includes(id));
  return valid.length ? valid : available.length === 1 ? available : [];
}

export function zoneCollection(zones: PlantingZoneAssignment[]) {
  return { type: 'GeometryCollection', geometries: zones.map(zone => zone.geometry) };
}

export function zoneExtent(zones: PlantingZoneAssignment[]): [number, number, number, number] | undefined {
  const points: number[][] = [];
  const visit = (value: unknown) => {
    if (!Array.isArray(value)) return;
    if (typeof value[0] === 'number' && typeof value[1] === 'number') {
      if (Number.isFinite(value[0]) && Number.isFinite(value[1])) points.push(value as number[]);
    } else value.forEach(visit);
  };
  zones.forEach(zone => visit(zone.geometry.coordinates));
  if (!points.length) return undefined;
  return points.reduce<[number, number, number, number]>((bounds, [x, y]) => [Math.min(bounds[0], x), Math.min(bounds[1], y), Math.max(bounds[2], x), Math.max(bounds[3], y)], [Infinity, Infinity, -Infinity, -Infinity]);
}
