import type {
  LocationBounds,
  LocationFeatureCollection,
  LocationGeometry,
  LocationPoint,
} from '@green/api-client';

const EARTH_RADIUS = 6378137;
export const projectMapPoint = ([
  longitude,
  latitude,
]: LocationPoint): LocationPoint => [
  (EARTH_RADIUS * longitude * Math.PI) / 180,
  EARTH_RADIUS *
    Math.log(
      Math.tan(
        Math.PI / 4 + (Math.max(-85, Math.min(85, latitude)) * Math.PI) / 360,
      ),
    ),
];
export const unprojectMapPoint = ([x, y]: LocationPoint): LocationPoint => [
  ((x / EARTH_RADIUS) * 180) / Math.PI,
  ((2 * Math.atan(Math.exp(y / EARTH_RADIUS)) - Math.PI / 2) * 180) / Math.PI,
];

export function geometryLines(
  geometry: LocationGeometry | null | undefined,
): LocationPoint[][] {
  if (!geometry) return [];
  if (geometry.type === 'GeometryCollection')
    return (geometry.geometries ?? []).flatMap(geometryLines);
  const lines: LocationPoint[][] = [];
  const visit = (value: unknown) => {
    if (!Array.isArray(value) || value.length === 0) return;
    if (Array.isArray(value[0]) && typeof value[0][0] === 'number') {
      const line = value
        .filter(
          (point): point is LocationPoint =>
            Array.isArray(point) &&
            point.length >= 2 &&
            Number.isFinite(point[0]) &&
            Number.isFinite(point[1]),
        )
        .map((point): LocationPoint => [point[0], point[1]]);
      if (line.length) lines.push(line);
    } else value.forEach(visit);
  };
  visit(geometry.coordinates);
  return lines;
}
export function collectionLines(collection?: LocationFeatureCollection | null) {
  return (
    collection?.features.flatMap((feature) =>
      geometryLines(feature.geometry),
    ) ?? []
  );
}
export function boundsOf(lines: LocationPoint[][]): LocationBounds | null {
  let minX = Infinity,
    minY = Infinity,
    maxX = -Infinity,
    maxY = -Infinity;
  for (const line of lines)
    for (const [x, y] of line) {
      minX = Math.min(minX, x);
      minY = Math.min(minY, y);
      maxX = Math.max(maxX, x);
      maxY = Math.max(maxY, y);
    }
  return Number.isFinite(minX) ? [minX, minY, maxX, maxY] : null;
}
export function sectionAround(
  [longitude, latitude]: LocationPoint,
  radiusM = 350,
): LocationBounds {
  const latDelta = radiusM / 111320;
  const lonDelta =
    latDelta / Math.max(0.1, Math.cos((latitude * Math.PI) / 180));
  return [
    longitude - lonDelta,
    latitude - latDelta,
    longitude + lonDelta,
    latitude + latDelta,
  ];
}
