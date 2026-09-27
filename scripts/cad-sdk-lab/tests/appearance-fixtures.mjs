import { createRequire } from 'node:module';
import { readFile, realpath } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { createHash } from 'node:crypto';

const points = [
  { x: 0, y: 0 },
  { x: 4, y: 0 },
  { x: 4, y: 4 },
  { x: 0, y: 4 },
];
const line = {
  type: 'LINE',
  layer: '0',
  colorIndex: 256,
  vertices: points.slice(0, 2),
};
const text = {
  type: 'TEXT',
  layer: '0',
  colorIndex: 256,
  text: 'A',
  textHeight: 2,
  startPoint: { x: 1, y: 1 },
};
const solid = {
  type: 'SOLID',
  layer: '0',
  colorIndex: 256,
  points: points.slice(0, 3),
};
const hatch = {
  type: 'HATCH',
  layer: '0',
  colorIndex: 256,
  isSolid: true,
  boundaryLoops: [{ type: 3, polyline: { vertices: points } }],
};
const insert = (name, layer, x = 30) => ({
  type: 'INSERT',
  name,
  layer,
  handle: `${name}-${layer}-${x}`,
  position: { x, y: 40 },
  xScale: 2,
  yScale: 0.5,
  rotation: 30,
});

export function appearanceSource(mode) {
  const content = [line, text, solid, hatch];
  if (mode === 'instanced')
    content.push(...Array.from({ length: 600 }, () => line));
  const tables = {
    layer: {
      layers: Object.fromEntries(
        ['0', 'A', 'B'].map((name) => [name, { name, color: 0x123456 }]),
      ),
    },
  };
  const blocks = {
    inner: { name: 'inner', position: { x: 1, y: 2 }, entities: content },
    outer: {
      name: 'outer',
      position: { x: 0, y: 0 },
      entities: [insert('inner', 'B', 10)],
    },
  };
  return {
    header: { $INSUNITS: 6 },
    tables,
    blocks,
    entities:
      mode === 'chunked'
        ? [
            {
              type: 'LWPOLYLINE',
              layer: 'A',
              colorIndex: 256,
              vertices: Array.from({ length: 65_540 }, (_, index) => ({
                x: index,
                y: index % 2,
              })),
            },
          ]
        : mode === 'direct'
          ? content.map((entity) => ({ ...entity, layer: 'A' }))
          : [
              insert('outer', 'A'),
              ...(mode === 'instanced' ? [insert('outer', 'A', 80)] : []),
            ],
  };
}

export async function buildAppearanceScene(sdk, source) {
  const fromSdk = (file) => import(pathToFileURL(resolve(sdk, file)).href);
  const { DxfScene } = await fromSdk('src/DxfScene.js');
  const { DxfViewer } = await fromSdk('src/DxfViewer.js');
  const require = createRequire(await realpath(resolve(sdk, 'package.json')));
  const { Scene, LineBasicMaterial } = require('three');
  const { parse } = require('opentype.js');
  const data = await readFile(
    resolve('apps/web/public/assets/fonts/NotoSans-Regular.ttf'),
  );
  const builder = new DxfScene({});
  await builder.Build(structuredClone(source), [
    async () =>
      parse(
        data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength),
      ),
  ]);
  const scene = builder.scene;
  const viewer = Object.create(DxfViewer.prototype);
  Object.assign(viewer, {
    options: {},
    scene: new Scene(),
    layers: new Map(),
    blocks: new Map(),
    _EnsureRenderer() {},
    Clear() {},
    _Emit() {},
    FitView() {},
    _CreateControls() {},
    Render() {},
    _TransformColor: (color) => color,
    _GetSimpleColorMaterial: (color) => new LineBasicMaterial({ color }),
  });
  const listeners = new Map();
  await viewer.Load({
    url: 'fixture',
    workerFactory: () => ({
      addEventListener(type, handler) {
        listeners.set(type, handler);
      },
      postMessage(request) {
        queueMicrotask(() =>
          listeners.get('message')({
            data: {
              ...request,
              data: request.type === 'LOAD' ? { scene } : null,
            },
          }),
        );
      },
      terminate() {},
    }),
  });
  return { scene, viewer, DxfViewer };
}

export function geometrySignature({ scene, viewer }) {
  const primitives = [];
  for (const object of viewer.scene.children) {
    const geometry = object.geometry,
      positions = geometry.getAttribute('position');
    const row0 = geometry.getAttribute('instanceTransform0'),
      row1 = geometry.getAttribute('instanceTransform1');
    const size = object.isMesh ? 3 : 2;
    const indices =
      geometry.index?.array ??
      Array.from({ length: positions.count }, (_, index) => index);
    for (let instance = 0; instance < (row0?.count ?? 1); instance++) {
      for (let offset = 0; offset < indices.length; offset += size) {
        const primitive = [object._dxfViewerLayer.name, size];
        for (let corner = 0; corner < size; corner++) {
          const index = indices[offset + corner],
            x = positions.getX(index),
            y = positions.getY(index);
          primitive.push(
            row0
              ? row0.getX(instance) * x +
                  row0.getY(instance) * y +
                  row0.getZ(instance) +
                  scene.origin.x
              : x + scene.origin.x,
            row1
              ? row1.getX(instance) * x +
                  row1.getY(instance) * y +
                  row1.getZ(instance) +
                  scene.origin.y
              : y + scene.origin.y,
          );
        }
        primitives.push(JSON.stringify(primitive));
      }
    }
  }
  return {
    verticesBytes: scene.vertices.byteLength,
    indicesBytes: scene.indices.byteLength,
    transformsBytes: scene.transforms.byteLength,
    primitives: primitives.length,
    worldGeometrySha256: createHash('sha256')
      .update(primitives.sort().join('\n'))
      .digest('hex'),
    transformsSha256: createHash('sha256')
      .update(new Uint8Array(scene.transforms))
      .digest('hex'),
  };
}
