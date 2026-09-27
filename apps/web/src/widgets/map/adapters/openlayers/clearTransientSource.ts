import type VectorSource from 'ol/source/Vector';

/** Empty hover/cursor clears must not invalidate every map layer on motion. */
export function clearTransientSource(source: VectorSource): void {
  // VectorSource.clear emits change even when empty. Keep its normal removal
  // events for populated sources, including features with no geometry.
  if (!source.isEmpty()) source.clear();
}
