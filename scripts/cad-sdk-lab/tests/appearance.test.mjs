import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import test from 'node:test';
import {
  appearanceSource,
  buildAppearanceScene,
  geometrySignature,
} from './appearance-fixtures.mjs';

const sdk =
  process.env.DXF_VIEWER_SDK_ROOT ??
  resolve('.runtime/cad-sdk-lab/node_modules/dxf-viewer');
const before = JSON.parse(
  await readFile(
    new URL('./appearance-geometry.json', import.meta.url),
    'utf8',
  ),
);

for (const mode of ['direct', 'flattened', 'instanced']) {
  test(`${mode}: appearance partitions keep source primitives, indices and transforms`, async () => {
    const result = await buildAppearanceScene(sdk, appearanceSource(mode));
    assert.deepEqual(geometrySignature(result), before[mode]);
    const { viewer, DxfViewer } = result;
    const objects = viewer.scene.children;
    assert.deepEqual(
      [
        ...new Set(objects.map((object) => object.userData.dxfAppearanceClass)),
      ].sort(),
      ['fill', 'line', 'text'],
    );
    assert.ok(
      objects.every(
        (object) =>
          object.userData.dxfLayer === (mode === 'direct' ? 'A' : 'B'),
      ),
    );
    const meshes = objects.filter((object) => object.isMesh);
    assert.ok(
      meshes.some((object) => object.userData.dxfAppearanceClass === 'text'),
    );
    assert.ok(
      meshes.some((object) => object.userData.dxfAppearanceClass === 'fill'),
    );
    if (mode !== 'direct') {
      viewer.ShowLayers({ A: false, B: true });
      assert.ok(objects.every((object) => !object.visible));
      viewer.ShowLayer('A', true);
      assert.ok(objects.every((object) => object.visible));
    }
    for (const factory of [
      '_CreateSimpleColorMaterial',
      '_CreateSimplePointMaterial',
    ]) {
      const material = DxfViewer.prototype[factory].call(viewer);
      assert.equal(material.uniforms.opacity.value, 1);
      assert.match(material.fragmentShader, /vec4\(color, opacity\)/);
      material.dispose();
    }
  });
}

test('all chunks of a long polyline retain the public line/layer metadata', async () => {
  const result = await buildAppearanceScene(sdk, appearanceSource('chunked'));
  const { viewer } = result;
  assert.ok(viewer.scene.children.length > 1);
  assert.ok(
    viewer.scene.children.every(
      (object) =>
        object.userData.dxfAppearanceClass === 'line' &&
        object.userData.dxfLayer === 'A',
    ),
  );
  assert.deepEqual(geometrySignature(result), before.chunked);
});
