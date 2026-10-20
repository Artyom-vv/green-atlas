import { describe, expect, it, vi } from 'vitest';
import * as THREE from 'three';
import {
  buildContextScene,
  disposeObjectTree,
  type SceneContextLike,
} from '@/widgets/scene/adapters/three/sceneGeometry';

function roadFeature(index: number): SceneContextLike {
  return {
    feature_id: `road-${index}`,
    kind: 'road',
    geometry: {
      type: 'LineString',
      coordinates: [
        [index, 0],
        [index, 10],
        [index + 1, 12],
      ],
    },
  };
}

function buildingFeature(
  index: number,
  height?: number,
  heightStatus: 'confirmed' | 'estimated' = 'confirmed',
): SceneContextLike {
  const x = index * 3;
  return {
    feature_id: `building-${index}`,
    kind: 'building',
    geometry: {
      type: 'Polygon',
      coordinates: [
        [
          [x, 0],
          [x + 2, 0],
          [x + 2, 2],
          [x, 2],
          [x, 0],
        ],
      ],
    },
    height_m: height,
    height_status: height ? heightStatus : 'missing',
  };
}

describe('3D DXF context batching', () => {
  it('merges 2500 road entities into one line draw instead of one object each', () => {
    const context = buildContextScene(
      Array.from({ length: 2_500 }, (_, index) => roadFeature(index)),
    );
    expect(context.group.children).toHaveLength(2);
    expect(context.group.getObjectByName('context-datum-base')).toBeInstanceOf(
      THREE.Mesh,
    );
    const lines = context.group.getObjectByName('context-lines:road');
    expect(lines).toBeInstanceOf(THREE.LineSegments);
    expect(
      (lines as THREE.LineSegments).geometry.getAttribute('position').count,
    ).toBe(10_000);
    expect(context.bounds.min.x).toBe(0);
    expect(context.bounds.max.x).toBe(2_500);
  });

  it('keeps missing heights flat and extrudes confirmed and explicitly labelled estimated heights', () => {
    const context = buildContextScene([
      buildingFeature(0),
      buildingFeature(1, 18),
      buildingFeature(2, 12, 'estimated'),
    ]);
    expect(context.flatBuildingCount).toBe(1);
    expect(context.confirmedBuildingCount).toBe(2);
    expect(context.bounds.max.y).toBe(18);
    expect(context.group.children.map((child) => child.name).sort()).toEqual([
      'context-datum-base',
      'context-fill:building',
      'context-fill:building_flat',
      'context-lines:building',
    ]);
  });

  it('gives ground covers priority over coincident flat building marks without altering their coordinates', () => {
    const flat = buildingFeature(0);
    const lawn: SceneContextLike = {
      feature_id: 'lawn-overlap',
      kind: 'lawn',
      geometry: {
        type: 'Polygon',
        coordinates: [
          [
            [1, 0],
            [3, 0],
            [3, 2],
            [1, 2],
            [1, 0],
          ],
        ],
      },
    };
    const original = structuredClone([flat, lawn]);
    // Input order must not decide the winner of coplanar fills.
    for (const features of [
      [flat, lawn],
      [lawn, flat],
    ]) {
      const context = buildContextScene(features);
      const footprint = context.group.getObjectByName(
        'context-fill:building_flat',
      ) as THREE.Mesh;
      const cover = context.group.getObjectByName(
        'context-fill:lawn',
      ) as THREE.Mesh;
      const datum = context.group.getObjectByName(
        'context-datum-base',
      ) as THREE.Mesh;
      footprint.geometry.computeBoundingBox();
      cover.geometry.computeBoundingBox();
      expect(footprint.geometry.boundingBox!.min.x).toBe(0);
      expect(footprint.geometry.boundingBox!.max.x).toBe(2);
      expect(footprint.geometry.boundingBox!.min.z).toBe(-2);
      expect(footprint.geometry.boundingBox!.max.z).toBeCloseTo(0);
      expect(footprint.geometry.boundingBox!.min.y).toBeCloseTo(0.012);
      expect(cover.geometry.boundingBox!.min.y).toBeCloseTo(0.012);
      expect(footprint.geometry.boundingBox!.max.y).toBeCloseTo(
        cover.geometry.boundingBox!.max.y,
      );
      expect((footprint.material as THREE.Material).depthWrite).toBe(false);
      expect((cover.material as THREE.Material).depthWrite).toBe(true);
      expect(datum.renderOrder).toBe(-2);
      expect(footprint.renderOrder).toBe(-1);
      expect(cover.renderOrder).toBeGreaterThan(footprint.renderOrder);
      expect(footprint.castShadow).toBe(false);
      expect(footprint.userData.sourceKind).toBe('building');
      expect(context.flatBuildingCount).toBe(1);
      expect(context.confirmedBuildingCount).toBe(0);
      context.group.updateWorldMatrix(true, true);
      const ray = new THREE.Raycaster(
        new THREE.Vector3(0.5, 10, -1),
        new THREE.Vector3(0, -1, 0),
      );
      expect(ray.intersectObject(footprint)).toHaveLength(1);
      expect(ray.intersectObject(cover)).toHaveLength(0);
      ray.set(new THREE.Vector3(1.5, 10, -1), new THREE.Vector3(0, -1, 0));
      expect(ray.intersectObject(footprint).length).toBeGreaterThan(0);
      expect(ray.intersectObject(cover).length).toBeGreaterThan(0);
      disposeObjectTree(context.group);
    }
    expect([flat, lawn]).toEqual(original);
  });

  it('keeps known building volumes depth-writing and shadow-casting independently of flat marks', () => {
    const context = buildContextScene([
      buildingFeature(0),
      buildingFeature(1, 18),
      buildingFeature(2, 12, 'estimated'),
    ]);
    const flat = context.group.getObjectByName(
      'context-fill:building_flat',
    ) as THREE.Mesh;
    const measured = context.group.getObjectByName(
      'context-fill:building',
    ) as THREE.Mesh;
    measured.geometry.computeBoundingBox();
    flat.geometry.computeBoundingBox();
    expect((measured.material as THREE.Material).depthWrite).toBe(true);
    expect(measured.castShadow).toBe(true);
    expect(measured.renderOrder).toBe(1);
    expect(measured.geometry.boundingBox!.min.y).toBeCloseTo(0);
    expect(measured.geometry.boundingBox!.max.y).toBeCloseTo(18);
    expect(flat.geometry.boundingBox!.min.y).toBeCloseTo(0.012);
    expect((flat.material as THREE.Material).depthWrite).toBe(false);
    expect(flat.castShadow).toBe(false);
    expect(context.confirmedBuildingCount).toBe(2);
    expect(context.flatBuildingCount).toBe(1);
    disposeObjectTree(context.group);
  });

  it('keeps polygon fills aligned with DXF linework on the world Z axis', () => {
    const context = buildContextScene([
      {
        feature_id: 'water-1',
        kind: 'water',
        geometry: {
          type: 'Polygon',
          coordinates: [
            [
              [10, 20],
              [16, 20],
              [16, 24],
              [10, 24],
              [10, 20],
            ],
          ],
        },
      },
    ]);
    const fill = context.group.getObjectByName(
      'context-fill:water',
    ) as THREE.Mesh;
    const lines = context.group.getObjectByName(
      'context-lines:water',
    ) as THREE.LineSegments;
    fill.geometry.computeBoundingBox();
    lines.geometry.computeBoundingBox();
    expect(fill.geometry.boundingBox?.min.z).toBeCloseTo(-24);
    expect(fill.geometry.boundingBox?.max.z).toBeCloseTo(-20);
    expect(lines.geometry.boundingBox?.min.z).toBeCloseTo(-24);
    expect(lines.geometry.boundingBox?.max.z).toBeCloseTo(-20);
  });

  it('renders confirmed XYZ terrain and samples its elevation for draped context', () => {
    const context = buildContextScene(
      [roadFeature(0)],
      [
        {
          primitive_type: 'surface_mesh',
          terrain_mapping_status: 'confirmed',
          vertices: [
            [0, 0, 2],
            [10, 0, 4],
            [10, 10, 6],
            [0, 10, 4],
          ],
          faces: [[0, 1, 2, 3]],
        },
      ],
    );
    expect(context.hasTerrain).toBe(true);
    expect(
      context.group.getObjectByName('confirmed-terrain:copernicus-glo90'),
    ).toBeInstanceOf(THREE.Mesh);
    expect(context.group.getObjectByName('context-datum-base')).toBeUndefined();
    expect(context.terrainElevationAt(5, -5)).toBeCloseTo(4);
    const lines = context.group.getObjectByName(
      'context-lines:road',
    ) as THREE.LineSegments;
    expect(lines.geometry.getAttribute('position').getY(0)).toBeCloseTo(2.065);
  });

  it('disposes each shared geometry and material once', () => {
    const context = buildContextScene([
      roadFeature(0),
      roadFeature(1),
      buildingFeature(0),
    ]);
    const geometries = new Set<THREE.BufferGeometry>();
    const materials = new Set<THREE.Material>();
    context.group.traverse((object) => {
      if (!(
        object instanceof THREE.Mesh ||
        object instanceof THREE.Line ||
        object instanceof THREE.LineSegments
      ))
        return;
      geometries.add(object.geometry);
      const list = Array.isArray(object.material)
        ? object.material
        : [object.material];
      list.forEach((material) => materials.add(material));
    });
    const geometrySpies = [...geometries].map((geometry) =>
      vi.spyOn(geometry, 'dispose'),
    );
    const materialSpies = [...materials].map((material) =>
      vi.spyOn(material, 'dispose'),
    );
    disposeObjectTree(context.group);
    geometrySpies.forEach((spy) => expect(spy).toHaveBeenCalledTimes(1));
    materialSpies.forEach((spy) => expect(spy).toHaveBeenCalledTimes(1));
  });
});
