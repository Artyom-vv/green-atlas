import Feature from 'ol/Feature';
import Circle from 'ol/geom/Circle';
import View from 'ol/View';
import VectorLayer from 'ol/layer/Vector';
import VectorSource from 'ol/source/Vector';
import { describe, expect, it } from 'vitest';
import { syncPlanFeatures } from './planFeatures';
import { createPlanComparisonDisplay } from './planComparisonDisplay';
import type { PlanObject } from '@green/api-client';
import { projection } from '../projection';

const tree: PlanObject = {
  id: 'tree',
  kind: 'tree',
  x: 10,
  y: 20,
  radius: 1,
  locked: false,
  status: 'valid',
  spacing_policy: 'balanced',
  size_class: 'standard',
};

describe('OpenLayers comparison visibility', () => {
  it('shows only the saved plan after planting without replacing source features or moving the view', () => {
    const savedSource = new VectorSource();
    syncPlanFeatures(savedSource, [tree]);
    const savedFeature = savedSource.getFeatureById('tree');
    const cadFeature = new Feature(new Circle([3, 4], 2));
    const cadSource = new VectorSource({ features: [cadFeature] });
    const cad = new VectorLayer({ source: cadSource });
    const savedPlan = new VectorLayer({ source: savedSource });
    const draftSource = new VectorSource({
      features: [new Feature(new Circle([30, 40], 1))],
    });
    const draft = new VectorLayer({ source: draftSource });
    const selection = new VectorLayer({ source: new VectorSource() });
    const hiddenOverlay = new VectorLayer({ visible: false });
    const view = new View({
      projection,
      center: [13, 17],
      resolution: 2,
      rotation: 0.4,
    });
    const camera = {
      center: view.getCenter(),
      resolution: view.getResolution(),
      rotation: view.getRotation(),
    };
    const display = createPlanComparisonDisplay({
      savedPlan,
      editingOverlays: [draft, selection, hiddenOverlay],
    });

    for (const mode of ['after', 'before', 'after', 'before'] as const) {
      display.apply(mode);
      expect(savedPlan.getVisible()).toBe(mode === 'after');
      expect(draft.getVisible()).toBe(false);
      expect(selection.getVisible()).toBe(false);
      expect(cad.getVisible()).toBe(true);
      expect(cadSource.getFeatures()).toEqual([cadFeature]);
      expect(savedSource.getFeatureById('tree')).toBe(savedFeature);
      expect(draftSource.getFeatures()).toHaveLength(1);
      expect({
        center: view.getCenter(),
        resolution: view.getResolution(),
        rotation: view.getRotation(),
      }).toEqual(camera);
    }
    display.apply('editing');
    expect(savedPlan.getVisible()).toBe(true);
    expect(draft.getVisible()).toBe(true);
    expect(selection.getVisible()).toBe(true);
    expect(hiddenOverlay.getVisible()).toBe(false);
  });

  it('uses current committed updates and deletions while the original CAD vegetation remains visible', () => {
    const source = new VectorSource();
    syncPlanFeatures(source, [tree]);
    const savedPlan = new VectorLayer({ source });
    const original = new Feature(new Circle([1, 1], 1));
    const cad = new VectorLayer({
      source: new VectorSource({ features: [original] }),
    });
    const display = createPlanComparisonDisplay({
      savedPlan,
      editingOverlays: [],
    });
    display.apply('before');
    syncPlanFeatures(source, [{ ...tree, x: 50 }]);
    display.apply('after');
    expect(
      (source.getFeatureById('tree')?.getGeometry() as Circle).getCenter(),
    ).toEqual([50, 20]);
    syncPlanFeatures(source, []);
    display.apply('before');
    expect(source.getFeatures()).toHaveLength(0);
    expect(cad.getVisible()).toBe(true);
    expect(cad.getSource()?.getFeatures()).toEqual([original]);
  });
});
