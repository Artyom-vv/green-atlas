import { describe, expect, it } from 'vitest';
import * as THREE from 'three';
import { assignBuildingRoofGroups, facadeMaterial } from './facadeAppearance';

describe('prepared facade appearance', () => {
  it('assigns the roof finish to the cap without altering the building geometry', () => {
    const geometry = new THREE.BoxGeometry(18, 6, 12).toNonIndexed();
    const positions = geometry.getAttribute('position').array.slice();
    assignBuildingRoofGroups(geometry);
    const normal = geometry.getAttribute('normal');
    let assigned = 0;
    for (const group of geometry.groups) {
      assigned += group.count;
      for (let i = group.start; i < group.start + group.count; i++)
        expect(group.materialIndex === 1).toBe(normal.getY(i) > 0.99);
    }
    expect(assigned).toBe(normal.count);
    expect(geometry.getAttribute('position').array).toEqual(positions);
    expect(geometry.groups.some((group) => group.materialIndex === 1)).toBe(
      true,
    );
    geometry.dispose();
  });

  it('uses mipmapped low-contrast grain and diffuse readable glazing', () => {
    const wall = facadeMaterial('building')!;
    const roof = facadeMaterial('building_roof')!;
    const glass = facadeMaterial('facade_glass')!;
    expect(wall.map).toBeInstanceOf(THREE.DataTexture);
    expect(wall.map!.generateMipmaps).toBe(true);
    expect(wall.map!.minFilter).toBe(THREE.LinearMipmapLinearFilter);
    expect(wall.map!.wrapS).toBe(THREE.RepeatWrapping);
    expect(roof.color.r).toBeLessThan(wall.color.r);
    expect(glass.color.r).toBeGreaterThan(0.07);
    expect(glass.roughness).toBe(1);
    expect(glass.envMapIntensity).toBe(0);
    expect(glass.transparent).toBe(false);
    [wall, roof, glass].forEach((material) => {
      material.map?.dispose();
      material.dispose();
    });
  });
});
