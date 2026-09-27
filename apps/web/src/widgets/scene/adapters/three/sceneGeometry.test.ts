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
      'context-lines:building',
    ]);
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
