import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFile, realpath } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import test from "node:test";

const repository = fileURLToPath(new URL("../../../", import.meta.url));
const sdk = process.env.DXF_VIEWER_SDK_ROOT ?? resolve(repository,
  ".runtime/cad-sdk-lab/node_modules/dxf-viewer");
const fromSdk = (path) => import(pathToFileURL(resolve(sdk, path)).href);
const { DxfScene } = await fromSdk("src/DxfScene.js");
const { DxfViewer } = await fromSdk("src/DxfViewer.js");
const sdkRequire = createRequire(await realpath(resolve(sdk, "package.json")));
const { Scene, LineBasicMaterial } = await import(pathToFileURL(sdkRequire.resolve("three")).href);
const packageInfo = JSON.parse(await readFile(resolve(sdk, "package.json"), "utf8"));
assert.equal(packageInfo.version, "1.0.44", "This qualification patch is pinned to 1.0.44");

const COLORS = { "0": 0xffffff, A: 0xaa0000, B: 0x0000aa, C: 0x00aa00, D: 0xaaaa00 };
const layerTable = () => ({ layer: { layers: Object.fromEntries(Object.entries(COLORS)
  .map(([name, color]) => [name, { name, color }])) } });
const line = (layer, colorIndex = 256, y = 0, extra = {}) => ({
  type: "LINE", layer, colorIndex, handle: `${layer}-${y}`,
  vertices: [{ x: 10, y: 20 + y }, { x: 12, y: 20 + y }], ...extra,
});
const insert = (name, layer, extra = {}) => ({
  type: "INSERT", name, layer, handle: `${name}-${layer}`,
  position: { x: 500000, y: 600000 }, ...extra,
});
const block = (name, entities) => ({ name, position: { x: 10, y: 20 }, entities });
const model = (blocks, entities) => ({ header: { $INSUNITS: 6 }, tables: layerTable(), blocks, entities });

async function prepare(source) {
  const builder = new DxfScene({});
  await builder.Build(structuredClone(source), []);
  return builder.scene;
}

// Drive the actual SDK Load/Batch/Layer/ShowLayer pipeline with its ordinary
// worker protocol; only WebGL drawing and DOM event dispatch are substituted.
async function mountPrepared(scene) {
  const viewer = Object.create(DxfViewer.prototype);
  Object.assign(viewer, {
    options: {}, scene: new Scene(), layers: new Map(), blocks: new Map(),
    _EnsureRenderer() {}, Clear() {}, _Emit() {}, FitView() {},
    _CreateControls() {}, Render() {}, _TransformColor: (color) => color,
    _GetSimpleColorMaterial: (color) => new LineBasicMaterial({ color }),
  });
  const listeners = new Map();
  await viewer.Load({ url: "fixture", workerFactory: () => ({
    addEventListener(type, handler) { listeners.set(type, handler); },
    postMessage(request) {
      queueMicrotask(() => listeners.get("message")({ data: {
        ...request, data: request.type === "LOAD" ? { scene } : null,
      } }));
    },
    terminate() {},
  }) });
  return viewer;
}

function descriptors(viewer) {
  return viewer.scene.children.map((object) => ({
    object,
    layer: object._dxfViewerLayer.name,
    gates: object._dxfViewerVisibilityLayers.map((gate) => gate.name).sort(),
    color: object.material.color.getHex(),
  }));
}

for (const repeated of [false, true]) {
  const mode = repeated ? "instanced" : "flattened";
  const repetitions = repeated ? 180 : 1;
  const leaves = Array.from({ length: repetitions }, (_, i) => [
    line("0", 256, i * 3), line("C", 256, i * 3 + 1), line("C", 0, i * 3 + 2),
  ]).flat();

  test(`${mode}: layer 0 inherits each INSERT, explicit leaf layer and BYBLOCK stay distinct`, async () => {
    const definitions = repeated ? { shared: block("shared", leaves) }
      : { one: block("one", leaves), two: block("two", leaves) };
    const source = model(definitions, [
      insert(repeated ? "shared" : "one", "A", { colorIndex: 2, color: 0xffff00 }),
      insert(repeated ? "shared" : "two", "B"),
    ]);
    const scene = await prepare(source);
    assert.equal(scene.batches.some((batch) => batch.key.geometryType === 5), repeated);
    const viewer = await mountPrepared(scene);
    const rows = descriptors(viewer);
    assert.deepEqual(rows.filter(({ layer }) => layer === "A").map(({ color }) => color), [COLORS.A]);
    assert.deepEqual(rows.filter(({ layer }) => layer === "B").map(({ color }) => color), [COLORS.B]);
    const cColors = rows.filter(({ layer }) => layer === "C").map(({ color }) => color).sort();
    assert.deepEqual(cColors, [COLORS.C, COLORS.C, COLORS.B, 0xffff00].sort());
    viewer.ShowLayer("A", false);
    viewer.ShowLayer("C", false);
    viewer.ShowLayer("C", true);
    assert.ok(rows.filter(({ gates }) => gates.includes("A")).every(({ object }) => !object.visible));
    assert.ok(rows.filter(({ gates }) => !gates.includes("A")).every(({ object }) => object.visible));
    viewer.ShowLayer("A", true);
    assert.ok(rows.every(({ object }) => object.visible));
    assert.deepEqual(Array.from(viewer.GetLayers(true)).map(({ name }) => name).sort(), ["A", "B", "C"]);
    for (const { object } of rows) {
      let disposals = 0;
      object.geometry.addEventListener("dispose", () => disposals++);
      for (const layer of viewer.layers.values()) {
        if (layer.objects.includes(object)) layer.Dispose();
      }
      assert.equal(disposals, 1, "several visibility gates do not duplicate geometry ownership");
      break;
    }
  });

  test(`${mode}: nested siblings retain their independent ancestor gates and colors`, async () => {
    const inner = block("inner", leaves);
    const siblings = [
      insert("inner", "B", { position: { x: 10, y: 20 }, colorIndex: 2, color: 0xffff00 }),
      insert("inner", "D", { position: { x: 20, y: 20 } }),
    ];
    const definitions = { inner, outer: block("outer", siblings) };
    const roots = [insert("outer", "A")];
    if (repeated) roots.push(insert("outer", "A", { position: { x: 510000, y: 600000 } }));
    const scene = await prepare(model(definitions, roots));
    assert.equal(scene.batches.some((batch) => batch.key.geometryType === 5 && batch.key.blockName === "outer"), repeated);
    const viewer = await mountPrepared(scene);
    const rows = descriptors(viewer);
    assert.ok(rows.some(({ layer, gates, color }) => layer === "B" && gates.join() === "A,B" && color === COLORS.B));
    assert.ok(rows.some(({ layer, gates, color }) => layer === "C" && gates.join() === "A,B,C" && color === 0xffff00));
    assert.ok(rows.some(({ layer, gates, color }) => layer === "C" && gates.join() === "A,C,D" && color === COLORS.C));
    viewer.ShowLayer("B", false);
    assert.ok(rows.filter(({ gates }) => gates.includes("B")).every(({ object }) => !object.visible));
    assert.ok(rows.filter(({ gates }) => gates.includes("D")).every(({ object }) => object.visible));
    viewer.ShowLayer("A", false);
    viewer.ShowLayer("B", true);
    assert.ok(rows.every(({ object }) => !object.visible));
  });

  test(`${mode}: nested BYBLOCK can inherit the parent layer without using its explicit leaf layer`, async () => {
    const innerLeaves = Array.from({ length: repetitions * 3 }, (_, y) => line("C", 0, y));
    const definitions = {
      inner: block("inner", innerLeaves),
      outer: block("outer", [insert("inner", "0", { position: { x: 10, y: 20 } })]),
    };
    const roots = [insert("outer", "A", { colorIndex: 2, color: 0xffff00 })];
    if (repeated) roots.push(insert("outer", "B"));
    const rows = descriptors(await mountPrepared(await prepare(model(definitions, roots))));
    assert.ok(rows.every(({ layer }) => layer === "C"));
    assert.deepEqual(rows.map(({ color }) => color).sort(), (repeated ? [COLORS.A, COLORS.B] : [COLORS.A]).sort());
  });
}

for (const repeated of [false, true]) test(`${repeated ? "instanced" : "flattened"}: basepoint, rotation, nonuniform scale and nested transforms retain source coordinates`, async () => {
  const repetitions = repeated ? 540 : 1;
  const source = model({
    inner: block("inner", Array.from({ length: repetitions }, () => line("C"))),
    outer: block("outer", [insert("inner", "B", {
      position: { x: 20, y: 30 }, xScale: 2, yScale: 3, rotation: 90,
    })]),
  }, [insert("outer", "A", { xScale: 4, yScale: 2, rotation: 90 })]);
  if (repeated) source.entities.push(insert("outer", "A", {
    position: { x: 510000, y: 600000 }, xScale: 4, yScale: 2, rotation: 90,
  }));
  const scene = await prepare(source);
  const viewer = await mountPrepared(scene);
  const world = viewer.scene.children.flatMap((object) => {
    const positions = object.geometry.getAttribute("position");
    const row0 = object.geometry.getAttribute("instanceTransform0");
    const row1 = object.geometry.getAttribute("instanceTransform1");
    return Array.from({ length: row0?.count ?? 1 }, (_, instance) =>
      Array.from({ length: positions.count }, (_, i) => {
        const x = positions.getX(i), y = positions.getY(i);
        return row0 ? [
          row0.getX(instance) * x + row0.getY(instance) * y + row0.getZ(instance) + scene.origin.x,
          row1.getX(instance) * x + row1.getY(instance) * y + row1.getZ(instance) + scene.origin.y,
        ] : [x + scene.origin.x, y + scene.origin.y];
      })).flat();
  });
  assert.equal(world.length, repetitions * 2 * (repeated ? 2 : 1));
  const uniqueWorld = [...new Set(world.map((point) => JSON.stringify(point)))].sort();
  const expected = [[499972, 600040], [499980, 600040]];
  if (repeated) expected.push([509972, 600040], [509980, 600040]);
  assert.deepEqual(uniqueWorld, expected.map((point) => JSON.stringify(point)).sort());
});

test("native hidden/frozen policy is unchanged, including filtered geometry", async () => {
  const source = model({}, [line("C", 256, 0, { hidden: true }), line("B"), line("A")]);
  source.tables.layer.layers.B.frozen = true;
  const scene = await prepare(source);
  const rows = descriptors(await mountPrepared(scene));
  assert.deepEqual(rows.map(({ layer }) => layer), ["A"]);
  assert.equal(scene.layers.some(({ name }) => name === "B"), false);
});

for (const repeated of [false, true]) test(`${repeated ? "instanced" : "flattened"}: indexed lines and solid faces share the same layer contract`, async () => {
  const vertices = [{ x: 10, y: 20 }, { x: 12, y: 20 }, { x: 12, y: 22 }, { x: 10, y: 22 }];
  const content = Array.from({ length: repeated ? 180 : 1 }, () => [
    { type: "LWPOLYLINE", layer: "C", colorIndex: 256, shape: true, vertices },
    { type: "SOLID", layer: "0", colorIndex: 256, points: vertices.slice(0, 3) },
  ]).flat();
  const roots = [insert("outer", "A")];
  if (repeated) roots.push(insert("outer", "A", { position: { x: 510000, y: 600000 } }));
  const source = model({
    inner: block("inner", content),
    outer: block("outer", [insert("inner", "B", { position: { x: 10, y: 20 } })]),
  }, roots);
  const scene = await prepare(source);
  const viewer = await mountPrepared(scene);
  const rows = descriptors(viewer);
  assert.deepEqual(rows.map(({ layer }) => layer).sort(), ["B", "C"]);
  assert.ok(rows.every(({ object }) => object.geometry.index !== null));
  assert.ok(rows.every(({ gates }) => gates.includes("A") && gates.includes("B")));
  viewer.ShowLayer("B", false);
  assert.ok(rows.every(({ object }) => !object.visible));
  viewer.ShowLayer("C", true);
  assert.ok(rows.every(({ object }) => !object.visible));
});

test("bulk layer updates apply every gate before recomputing each object and render once", async () => {
  const source = model({ inner: block("inner", [line("C")]), outer: block("outer", [
    insert("inner", "B", { position: { x: 10, y: 20 } }),
    insert("inner", "D", { position: { x: 20, y: 20 } }),
  ]) }, [insert("outer", "A")]);
  const viewer = await mountPrepared(await prepare(source));
  const rows = descriptors(viewer);
  let renders = 0, writes = 0;
  viewer.Render = () => renders++;
  for (const { object } of rows) {
    let visible = object.visible;
    Object.defineProperty(object, "visible", {
      get: () => visible, set(value) { visible = value; writes++; },
    });
  }
  viewer.ShowLayers({ A: false, B: true, C: true, D: false, missing: true });
  assert.equal(renders, 1);
  assert.equal(writes, rows.length, "objects shared by several gates are visited once");
  assert.ok(rows.every(({ object }) => !object.visible));
  viewer.ShowLayers({ A: true, D: true, B: false });
  assert.equal(renders, 2);
  assert.equal(writes, rows.length * 2);
  assert.ok(rows.filter(({ gates }) => gates.includes("B")).every(({ object }) => !object.visible));
  assert.ok(rows.filter(({ gates }) => gates.includes("D")).every(({ object }) => object.visible));
});
