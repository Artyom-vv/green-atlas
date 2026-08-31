import { describe, expect, it } from 'vitest';
import VectorSource from 'ol/source/Vector';
import Circle from 'ol/geom/Circle';
import type { PlanObject } from '@green/api-client';
import { syncPlanFeatures } from './MapViewport';

const object = (id: string, x: number): PlanObject => ({
  id,
  kind: 'tree',
  x,
  y: 20,
  radius: 1.6,
  layout_radius_m: 1.6,
  size_class: 'unspecified',
  locked: false,
  status: 'valid',
  spacing_policy: 'balanced',
});

describe('plan feature diff', () => {
  it('keeps unchanged OpenLayers features instead of clearing the plan layer', () => {
    const source = new VectorSource();
    syncPlanFeatures(source, [object('a', 10), object('b', 30)]);
    const stable = source.getFeatureById('a');

    syncPlanFeatures(source, [object('a', 10), object('b', 35), object('c', 50)]);

    expect(source.getFeatureById('a')).toBe(stable);
    expect((source.getFeatureById('b')?.getGeometry() as Circle).getCenter()).toEqual([35, 20]);
    expect(source.getFeatureById('c')).not.toBeNull();
    syncPlanFeatures(source, [object('a', 10)]);
    expect(source.getFeatureById('b')).toBeNull();
  });
});
