import type { ViewportGeometryContext } from './viewportGeometryContext';

export interface ViewportResolutionRange {
  min: number;
  max: number;
  min_inclusive: boolean;
  max_inclusive: boolean;
}

/** Only a response for this exact query can suppress another scale request. */
export function viewportResolutionRange(
  geometry: Record<string, unknown> | undefined,
  expected: ViewportGeometryContext,
): ViewportResolutionRange | undefined {
  const context = geometry?.viewportContext as
    ViewportGeometryContext | undefined;
  if (
    context?.projectId !== expected.projectId ||
    context.geometryVersion !== expected.geometryVersion ||
    context.sourceKey !== expected.sourceKey ||
    context.resolution !== expected.resolution
  )
    return;
  const metadata = geometry?.metadata as Record<string, unknown> | undefined;
  if (
    typeof metadata?.representation_id !== 'string' ||
    !metadata.representation_id ||
    (expected.geometryVersion !== undefined &&
      metadata.geometry_version !== expected.geometryVersion)
  )
    return;
  const range = metadata.resolution_range as
    Partial<ViewportResolutionRange> | undefined;
  if (
    !range ||
    typeof range.min !== 'number' ||
    typeof range.max !== 'number' ||
    !Number.isFinite(range.min) ||
    !Number.isFinite(range.max) ||
    range.min < 0 ||
    range.min >= range.max ||
    typeof range.min_inclusive !== 'boolean' ||
    typeof range.max_inclusive !== 'boolean'
  )
    return;
  return range as ViewportResolutionRange;
}

export function resolutionInRange(
  resolution: number,
  range: ViewportResolutionRange,
) {
  return (
    (range.min_inclusive ? resolution >= range.min : resolution > range.min) &&
    (range.max_inclusive ? resolution <= range.max : resolution < range.max)
  );
}
