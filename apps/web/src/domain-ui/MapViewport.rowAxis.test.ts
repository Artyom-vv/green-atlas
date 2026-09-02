import { describe, expect, it, vi } from 'vitest';
import Feature from 'ol/Feature';
import LineString from 'ol/geom/LineString';
import Point from 'ol/geom/Point';
import Polygon from 'ol/geom/Polygon';
import VectorSource from 'ol/source/Vector';
import { axisCoordinatesFromFeature, nearestLineFeature } from './MapViewport';

describe('nearestLineFeature', () => {
  it('queries only the local spatial-index extent on a dense source', () => {
    const farFeatures = Array.from({ length: 10_000 }, (_, index) => new Feature({
      geometry: new LineString([[10_000 + index * 2, 10_000], [10_001 + index * 2, 10_001]]),
    }));
    const nearbyPoint = new Feature({ geometry: new Point([1, 1]) });
    const nearbyLine = new Feature({ geometry: new LineString([[-5, 3], [5, 3]]) });
    nearbyLine.setId('nearby-axis');
    const source = new VectorSource({ features: [...farFeatures, nearbyPoint, nearbyLine] });
    const fullScan = vi.spyOn(source, 'getFeatures');

    expect(nearestLineFeature([source], [0, 0], 4)?.getId()).toBe('nearby-axis');
    expect(fullScan).not.toHaveBeenCalled();
  });

  it('does not return a line outside the pointer tolerance', () => {
    const source = new VectorSource({ features: [new Feature({ geometry: new LineString([[20, 20], [30, 20]]) })] });
    expect(nearestLineFeature([source], [0, 0], 4)).toBeUndefined();
  });

  it('treats a visible polygon edge as a selectable DXF axis', () => {
    const polygon = new Feature({ geometry: new Polygon([[[0, 0], [20, 0], [20, 10], [0, 10], [0, 0]]]) });
    polygon.setId('building-edge');
    const source = new VectorSource({ features: [polygon] });

    expect(nearestLineFeature([source], [10, 1], 2)?.getId()).toBe('building-edge');
    expect(axisCoordinatesFromFeature(polygon, [10, 1])).toEqual([[0, 0], [20, 0], [20, 10], [0, 10], [0, 0]]);
  });
});
