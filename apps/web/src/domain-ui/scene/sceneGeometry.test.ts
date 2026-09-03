import { describe, expect, it, vi } from 'vitest';
import * as THREE from 'three';
import { buildContextScene, disposeObjectTree, type SceneContextLike } from './sceneGeometry';

function roadFeature(index: number): SceneContextLike {
  return {
    feature_id: `road-${index}`,
    kind: 'road',
    geometry: { type: 'LineString', coordinates: [[index, 0], [index, 10], [index + 1, 12]] },
  };
}

function buildingFeature(index: number, height?: number): SceneContextLike {
  const x = index * 3;
  return {
    feature_id: `building-${index}`,
    kind: 'building',
    geometry: { type: 'Polygon', coordinates: [[[x, 0], [x + 2, 0], [x + 2, 2], [x, 2], [x, 0]]] },
    height_m: height,
    height_status: height ? 'confirmed' : 'missing',
  };
}

describe('3D DXF context batching', () => {
  it('merges 2500 road entities into one line draw instead of one object each', () => {
    const context = buildContextScene(Array.from({ length: 2_500 }, (_, index) => roadFeature(index)));
    expect(context.group.children).toHaveLength(1);
    const lines = context.group.getObjectByName('context-lines:road');
    expect(lines).toBeInstanceOf(THREE.LineSegments);
    expect((lines as THREE.LineSegments).geometry.getAttribute('position').count).toBe(10_000);
    expect(context.bounds.min.x).toBe(0);
    expect(context.bounds.max.x).toBe(2_500);
  });

  it('keeps missing building heights flat and extrudes only confirmed heights', () => {
    const context = buildContextScene([buildingFeature(0), buildingFeature(1, 18)]);
    expect(context.flatBuildingCount).toBe(1);
    expect(context.confirmedBuildingCount).toBe(1);
    expect(context.bounds.max.y).toBe(18);
    expect(context.group.children.map((child) => child.name).sort()).toEqual([
      'context-fill:building',
      'context-lines:building',
    ]);
  });

  it('disposes each shared geometry and material once', () => {
    const context = buildContextScene([roadFeature(0), roadFeature(1), buildingFeature(0)]);
    const geometries = new Set<THREE.BufferGeometry>();
    const materials = new Set<THREE.Material>();
    context.group.traverse((object) => {
      if (!(object instanceof THREE.Mesh || object instanceof THREE.Line || object instanceof THREE.LineSegments)) return;
      geometries.add(object.geometry);
      const list = Array.isArray(object.material) ? object.material : [object.material];
      list.forEach((material) => materials.add(material));
    });
    const geometrySpies = [...geometries].map((geometry) => vi.spyOn(geometry, 'dispose'));
    const materialSpies = [...materials].map((material) => vi.spyOn(material, 'dispose'));
    disposeObjectTree(context.group);
    geometrySpies.forEach((spy) => expect(spy).toHaveBeenCalledTimes(1));
    materialSpies.forEach((spy) => expect(spy).toHaveBeenCalledTimes(1));
  });
});
