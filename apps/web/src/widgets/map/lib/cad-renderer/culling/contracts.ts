import type { Sphere } from 'three';

export interface CadAttribute {
  count: number;
  itemSize: number;
  getX: (index: number) => number;
  getY: (index: number) => number;
  getZ?: (index: number) => number;
  meshPerAttribute?: number;
  data?: { meshPerAttribute?: number };
}

export interface CadGeometry {
  isBufferGeometry: true;
  isInstancedBufferGeometry?: boolean;
  attributes: Record<string, unknown>;
  boundingSphere: Sphere | null;
}

export interface CadObject {
  geometry: CadGeometry;
  frustumCulled: boolean;
  matrixWorld: {
    elements: number[];
    getMaxScaleOnAxis: () => number;
  };
  isLine?: boolean;
  isLineSegments?: boolean;
  isMesh?: boolean;
}

export interface Bounds2d {
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
  magnitude: number;
}

export interface CadCullingSummary {
  enabledObjects: number;
  pointObjectsUnculled: number;
  unsafeObjectsUnculled: number;
  boundedGeometries: number;
  positionVerticesRead: number;
  transformsRead: number;
}

export function record(value: unknown): Record<string, unknown> | undefined {
  return value !== null && typeof value === 'object'
    ? (value as Record<string, unknown>)
    : undefined;
}

export function attribute(value: unknown, itemSize: number) {
  const candidate = record(value);
  if (
    !candidate ||
    candidate.itemSize !== itemSize ||
    !Number.isInteger(candidate.count) ||
    Number(candidate.count) <= 0 ||
    typeof candidate.getX !== 'function' ||
    typeof candidate.getY !== 'function' ||
    (itemSize === 3 && typeof candidate.getZ !== 'function')
  )
    return;
  return value as CadAttribute;
}
