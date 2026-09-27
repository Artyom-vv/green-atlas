import { Sphere, Vector3 } from 'three';
import type { Bounds2d, CadAttribute, CadCullingSummary } from './contracts';

// Float32 attributes/shader affine multiply-adds need a conservative rounding
// margin, including large cancelling products, not just the final coordinate.
const FLOAT32_ROUNDING_MARGIN = 8 * 2 ** -23;
const MAX_FLOAT32 = 3.4028234663852886e38;

export function readBounds(
  position: CadAttribute,
  summary: CadCullingSummary,
): Bounds2d | undefined {
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (let index = 0; index < position.count; index += 1) {
    const x = position.getX(index);
    const y = position.getY(index);
    summary.positionVerticesRead += 1;
    if (!Number.isFinite(x) || !Number.isFinite(y)) return;
    minX = Math.min(minX, x);
    minY = Math.min(minY, y);
    maxX = Math.max(maxX, x);
    maxY = Math.max(maxY, y);
  }
  return {
    minX,
    minY,
    maxX,
    maxY,
    magnitude: Math.max(
      Math.abs(minX),
      Math.abs(minY),
      Math.abs(maxX),
      Math.abs(maxY),
    ),
  };
}

export function boundsSphere(bounds: Bounds2d): Sphere | undefined {
  const { minX, minY, maxX, maxY, magnitude } = bounds;
  if (
    ![minX, minY, maxX, maxY, magnitude].every(Number.isFinite) ||
    magnitude > MAX_FLOAT32
  )
    return;
  const padding = Math.max(1, magnitude) * FLOAT32_ROUNDING_MARGIN;
  return new Sphere(
    new Vector3((minX + maxX) / 2, (minY + maxY) / 2, 0),
    Math.hypot((maxX - minX) / 2, (maxY - minY) / 2) + Math.SQRT2 * padding,
  );
}
