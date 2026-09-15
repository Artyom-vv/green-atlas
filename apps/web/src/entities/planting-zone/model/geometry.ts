export function polygonCoordinates(
  geometry: Record<string, unknown>,
): number[][][][] {
  if (!Array.isArray(geometry.coordinates)) return [];
  if (geometry.type === 'Polygon')
    return [geometry.coordinates as number[][][]];
  return geometry.type === 'MultiPolygon'
    ? (geometry.coordinates as number[][][][])
    : [];
}

export function zoneGeometryArea(geometry: Record<string, unknown>): number {
  const ringArea = (ring: number[][]) =>
    Math.abs(
      ring.reduce((sum, point, index) => {
        const next = ring[(index + 1) % ring.length];
        return sum + point[0] * next[1] - next[0] * point[1];
      }, 0) / 2,
    );
  return polygonCoordinates(geometry).reduce(
    (sum, polygon) =>
      sum +
      (polygon[0] ? ringArea(polygon[0]) : 0) -
      polygon.slice(1).reduce((holes, ring) => holes + ringArea(ring), 0),
    0,
  );
}

export function zoneGeometryPath(geometry: Record<string, unknown>): string {
  return polygonCoordinates(geometry)
    .map((polygon) =>
      polygon
        .map((ring) =>
          ring.length
            ? `M${ring.map((point) => `${point[0]},${-point[1]}`).join('L')}Z`
            : '',
        )
        .join(''),
    )
    .join('');
}

export function zoneGeometryViewBox(
  geometries: Record<string, unknown>[],
): string {
  const points = geometries
    .flatMap((geometry) => polygonCoordinates(geometry).flat(2))
    .filter(
      (point) => point.length >= 2 && point.slice(0, 2).every(Number.isFinite),
    );
  if (!points.length) return '0 0 1 1';
  const xs = points.map((point) => point[0]),
    ys = points.map((point) => point[1]);
  const x = Math.min(...xs),
    y = Math.min(...ys);
  const width = Math.max(...xs) - x || 1,
    height = Math.max(...ys) - y || 1;
  const pad = Math.max(width, height) * 0.08;
  return `${x - pad} ${-y - height - pad} ${width + pad * 2} ${height + pad * 2}`;
}
