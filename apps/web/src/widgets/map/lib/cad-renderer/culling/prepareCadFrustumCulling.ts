import { CadBoundsCache } from './CadBoundsCache';
import { LineMargins } from './LineMargins';
import { worldScale } from './worldScale';
import {
  record,
  type CadCullingSummary,
  type CadGeometry,
  type CadObject,
} from './contracts';

/** Enable Three's own frustum test without changing visibility or GPU buffers. */
export function prepareCadFrustumCulling(scene: { children: unknown[] }) {
  const summary: CadCullingSummary = {
    enabledObjects: 0,
    pointObjectsUnculled: 0,
    unsafeObjectsUnculled: 0,
    boundedGeometries: 0,
    positionVerticesRead: 0,
    transformsRead: 0,
  };
  const bounds = new CadBoundsCache(summary);
  const lines = new LineMargins();
  const pending = [...scene.children];
  while (pending.length) {
    const item = record(pending.pop());
    if (!item) continue;
    if (Array.isArray(item.children)) pending.push(...item.children);
    const rawGeometry = record(item.geometry);
    if (!rawGeometry || rawGeometry.isBufferGeometry !== true) continue;
    item.frustumCulled = false;
    if (item.isPoints) {
      summary.pointObjectsUnculled += 1;
      continue; // Pixel-sized POINTS need a distinct raster contract.
    }
    const matrix = record(item.matrixWorld);
    const attributes = record(rawGeometry.attributes);
    if (
      (!item.isMesh && !item.isLine && !item.isLineSegments) ||
      item.isInstancedMesh ||
      !attributes ||
      typeof matrix?.getMaxScaleOnAxis !== 'function'
    ) {
      summary.unsafeObjectsUnculled += 1;
      continue;
    }
    if (worldScale(matrix) === undefined) {
      summary.unsafeObjectsUnculled += 1;
      continue;
    }
    const geometry = rawGeometry as unknown as CadGeometry;
    const sphere = bounds.get(geometry);
    const width = Number(record(item.material)?.linewidth ?? 1);
    if (!sphere || !Number.isFinite(width) || width < 0) {
      summary.unsafeObjectsUnculled += 1;
      continue;
    }
    const line = Boolean(item.isLine || item.isLineSegments);
    if (line)
      lines.add(sphere, item as unknown as CadObject, Math.max(1, width));
    // Defer raster line culling until the first actual viewport margin exists.
    item.frustumCulled = !line;
    summary.enabledObjects += 1;
  }
  return {
    summary,
    /** Source units per CSS pixel, after the application's metre conversion. */
    updateForResolution: (sourceResolution: number) =>
      lines.update(sourceResolution),
  };
}
