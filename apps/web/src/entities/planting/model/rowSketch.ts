import type { RowPatternRequest } from '@green/api-client';

export type RowSketchSettings = {
  placementMode: RowPatternRequest['placement_mode'];
  count: number;
  spacing: number;
  side: RowPatternRequest['side'];
  lateralOffset: number;
  startOffset: number;
  endOffset: number;
  kind: RowPatternRequest['plant_kind'];
};
export type RowAxis = { type: 'LineString'; coordinates: number[][] };

export function rowSketchFrame(axis: RowAxis, settings: RowSketchSettings) {
  const sites = rowSketch(axis, settings).sites;
  const points = [
    ...axis.coordinates,
    ...sites.map((site) => [site.x, site.y]),
  ];
  const xs = points.map((p) => p[0]),
    ys = points.map((p) => p[1]);
  const pad = Math.max(12, settings.lateralOffset * 2);
  const left = Math.min(...xs) - pad,
    right = Math.max(...xs) + pad,
    bottom = Math.min(...ys) - pad,
    top = Math.max(...ys) + pad;
  return {
    type: 'Polygon',
    coordinates: [
      [
        [left, bottom],
        [right, bottom],
        [right, top],
        [left, top],
        [left, bottom],
      ],
    ],
  };
}

/** Geometric sketch only. These points never have IDs or an apply payload.
 * Restrictions and any species-driven spacing adjustment belong to the API. */
export function rowSketch(
  axis: RowAxis | undefined,
  settings: RowSketchSettings,
  limit = 5000,
) {
  const points = (axis?.coordinates ?? []).filter(
    (p) => p.length >= 2 && p.every(Number.isFinite),
  );
  const lengths = points
    .slice(1)
    .map((p, i) => Math.hypot(p[0] - points[i][0], p[1] - points[i][1]));
  const length = lengths.reduce((a, b) => a + b, 0);
  const usable = length - settings.startOffset - settings.endOffset;
  const sides =
    settings.side === 'both'
      ? [1, -1]
      : [settings.side === 'left' ? 1 : settings.side === 'right' ? -1 : 0];
  const total =
    settings.placementMode === 'count'
      ? Math.max(2, Math.floor(settings.count))
      : Infinity;
  const stations =
    settings.placementMode === 'count'
      ? Math.ceil(total / sides.length)
      : Math.floor(usable / Math.max(0.1, settings.spacing)) + 1;
  const step =
    settings.placementMode === 'count'
      ? stations > 1
        ? usable / (stations - 1)
        : 0
      : settings.spacing;
  const at = (distance: number) => {
    let remaining = Math.max(0, Math.min(length, distance));
    for (let i = 0; i < lengths.length; i++) {
      if (!lengths[i]) continue;
      if (remaining <= lengths[i] || i === lengths.length - 1) {
        const t = remaining / lengths[i],
          a = points[i],
          b = points[i + 1];
        return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
      }
      remaining -= lengths[i];
    }
    return points.at(-1) ?? [0, 0];
  };
  const sites: { x: number; y: number; kind: RowSketchSettings['kind'] }[] = [];
  if (!length || usable < 0 || !Number.isFinite(step) || step < 0)
    return { length, step, sites, invalidOffsets: usable < 0, total: 0 };
  for (let i = 0; i < stations && sites.length < Math.min(limit, total); i++) {
    const distance =
      settings.startOffset + (stations === 1 ? usable / 2 : i * step);
    const center = at(distance),
      epsilon = Math.min(0.05, length / 100);
    const a = at(distance - epsilon),
      b = at(distance + epsilon),
      dx = b[0] - a[0],
      dy = b[1] - a[1],
      segment = Math.hypot(dx, dy);
    if (!segment) continue;
    for (const side of sides) {
      if (sites.length >= Math.min(limit, total)) break;
      sites.push({
        x: center[0] - (dy / segment) * settings.lateralOffset * side,
        y: center[1] + (dx / segment) * settings.lateralOffset * side,
        kind: settings.kind,
      });
    }
  }
  return {
    length,
    step,
    sites,
    invalidOffsets: false,
    total: Math.min(total, stations * sides.length),
  };
}
