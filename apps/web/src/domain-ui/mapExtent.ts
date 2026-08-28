export type NumericExtent = [number, number, number, number];

export function paddedMapExtent(bounds?: readonly number[] | null, minimumSpan = 20, relativePadding = 0): NumericExtent | undefined {
  if (!bounds || bounds.length !== 4 || !bounds.every(Number.isFinite)) return undefined;
  const [firstX, firstY, secondX, secondY] = bounds;
  let minX = Math.min(firstX, secondX);
  let minY = Math.min(firstY, secondY);
  let maxX = Math.max(firstX, secondX);
  let maxY = Math.max(firstY, secondY);
  const targetSpan = Math.max(0.01, minimumSpan);

  if (maxX - minX < targetSpan) {
    const centerX = (minX + maxX) / 2;
    minX = centerX - targetSpan / 2;
    maxX = centerX + targetSpan / 2;
  }
  if (maxY - minY < targetSpan) {
    const centerY = (minY + maxY) / 2;
    minY = centerY - targetSpan / 2;
    maxY = centerY + targetSpan / 2;
  }

  const paddingRatio = Math.max(0, relativePadding);
  const paddingX = (maxX - minX) * paddingRatio;
  const paddingY = (maxY - minY) * paddingRatio;

  return [minX - paddingX, minY - paddingY, maxX + paddingX, maxY + paddingY];
}
