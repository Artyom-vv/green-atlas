import type { RefObject } from 'react';
import type { MapViewportOptions } from '../../model/mapViewportOptions';
import type Map from 'ol/Map';
import type VectorSource from 'ol/source/Vector';
import Feature from 'ol/Feature';
import Point from 'ol/geom/Point';
import Snap from 'ol/interaction/Snap';

export interface AttachSnappingOptions extends Pick<
  MapViewportOptions,
  'tool'
> {
  map: Map | null;
  guideSource: VectorSource;
  targetSource: VectorSource;
  snapInteractionRef: RefObject<Snap | null>;
  rebuildSnapTargets: () => void;
}

/** Attach one adapter responsibility; its owner calls the returned cleanup. */
export function attachSnapping({
  map,
  tool,
  guideSource,
  targetSource,
  snapInteractionRef,
  rebuildSnapTargets,
}: AttachSnappingOptions) {
  if (!map) return;
  if (snapInteractionRef.current)
    map.removeInteraction(snapInteractionRef.current);
  snapInteractionRef.current = null;
  guideSource.clear();
  if (tool !== 'draw_area') return;
  rebuildSnapTargets();
  const snap = new Snap({
    source: targetSource,
    edge: true,
    vertex: true,
    intersection: true,
    pixelTolerance: 12,
  });
  snap.on('snap', (event) => {
    guideSource.clear();
    guideSource.addFeature(new Feature({ geometry: new Point(event.vertex) }));
  });
  snap.on('unsnap', () => {
    guideSource.clear();
  });
  map.addInteraction(snap);
  snapInteractionRef.current = snap;
  return () => {
    map.removeInteraction(snap);
    guideSource.clear();
    if (snapInteractionRef.current === snap) snapInteractionRef.current = null;
  };
}
