import { useEffect, type RefObject } from 'react';
import type { PatternPreview } from '@green/api-client';
import type Map from 'ol/Map';
import VectorLayer from 'ol/layer/Vector';
import VectorSource from 'ol/source/Vector';
import GeoJSON from 'ol/format/GeoJSON';
import { Fill, Stroke, Style } from 'ol/style';
import { projection } from './projection';

/** Read-only calculation evidence, never CAD source or a selectable work zone. */
export function useSearchDomainLayer(
  mapRef: RefObject<Map | null>,
  domains: PatternPreview['search_domains'],
) {
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !domains?.length) return;
    const formatter = new GeoJSON();
    const features = domains.flatMap((domain) =>
      (['available', 'unresolved', 'pending'] as const).flatMap((state) => {
        const geometry = state === 'available' ? domain.geometry
          : state === 'pending' ? domain.pending_geometry : domain.unresolved_geometry;
        if (!geometry) return [];
        const feature = formatter.readFeature({
          type: 'Feature', geometry, properties: { searchDomainState: state },
        }, { dataProjection: projection, featureProjection: projection });
        return feature;
      }),
    );
    const styles = {
      available: new Style({
        stroke: new Stroke({ color: '#11765c', width: 1.5 }),
        fill: new Fill({ color: '#11765c28' }),
      }),
      unresolved: new Style({
        stroke: new Stroke({ color: '#b66a10', width: 1, lineDash: [4, 4] }),
        fill: new Fill({ color: '#b66a1018' }),
      }),
      pending: new Style({
        stroke: new Stroke({ color: '#64748b', width: 1, lineDash: [2, 6] }),
        fill: new Fill({ color: '#64748b12' }),
      }),
    };
    const source = new VectorSource({ features });
    const layer = new VectorLayer({
      source, zIndex: 2.2,
      style: (feature) => styles[feature.get('searchDomainState') as keyof typeof styles],
    });
    map.addLayer(layer);
    return () => { map.removeLayer(layer); source.clear(); layer.dispose(); };
  }, [mapRef, domains]);
}
