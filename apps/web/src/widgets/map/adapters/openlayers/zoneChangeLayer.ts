import { zoneChangeSummary } from '@/entities/planting-zone/model/zoneChangeSummary';
import type { ZoneChangePreview } from '@green/api-client';
import type { FeatureLike } from 'ol/Feature';
import Feature from 'ol/Feature';
import GeoJSON from 'ol/format/GeoJSON';
import { Fill, Stroke, Style } from 'ol/style';

/** A separate read-only layer; proposed zones never replace workspace zones. */
export function zoneChangeFeatures(preview: ZoneChangePreview): Feature[] {
  const summary = zoneChangeSummary(preview);
  if (!summary) return [];
  const entries = [
    ...(summary.before && (!summary.after || summary.geometryChanged)
      ? [
          {
            zone: summary.before,
            role: preview.operation === 'delete' ? 'deletion' : 'before',
          },
        ]
      : []),
    ...(summary.after ? [{ zone: summary.after, role: 'after' }] : []),
  ];
  try {
    return entries.map(({ zone, role }) => {
      const feature = new GeoJSON().readFeature({
        type: 'Feature',
        geometry: zone.geometry,
        properties: { zonePreviewRole: role },
      }) as Feature;
      feature.setId(`zone-preview:${preview.id}:${role}`);
      return feature;
    });
  } catch {
    return [];
  }
}

const styles = {
  before: new Style({
    stroke: new Stroke({ color: '#596675', width: 2, lineDash: [7, 5] }),
  }),
  after: new Style({
    stroke: new Stroke({ color: '#225cff', width: 2.5 }),
    fill: new Fill({ color: '#225cff16' }),
  }),
  deletion: new Style({
    stroke: new Stroke({ color: '#b42318', width: 2.5, lineDash: [7, 4] }),
    fill: new Fill({ color: '#b4231812' }),
  }),
};

export function zoneChangeStyle(feature: FeatureLike): Style {
  return (
    styles[feature.get('zonePreviewRole') as keyof typeof styles] ??
    styles.before
  );
}
