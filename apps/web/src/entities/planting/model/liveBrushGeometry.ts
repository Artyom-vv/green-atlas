import type {
  BrushPreviewRequest,
  BrushStroke,
  PlantingZoneAssignment,
  PlanObjectCreate,
} from '@green/api-client';

export interface LiveBrushSettings {
  composition: BrushPreviewRequest['composition'];
  density: BrushPreviewRequest['density'];
  treeShare: number;
  spacing: number;
  treeSpeciesId?: string;
  shrubSpeciesId?: string;
}
export const DEFAULT_LIVE_BRUSH_SETTINGS: LiveBrushSettings = {
  composition: 'trees',
  density: 'balanced',
  treeShare: 0.7,
  spacing: 6,
};
export type LiveBrushSite = {
  x: number;
  y: number;
  kind: PlanObjectCreate['kind'];
};
type Point = [number, number];

function insideRing(point: Point, ring: number[][]): boolean {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const a = ring[i],
      b = ring[j];
    if (
      a[1] > point[1] !== b[1] > point[1] &&
      point[0] < ((b[0] - a[0]) * (point[1] - a[1])) / (b[1] - a[1]) + a[0]
    )
      inside = !inside;
  }
  return inside;
}
function inZone(point: Point, zone: PlantingZoneAssignment): boolean {
  const geometry = zone.geometry as {
    type: string;
    coordinates: number[][][] | number[][][][];
  };
  const polygons =
    geometry.type === 'MultiPolygon'
      ? (geometry.coordinates as number[][][][])
      : geometry.type === 'Polygon'
        ? [geometry.coordinates as number[][][]]
        : [];
  return polygons.some(
    (polygon) =>
      polygon.length &&
      insideRing(point, polygon[0]) &&
      !polygon.slice(1).some((ring) => insideRing(point, ring)),
  );
}
function strokeDistance(point: Point, stroke: BrushStroke): number {
  const points = stroke.geometry.coordinates as number[][];
  let best = Infinity;
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1],
      b = points[i],
      dx = b[0] - a[0],
      dy = b[1] - a[1],
      length = dx * dx + dy * dy;
    const t = length
      ? Math.max(
          0,
          Math.min(
            1,
            ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / length,
          ),
        )
      : 0;
    best = Math.min(
      best,
      Math.hypot(point[0] - a[0] - t * dx, point[1] - a[1] - t * dy),
    );
  }
  return best;
}
function noise(x: number, y: number, salt: number): number {
  let n = Math.imul(x, 374761393) ^ Math.imul(y, 668265263) ^ salt;
  n = Math.imul(n ^ (n >>> 13), 1274126177);
  return ((n ^ (n >>> 16)) >>> 0) / 4294967296;
}

/** Visual-only draft. It deliberately has no object IDs, validity or apply payload.
 * The server's accepted preview replaces it; normative checks are never duplicated here.
 * World-anchored samples do not shuffle on every pointer event or repeated segment.
 */
export function liveBrushSites(
  strokes: readonly BrushStroke[],
  zones: readonly PlantingZoneAssignment[],
  width: number,
  settings: LiveBrushSettings,
  maxSites = 500,
): LiveBrushSite[] {
  if (!zones.length || width <= 0 || !Number.isFinite(width)) return [];
  const add = strokes.filter((s) => s.mode === 'add'),
    subtract = strokes.filter((s) => s.mode === 'subtract');
  const points = add
    .flatMap((s) => s.geometry.coordinates as number[][])
    .filter((p) => p.length >= 2 && p.every(Number.isFinite));
  if (!points.length) return [];
  const radius = width / 2,
    step = Math.max(
      1,
      settings.spacing /
        Math.sqrt({ sparse: 0.42, balanced: 0.7, dense: 1 }[settings.density]),
    );
  const xs = points.map((p) => p[0]),
    ys = points.map((p) => p[1]);
  const minX = Math.floor((Math.min(...xs) - radius) / step),
    maxX = Math.ceil((Math.max(...xs) + radius) / step);
  const minY = Math.floor((Math.min(...ys) - radius) / step),
    maxY = Math.ceil((Math.max(...ys) + radius) / step);
  const result: LiveBrushSite[] = [];
  let attempts = 0;
  for (let y = minY; y <= maxY; y++)
    for (let x = minX; x <= maxX; x++) {
      if (++attempts > 12000 || result.length >= maxSites) return result;
      const point: Point = [
        (x + 0.5 + (noise(x, y, 47) - 0.5) * 0.4) * step,
        (y + 0.5 + (noise(x, y, 91) - 0.5) * 0.4) * step,
      ];
      if (
        !zones.some((zone) => inZone(point, zone)) ||
        !add.some((s) => strokeDistance(point, s) <= radius) ||
        subtract.some((s) => strokeDistance(point, s) <= radius)
      )
        continue;
      result.push({
        x: point[0],
        y: point[1],
        kind:
          settings.composition === 'shrubs' ||
          (settings.composition === 'mixed' &&
            noise(x, y, 13) >= settings.treeShare)
            ? 'shrub'
            : 'tree',
      });
    }
  return result;
}
