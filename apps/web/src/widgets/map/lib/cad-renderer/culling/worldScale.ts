import { record } from './contracts';

const ORTHOGONAL_TOLERANCE = 1e-10;
const COLUMN_PAIRS = [
  [0, 4],
  [0, 8],
  [4, 8],
] as const;

/** Three's max-column sphere scaling is safe for orthogonal affine axes. */
export function worldScale(value: unknown): number | undefined {
  const matrix = record(value);
  const e = matrix?.elements;
  if (
    !matrix ||
    !Array.isArray(e) ||
    e.length !== 16 ||
    !e.every(Number.isFinite) ||
    typeof matrix.getMaxScaleOnAxis !== 'function' ||
    e[3] !== 0 ||
    e[7] !== 0 ||
    e[11] !== 0 ||
    e[15] !== 1
  )
    return;
  const scale = Number(matrix.getMaxScaleOnAxis());
  if (!Number.isFinite(scale) || scale <= 0) return;
  // SDK batches have identity world matrices. Also accept rotations and
  // negative/nonuniform scales; an unknown shear must stay unculled.
  for (const [a, b] of COLUMN_PAIRS) {
    const dot = e[a] * e[b] + e[a + 1] * e[b + 1] + e[a + 2] * e[b + 2];
    const length =
      Math.hypot(e[a], e[a + 1], e[a + 2]) *
      Math.hypot(e[b], e[b + 1], e[b + 2]);
    if (Math.abs(dot) > ORTHOGONAL_TOLERANCE * length) return;
  }
  return scale;
}
