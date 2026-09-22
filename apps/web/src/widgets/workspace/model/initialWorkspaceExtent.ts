import type { Layer } from '@green/api-client';
import { paddedMapExtent } from '@/shared/geometry/mapExtent';

/** Frame the chosen working territory, not remote legends in CAD model space.
 * This affects the camera only; it neither confirms mappings nor clips data.
 */
export function initialWorkspaceExtent(
  sourceBounds: readonly number[] | null | undefined,
  layers: readonly Pick<Layer, 'mapped_kind' | 'bounds' | 'boundary_candidate'>[],
  preview: boolean,
) {
  const boundaries = preview ? [] : layers.flatMap((layer) => {
    if (layer.mapped_kind !== 'site_border' ||
        layer.boundary_candidate?.status !== 'usable') return [];
    const extent = paddedMapExtent(layer.bounds);
    return extent ? [extent] : [];
  });
  if (!boundaries.length) return paddedMapExtent(sourceBounds, 20, preview ? 0.3 : 0);
  const extent = boundaries[0].slice();
  for (const bounds of boundaries.slice(1)) {
    extent[0] = Math.min(extent[0], bounds[0]);
    extent[1] = Math.min(extent[1], bounds[1]);
    extent[2] = Math.max(extent[2], bounds[2]);
    extent[3] = Math.max(extent[3], bounds[3]);
  }
  return paddedMapExtent(extent, 20, 0.05);
}
