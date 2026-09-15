import { createRequire } from 'node:module';
import { describe, expect, it, vi } from 'vitest';
import { CadAppearance } from './CadAppearance';

const sdkRequire = createRequire(
  createRequire(import.meta.url).resolve('dxf-viewer'),
);
const sdk = sdkRequire('three') as typeof import('three');

function fixture() {
  const material = new sdk.RawShaderMaterial({
    uniforms: {
      color: { value: new sdk.Color('#ab1234') },
      opacity: { value: 1 },
    },
    depthTest: false,
    depthWrite: false,
  });
  const objects = ['fill', 'line', 'text', 'line'].map(
    (appearanceClass, index) => {
      const object = new sdk.Mesh(new sdk.BufferGeometry(), material);
      object.userData = {
        dxfLayer: 'Road',
        dxfAppearanceClass: appearanceClass,
      };
      object.renderOrder = index + 10;
      object.visible = index !== 0;
      return object;
    },
  );
  return { material, objects, appearance: new CadAppearance(objects) };
}

describe('CAD project appearance', () => {
  it('separates fills from opaque text while restoring exact source materials and order', () => {
    const f = fixture();
    const before = f.objects.map((object) => ({
      material: object.material,
      order: object.renderOrder,
      geometry: object.geometry,
      matrix: object.matrix,
      visible: object.visible,
    }));
    expect(f.appearance.apply('design', { Road: 'road' })).toBe(true);
    const [fill, line, text, repeated] = f.objects.map(
      (object) => object.material,
    );
    expect(fill.uniforms.opacity.value).toBe(0.12);
    expect(line.uniforms.opacity.value).toBe(1);
    expect(text.uniforms.opacity.value).toBe(1);
    expect(line).toBe(repeated);
    expect(text).not.toBe(fill);
    expect(f.objects.map((object) => object.renderOrder)).toEqual([
      -2, -1, 0, -1,
    ]);
    expect(line.uniforms.color.value.getHexString()).toBe('c7ced6');
    expect(text.uniforms.color.value.getHexString()).toBe('596675');
    expect(f.material.uniforms.color.value.getHexString()).toBe('ab1234');
    const disposal = [fill, line, text].map((material) =>
      vi.spyOn(material, 'dispose'),
    );
    expect(f.appearance.apply('design', { Road: 'road' })).toBe(false);
    expect(f.appearance.apply('cad', {})).toBe(true);
    f.objects.forEach((object, index) => {
      expect(object.material).toBe(before[index].material);
      expect(object.renderOrder).toBe(before[index].order);
      expect(object.geometry).toBe(before[index].geometry);
      expect(object.matrix).toBe(before[index].matrix);
      expect(object.visible).toBe(before[index].visible);
    });
    disposal.forEach((dispose) => expect(dispose).toHaveBeenCalledTimes(1));
  });

  it('uses only confirmed layer roles and disposes derived materials on remapping/unmount', () => {
    const f = fixture();
    f.appearance.apply('design', {});
    expect(f.objects[1].material.uniforms.color.value.getHexString()).toBe(
      '85919e',
    );
    const previous = vi.spyOn(f.objects[1].material, 'dispose');
    f.appearance.apply('design', { Road: 'utility' });
    expect(previous).toHaveBeenCalledOnce();
    expect(f.objects[1].material.uniforms.color.value.getHexString()).toBe(
      '8a4b00',
    );
    const derived = vi.spyOn(f.objects[1].material, 'dispose');
    const original = vi.spyOn(f.material, 'dispose');
    f.appearance.dispose();
    expect(derived).toHaveBeenCalledOnce();
    expect(original).not.toHaveBeenCalled();
    expect(f.objects.every((object) => object.material === f.material)).toBe(
      true,
    );
  });

  it('leaves unrelated and unclassified scene objects untouched', () => {
    const object = new sdk.Mesh(
      new sdk.BufferGeometry(),
      new sdk.MeshBasicMaterial(),
    );
    const material = object.material;
    const appearance = new CadAppearance([object, null, 1]);
    appearance.apply('design', {});
    expect(object.material).toBe(material);
    expect(object.renderOrder).toBe(0);
    appearance.dispose();
  });
});
