import type {
  Bounds2d,
  CadAttribute,
  CadCullingSummary,
  CadGeometry,
} from './contracts';
import { attribute } from './contracts';

const divisor = (value: CadAttribute) =>
  value.meshPerAttribute ?? value.data?.meshPerAttribute ?? 1;

/** Match the SDK shader's two affine rows, or its point translation. */
export function instanceBounds(
  base: Bounds2d,
  geometry: CadGeometry,
  summary: CadCullingSummary,
): Bounds2d | undefined {
  const attrs = geometry.attributes;
  const point = attribute(attrs.instanceTransform, 2);
  const row0 = attribute(attrs.instanceTransform0, 3);
  const row1 = attribute(attrs.instanceTransform1, 3);
  const hasPoint = attrs.instanceTransform !== undefined;
  const hasRows =
    attrs.instanceTransform0 !== undefined ||
    attrs.instanceTransform1 !== undefined;
  if (!geometry.isInstancedBufferGeometry)
    return hasPoint || hasRows ? undefined : base;
  if (
    hasPoint === hasRows ||
    (hasPoint && !point) ||
    (hasRows &&
      (!row0 ||
        !row1 ||
        row0.count !== row1.count ||
        divisor(row0) !== divisor(row1)))
  )
    return;
  const count = point?.count ?? row0!.count;
  const result: Bounds2d = {
    minX: Infinity,
    minY: Infinity,
    maxX: -Infinity,
    maxY: -Infinity,
    magnitude: base.magnitude,
  };
  const absX = Math.max(Math.abs(base.minX), Math.abs(base.maxX));
  const absY = Math.max(Math.abs(base.minY), Math.abs(base.maxY));
  // All available transforms conservatively cover finite draw ranges and the
  // SDK's default instanceCount=Infinity; never truncate to an inferred count.
  for (let index = 0; index < count; index += 1) {
    const a = point ? 1 : row0!.getX(index);
    const b = point ? 0 : row0!.getY(index);
    const tx = point ? point.getX(index) : row0!.getZ!(index);
    const c = point ? 0 : row1!.getX(index);
    const d = point ? 1 : row1!.getY(index);
    const ty = point ? point.getY(index) : row1!.getZ!(index);
    summary.transformsRead += 1;
    if (![a, b, tx, c, d, ty].every(Number.isFinite)) return;
    result.magnitude = Math.max(
      result.magnitude,
      Math.abs(a) * absX + Math.abs(b) * absY + Math.abs(tx),
      Math.abs(c) * absX + Math.abs(d) * absY + Math.abs(ty),
    );
    for (const x of [base.minX, base.maxX]) {
      for (const y of [base.minY, base.maxY]) {
        const nextX = a * x + b * y + tx;
        const nextY = c * x + d * y + ty;
        result.minX = Math.min(result.minX, nextX);
        result.minY = Math.min(result.minY, nextY);
        result.maxX = Math.max(result.maxX, nextX);
        result.maxY = Math.max(result.maxY, nextY);
      }
    }
  }
  return result;
}
