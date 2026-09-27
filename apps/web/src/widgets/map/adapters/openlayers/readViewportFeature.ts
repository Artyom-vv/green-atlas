import type Feature from 'ol/Feature';
import GeoJSON from 'ol/format/GeoJSON';
import { projection } from './projection';
import { isTechnical3dMapFeature } from './hitTargets';
import type { ViewportFeatureCache } from './ViewportFeatureCache';
import type { GeometrySourceChanges } from './geometrySources';

export interface RawViewportFeature {
  type?: string;
  id?: string | number;
  geometry?: Record<string, unknown>;
  properties?: Record<string, unknown>;
}

interface ReadViewportFeatureOptions {
  cache: ViewportFeatureCache;
  format: GeoJSON;
  representation: string;
  changes: GeometrySourceChanges;
}

interface ViewportFeatureRead {
  feature?: Feature;
  parsed: boolean;
}

/** Compare exact incoming content only on delivery, never during React render. */
export function readViewportFeature(
  raw: RawViewportFeature,
  index: number,
  { cache, format, representation, changes }: ReadViewportFeatureOptions,
): ViewportFeatureRead {
  const explicitId =
    raw.id === undefined || raw.id === null ? undefined : String(raw.id);
  const previous = explicitId === undefined ? undefined : cache.get(explicitId);
  const forgetPrevious = () => {
    if (!previous || explicitId === undefined) return;
    cache.delete(explicitId);
    changes.remove(previous);
  };
  if (raw.type !== 'Feature' || !raw.geometry) {
    forgetPrevious();
    return { parsed: false };
  }
  // Compare numbers directly; serializing all coordinates costs more than
  // parsing them on dense CAD. Only small non-coordinate attributes use JSON.
  if (previous && cache.matches(previous, raw, representation))
    return { feature: previous, parsed: false };
  let feature: Feature;
  try {
    feature = format.readFeature(raw, {
      dataProjection: projection,
      featureProjection: projection,
    }) as Feature;
  } catch {
    forgetPrevious();
    return { parsed: true };
  }
  if (isTechnical3dMapFeature(feature)) {
    forgetPrevious();
    return { parsed: true };
  }
  const shape = feature.getGeometry();
  const fallbackId = `geometry-${String(feature.get('kind') ?? 'source')}-${String(feature.get('rule_id') ?? feature.get('source_handle') ?? feature.get('source_layer') ?? index)}-${shape?.getExtent().join(':') ?? index}`;
  const id = explicitId ?? fallbackId;
  feature.setId(id);
  const replaced = previous ?? cache.get(id);
  if (replaced) changes.remove(replaced);
  for (const evicted of cache.remember(feature, raw, representation))
    changes.remove(evicted);
  return { feature, parsed: true };
}
