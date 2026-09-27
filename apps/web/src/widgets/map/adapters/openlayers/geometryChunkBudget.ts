import { GEOMETRY_CHUNK_SIZE } from './openLayersConfig';

/** Initial responsiveness budgets; one indivisible feature/index flush may exceed them. */
export const GEOMETRY_CHUNK_POLICY = {
  maxParsedFeaturesPerTask: GEOMETRY_CHUNK_SIZE,
  maxVerticesPerBatch: 12_000,
  maxTaskWorkMs: 6,
} as const;

function coordinateCount(value: unknown): number {
  if (!Array.isArray(value) || value.length === 0) return 0;
  if (typeof value[0] === 'number') return 1;
  // GeoJSON sequences contain homogeneous positions: count their length without
  // traversing every coordinate again merely to decide when to yield.
  if (Array.isArray(value[0]) && typeof value[0][0] === 'number')
    return value.length;
  let total = 0;
  for (const child of value) total += coordinateCount(child);
  return total;
}

export function geometryVertexCount(value: unknown): number {
  if (!value || typeof value !== 'object') return 0;
  const geometry = value as Record<string, unknown>;
  if (Array.isArray(geometry.geometries)) {
    return geometry.geometries.reduce<number>(
      (total, child) => total + geometryVertexCount(child),
      0,
    );
  }
  return coordinateCount(geometry.coordinates);
}

export function geometryTaskComplete(
  features: number,
  startedAt: number,
): boolean {
  return (
    features >= GEOMETRY_CHUNK_POLICY.maxParsedFeaturesPerTask ||
    performance.now() - startedAt >= GEOMETRY_CHUNK_POLICY.maxTaskWorkMs
  );
}
