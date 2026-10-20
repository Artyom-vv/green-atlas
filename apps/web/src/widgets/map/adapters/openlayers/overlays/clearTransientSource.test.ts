import Feature from 'ol/Feature';
import Point from 'ol/geom/Point';
import VectorSource from 'ol/source/Vector';
import { describe, expect, it, vi } from 'vitest';
import { clearTransientSource } from './clearTransientSource';

describe('transient map source invalidations', () => {
  it('does not invalidate map rendering for repeated empty pointer frames', () => {
    const sources = [new VectorSource(), new VectorSource()];
    const invalidated = vi.fn();
    sources.forEach((source) => source.on('change', invalidated));
    const revisions = sources.map((source) => source.getRevision());
    for (let frame = 0; frame < 120; frame += 1)
      sources.forEach(clearTransientSource);
    expect(invalidated).not.toHaveBeenCalled();
    expect(sources.map((source) => source.getRevision())).toEqual(revisions);
  });

  it('removes an old hover once, releases its listener and clears spatial hits', () => {
    const point = new Point([10, 20]);
    const feature = new Feature(point);
    const source = new VectorSource({ features: [feature] });
    const invalidated = vi.fn();
    const removed = vi.fn();
    source.on('change', invalidated);
    source.on('removefeature', removed);
    clearTransientSource(source);
    clearTransientSource(source);
    point.setCoordinates([30, 40]);
    expect(removed).toHaveBeenCalledOnce();
    expect(invalidated).toHaveBeenCalledOnce();
    expect(source.getFeaturesInExtent([0, 0, 100, 100])).toEqual([]);
    expect(source.hasFeature(feature)).toBe(false);
    source.addFeature(feature);
    invalidated.mockClear();
    clearTransientSource(source);
    expect(invalidated).toHaveBeenCalledOnce();
  });

  it('also clears non-spatial features rather than treating them as empty', () => {
    const feature = new Feature();
    const source = new VectorSource({ features: [feature] });
    clearTransientSource(source);
    expect(source.isEmpty()).toBe(true);
    expect(source.hasFeature(feature)).toBe(false);
  });
});
