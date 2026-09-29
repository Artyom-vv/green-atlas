import type { MapExtent } from './mapContracts';
import {
  resolutionInRange,
  type ViewportResolutionRange,
} from './viewportResolutionRange';

export const VIEWPORT_REQUEST_POLICY = {
  // A quarter-screen margin keeps short pans covered without requesting
  // almost seven screenfuls of dense CAD geometry on every settled zoom.
  bufferRatio: 0.25,
  safeInsetRatio: 0.1,
  settleMs: 180,
  cacheMs: 30_000,
} as const;

export interface BufferedMapRequest {
  projectId: string;
  extent: MapExtent;
  resolution: number;
}

/** Spatial reuse is valid only when the previous response covered all hits. */
export interface CompleteViewportCoverage {
  resolutionRange?: ViewportResolutionRange;
}

export function bufferedMapRequest(
  projectId: string,
  extent: MapExtent,
  resolution: number,
): BufferedMapRequest {
  const width = Math.max(1, extent[2] - extent[0]);
  const height = Math.max(1, extent[3] - extent[1]);
  const ratio = VIEWPORT_REQUEST_POLICY.bufferRatio;
  return {
    projectId,
    extent: [
      extent[0] - width * ratio,
      extent[1] - height * ratio,
      extent[2] + width * ratio,
      extent[3] + height * ratio,
    ].map((value) => Number(value.toFixed(1))) as MapExtent,
    // Rounding here could cross a server's exact/visibility boundary.
    resolution,
  };
}

export function viewportCovered(
  previous: BufferedMapRequest | undefined,
  projectId: string,
  extent: MapExtent,
  resolution: number,
  coverage?: CompleteViewportCoverage,
) {
  if (!previous || previous.projectId !== projectId) return false;
  const next = bufferedMapRequest(projectId, extent, resolution);
  if (
    previous.resolution === resolution &&
    previous.extent.every((value, index) => value === next.extent[index])
  )
    return true;
  // A budgeted wide response can omit all local detail after a narrow zoom
  // or a short pan. Keep only exact-request dedup until completeness is known.
  if (!coverage) return false;
  const insetX =
    (previous.extent[2] - previous.extent[0]) *
    VIEWPORT_REQUEST_POLICY.safeInsetRatio;
  const insetY =
    (previous.extent[3] - previous.extent[1]) *
    VIEWPORT_REQUEST_POLICY.safeInsetRatio;
  return (
    (previous.resolution === resolution ||
      (coverage.resolutionRange !== undefined &&
        resolutionInRange(resolution, coverage.resolutionRange))) &&
    extent[0] >= previous.extent[0] + insetX &&
    extent[1] >= previous.extent[1] + insetY &&
    extent[2] <= previous.extent[2] - insetX &&
    extent[3] <= previous.extent[3] - insetY
  );
}
